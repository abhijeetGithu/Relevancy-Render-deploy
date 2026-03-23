#!/usr/bin/env python3
"""
LLM-based title match scoring for Vespa search results.

Reads a CSV containing columns:
  - Query_ID, Generated_Query, Original_Title, Original_URL,
  - Vespa_Result_Rank, Vespa_Result_Title, Vespa_Result_URL,
  - Vespa_Result_Description, Vespa_Search_Score, Title_Match (optional)

For each query group (Query_ID), asks an OpenAI model to rate how well each
Vespa_Result_Title matches the Generated_Query on a 0-10 integer scale.

Outputs a new CSV with the Title_Match column filled (0-10).

Model default: gpt-4o-mini (you can pass --model to change; e.g., "gpt-4o-nano" if available).
Requires OPENAI_API_KEY environment variable.
original title to vespa result
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
from typing import Dict, List, Any

import pandas as pd

try:
	import openai  # type: ignore
except Exception as e:
	openai = None


def require_openai():
	if openai is None:
		raise RuntimeError("The 'openai' package is not installed. Please 'pip install openai'.")
	api_key = os.getenv("OPENAI_API_KEY")
	if not api_key:
		raise RuntimeError("OPENAI_API_KEY environment variable not set.")
	try:
		client = openai.OpenAI(api_key=api_key)
	except Exception:
		# Some versions use from openai import OpenAI
		from openai import OpenAI  # type: ignore
		client = OpenAI(api_key=api_key)
	return client


PROMPT_TEMPLATE = (
		"""
You are a strict evaluator of search result title relevance.

Task:
Given a user search query and up to 10 candidate RESULT TITLES, assign an integer score from 0 to 10 for EACH title, where:

- 10: Perfect match. Title directly answers or exactly matches the user's intent.
- 8-9: Strong match. Very relevant; highly likely what the user wants.
- 5-7: Partial match. Related topic but may be broader/narrower or missing key intent.
- 1-4: Weak match. Tangentially related or only shares keywords without matching intent.
- 0: Unrelated, off-topic, spam, or irrelevant.

Important constraints:
- Only consider the Generated Query and the Result Title. Ignore any descriptions, URLs, or other fields.
- Focus on intent alignment (device, product, action, problem context) inferred from the query and title text.
- Be conservative: don't over-score vague matches.
- Output integers only (no decimals), within 0..10 inclusive.

Return ONLY a compact JSON object with the following shape (no extra text):
{{
	"scores": [
		{{ "rank": <int>, "score": <int> }},
		... one for each candidate ...
	]
}}

User Query: {query}

Candidates (rank, title):
{candidates}
"""
).strip()


def _safe_int_rank(val) -> int | None:
	"""Convert rank to int; return None if missing/NaN/invalid."""
	try:
		if val is None:
			return None
		# pandas may have NaN floats
		if isinstance(val, float) and pd.isna(val):
			return None
		s = str(val).strip()
		if s == "" or s.lower() == "nan":
			return None
		return int(float(s))
	except Exception:
		return None


def build_candidates_block(rows: List[Dict[str, Any]]) -> str:
	lines = []
	for r in rows:
		rank = _safe_int_rank(r.get("Vespa_Result_Rank"))
		if rank is None:
			continue
		title = str(r.get("Vespa_Result_Title", "") or "").strip()
		lines.append(f"- rank={rank} | title={title}")
	return "\n".join(lines)


def call_model(client, model: str, query: str, group_rows: List[Dict[str, Any]], max_retries: int = 3, backoff: float = 2.0) -> Dict[int, int]:
	prompt = PROMPT_TEMPLATE.format(query=query, candidates=build_candidates_block(group_rows))

	for attempt in range(1, max_retries + 1):
		try:
			resp = client.chat.completions.create(
				model=model,
				messages=[
					{"role": "system", "content": "Respond ONLY with valid JSON per instructions."},
					{"role": "user", "content": prompt},
				],
				temperature=0.0,
				max_tokens=300,
				response_format={"type": "json_object"},
			)

			content = resp.choices[0].message.content.strip()
			data = json.loads(content)
			scores_list = data.get("scores", [])
			result = {}
			for item in scores_list:
				try:
					r = int(item.get("rank"))
					s = int(item.get("score"))
					# clamp
					s = max(0, min(10, s))
					result[r] = s
				except Exception:
					continue
			if result:
				return result
			else:
				raise ValueError("Empty or invalid scores in model response")

		except Exception as e:
			if attempt == max_retries:
				print(f"❌ Model call failed after {max_retries} attempts: {e}")
				return {}
			sleep_for = backoff ** (attempt - 1)
			print(f"⚠️ Model call error (attempt {attempt}/{max_retries}): {e} — retrying in {sleep_for:.1f}s...")
			time.sleep(sleep_for)

	return {}


def score_titles(input_csv: str, output_csv: str, model: str = "gpt-4.1-nano") -> None:
	print(f"📄 Loading: {input_csv}")
	try:
		df = pd.read_csv(input_csv)
	except Exception as e:
		print(f"❌ Failed to read CSV: {e}")
		sys.exit(1)

	required_cols = [
		"Query_ID",
		"Generated_Query",
		"Vespa_Result_Rank",
		"Vespa_Result_Title",
	]
	for col in required_cols:
		if col not in df.columns:
			print(f"❌ Required column missing: {col}")
			sys.exit(1)

	if "Title_Match" not in df.columns:
		df["Title_Match"] = ""

	client = require_openai()
	print(f"🤖 Using model: {model}")

	# Ensure proper ordering inside groups
	df["Vespa_Result_Rank"] = pd.to_numeric(df["Vespa_Result_Rank"], errors="coerce")
	df.sort_values(["Query_ID", "Vespa_Result_Rank"], inplace=True)

	groups = df.groupby(["Query_ID", "Generated_Query"], dropna=False)
	total_groups = len(groups)
	print(f"🔍 Scoring {total_groups} query groups...")

	processed = 0
	for (qid, query), group in groups:
		processed += 1
		rows = group.to_dict(orient="records")
		# Limit to top 10 per spec; use only valid numeric ranks 1..10
		rows_top10 = []
		for r in rows:
			rank = _safe_int_rank(r.get("Vespa_Result_Rank"))
			if rank is not None and 1 <= rank <= 10:
				rows_top10.append(r)
		# If none valid, skip scoring for this group
		if not rows_top10:
			print("  ⚠️  No valid ranked candidates (1..10) — skipping LLM scoring for this query.")
			continue

		print(f"\n📝 Query_ID={qid} | '{str(query)[:80]}' | {len(rows_top10)} candidates")
		rank_to_score = call_model(client, model, str(query), rows_top10)

		# Apply scores back to df
		for idx, r in group.iterrows():
			rank = _safe_int_rank(r.get("Vespa_Result_Rank"))
			if rank is None:
				continue
			score = rank_to_score.get(rank)
			# Assign model score if present, otherwise 0 fallback
			df.at[idx, "Title_Match"] = int(score) if score is not None else 0

		if processed % 25 == 0:
			print(f"💾 Progress checkpoint: {processed}/{total_groups} groups — saving interim output...")
			try:
				df.to_csv(output_csv, index=False)
			except Exception as e:
				print(f"⚠️ Failed interim save: {e}")

	print(f"\n💾 Saving scored results to: {output_csv}")
	try:
		df.to_csv(output_csv, index=False)
		print("✅ Done")
	except Exception as e:
		print(f"❌ Failed to write output CSV: {e}")
		sys.exit(1)


def parse_args(argv: List[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="LLM title-match scoring for Vespa results")
	default_input = os.path.join(
		os.path.dirname(__file__),
		"search_results-vespa-title-match .csv",  # Note: filename includes a space before .csv per provided file
	)
	default_output = os.path.join(
		os.path.dirname(__file__),
		"vespa-title-match_scored.csv",
	)
	parser.add_argument("--input", dest="input_csv", default=default_input, help="Path to input CSV")
	parser.add_argument("--output", dest="output_csv", default=default_output, help="Path to output CSV")
	parser.add_argument("--model", dest="model", default="gpt-4o-mini", help="OpenAI model (e.g., gpt-4o-nano or gpt-4o-mini)")
	return parser.parse_args(argv)


if __name__ == "__main__":
	args = parse_args(sys.argv[1:])
	score_titles(args.input_csv, args.output_csv, model=args.model)

