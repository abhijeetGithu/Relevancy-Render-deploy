import json
import os
import logging
from typing import Dict, List

import httpx

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Strict per-query judge: used by DQE LLM Comparison when no custom prompt is supplied.
DEFAULT_LLM_PROMPT = (
    "You are a strict search quality auditor. The user message is JSON with:\n"
    "- query: the user's search string\n"
    "- expected_document_title: gold-standard title if the evaluation has ground truth (may be empty string)\n"
    "- results: ranked list of objects {rank, title} from one search engine (up to 10 items)\n\n"
    "Task: score how well this ranked list serves the query. Be critical; do not inflate scores.\n\n"
    "Scoring rubric (integer 0-10 only):\n"
    "- 9-10: Top results are directly on-topic. If expected_document_title is non-empty, a correct or clearly "
    "equivalent match must appear in ranks 1-3.\n"
    "- 7-8: Mostly relevant; best answer may be rank 4-6 or tangential but useful.\n"
    "- 4-6: Partial relevance; several top titles are generic, off-intent, or weakly related.\n"
    "- 1-3: Largely irrelevant, wrong intent, or noisy/spam-like titles in top positions.\n"
    "- 0: No meaningful relation to the query.\n\n"
    "Rules:\n"
    "1. If expected_document_title is empty, judge only query intent vs result titles (query-only mode).\n"
    "2. Penalize duplicate or near-duplicate titles, obvious spam, and wrong product/domain in top ranks.\n"
    "3. Ranking quality matters: the correct document at rank 8 scores lower than at rank 2 with the same set.\n"
    "4. Reference specific ranks and titles briefly in your reason.\n\n"
    "Respond with ONLY valid JSON: {\"score\": <int 0-10>, \"reason\": \"<2-5 sentences>\"}"
)

DEFAULT_FINAL_ANALYSIS_PROMPT = (
    "You are a senior search relevance lead writing an internal QA report.\n"
    "You receive per-query scores and short LLM reasons for SearchUnify (SU) and Google result lists.\n"
    "Be strict, specific, and actionable. Avoid generic advice (e.g. 'improve search quality').\n"
    "Return ONLY valid JSON matching the schema requested in the user message."
)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


# ---------------------------------------------------------------------------
# OpenAI helpers
# ---------------------------------------------------------------------------

async def _call_openai(messages: list, api_key: str, model: str,
                       temperature: float = 0, json_mode: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers, json=body,
        )
    if resp.status_code != 200:
        logger.error(f"OpenAI API error: HTTP {resp.status_code} - {resp.text}")
        return {}
    data = resp.json()
    return data


def _extract_openai_text(data: dict) -> str:
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

async def _call_gemini(user_text: str, api_key: str, model: str,
                       system_text: str = "", temperature: float = 0,
                       json_mode: bool = False) -> dict:
    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"

    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body)
    if resp.status_code != 200:
        logger.error(f"Gemini API error: HTTP {resp.status_code} - {resp.text}")
        return {}
    return resp.json()


def _extract_gemini_text(data: dict) -> str:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        logger.error(f"Unexpected Gemini response structure: {json.dumps(data)[:500]}")
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def judge_results(
    query: str,
    expected_title: str,
    expected_url: str,
    results: List[Dict],
    api_key: str,
    model: str,
    prompt_template: str,
    provider: str = "openai",
) -> Dict:
    if not api_key:
        return {}

    results_payload = []
    for res in results[:10]:
        results_payload.append({
            "rank": res.get("rank"),
            "title": res.get("title"),
        })

    payload = {
        "query": query,
        "expected_document_title": expected_title or "",
        "results": results_payload,
    }
    user_content = json.dumps(payload)

    if provider == "gemini":
        data = await _call_gemini(
            user_text=user_content,
            api_key=api_key,
            model=model,
            system_text=prompt_template,
            temperature=0,
            json_mode=True,
        )
        content = _extract_gemini_text(data)
    else:
        messages = [
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": user_content},
        ]
        data = await _call_openai(messages, api_key, model, temperature=0, json_mode=True)
        content = _extract_openai_text(data)

    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse LLM response JSON: {exc}. Content: {content}")
        return {}
    return parsed


async def generate_overall_analysis(
    judgments: List[str],
    api_key: str,
    model: str,
    provider: str = "openai",
) -> str:
    if not api_key or not judgments:
        return ""

    combined_reasons = []
    for j_str in judgments:
        try:
            j = json.loads(j_str)
            source = j.get("source", "Unknown")
            query = j.get("query", "")
            judgment = j.get("judgment", {})
            score = judgment.get("score", "N/A")
            reason = judgment.get("reason", "")
            combined_reasons.append(f"Source: {source} | Query: {query} | Score: {score} | Reason: {reason}")
        except Exception:
            continue

    context_text = "\n".join(combined_reasons[:50])

    prompt = (
        "You are a Senior Search Engineer. Analyze the following search judgment logs to diagnose specific technical failures.\n"
        "Do NOT provide generic advice like 'improve content' or 'add feedback loops'.\n"
        "Analyze the gap between the Query and the Results to provide precise technical recommendations.\n\n"
        "Focus on:\n"
        "1. **Retrieval Analysis**: \n"
        "   - Lexical Gap: Are keywords missing? (Suggest Synonyms/Stemming/Analyzer changes)\n"
        "   - Semantic Drift: Are results semantically related but factually wrong? (Vector search issue -> suggest Hybrid Search weighting, chunking strategy)\n"
        "   - Ranking: Is the correct result found but ranked low? (Suggest Title boosting, field weights)\n\n"
        "2. **Specific Failure Patterns**: Group failures by technical root cause (e.g., 'Acronyms not recognized', 'Intent mismatch').\n\n"
        "3. **Actionable Recommendations**: Provide 3-5 concrete engineering changes. Examples: 'Increase BM25 weight', 'Add synonym mapping for X', 'Implement re-ranking'.\n\n"
        f"Logs:\n{context_text}"
    )

    try:
        if provider == "gemini":
            data = await _call_gemini(
                user_text=prompt,
                api_key=api_key,
                model=model,
                system_text="You are a helpful analyst.",
                temperature=0.3,
            )
            content = _extract_gemini_text(data)
        else:
            messages = [
                {"role": "system", "content": "You are a helpful analyst."},
                {"role": "user", "content": prompt},
            ]
            data = await _call_openai(messages, api_key, model, temperature=0.3)
            content = _extract_openai_text(data)

        return content or "Could not generate overall analysis."
    except Exception as exc:
        logger.error(f"Error generating overall analysis: {exc}")
        return "Error generating analysis."


def _empty_final_analysis(message: str) -> Dict:
    return {
        "executive_summary": message,
        "performed_well": [],
        "performed_poorly": [],
        "searchunify_vs_google": "",
        "recommendations": [],
    }


async def generate_dqe_final_analysis(
    compact_rows: List[Dict],
    has_ground_truth: bool,
    api_key: str,
    model: str,
    provider: str = "openai",
) -> Dict:
    """
    Batch-level comparison after all per-query SU/Google judgments.
    compact_rows: dicts with query, su_score, google_score, su_reason, google_reason (optional recall fields).
    """
    if not api_key or not compact_rows:
        return _empty_final_analysis("Not enough data to generate final analysis.")

    max_q = 45
    rows = compact_rows[:max_q]
    if len(compact_rows) > max_q:
        note = f"\n\n(Note: analysis covers first {max_q} of {len(compact_rows)} queries.)"
    else:
        note = ""

    mode_line = (
        "Mode: Ground truth evaluation — recall ranks may be present; prioritize alignment with expected documents."
        if has_ground_truth
        else "Mode: Query-only — no gold document; judge quality from scores and reasons only."
    )

    lines = []
    for i, r in enumerate(rows, 1):
        q = str(r.get("query") or "")[:400]
        lines.append(
            f"--- Q{i} ---\nquery: {q}\n"
            f"SU_LLM_score: {r.get('su_score')!s} | Google_LLM_score: {r.get('google_score')!s}\n"
            f"SU_recall_rank: {r.get('su_recall')!s} | Google_recall_rank: {r.get('google_recall')!s}\n"
            f"SU_reason: {str(r.get('su_reason') or '')[:380]}\n"
            f"Google_reason: {str(r.get('google_reason') or '')[:380]}"
        )
    per_query_block = "\n\n".join(lines)

    schema_instruction = (
        "Return ONLY valid JSON with exactly these keys:\n"
        '{\n'
        '  "executive_summary": "3-5 sentences on overall batch quality and SU vs Google.",\n'
        '  "performed_well": ["Short bullet: which queries or patterns did well and why"],\n'
        '  "performed_poorly": ["Short bullet: which queries or patterns failed and why"],\n'
        '  "searchunify_vs_google": "2-5 sentences: systematic strengths/weaknesses of SU vs Google on this batch.",\n'
        '  "recommendations": ["Concrete engineering or tuning action 1", "action 2", ... at least 3 items]\n'
        "}\n"
        "Use empty arrays only if truly nothing applies. Be specific (synonyms, ranking, indexing, query understanding)."
    )

    user_text = f"{mode_line}{note}\n\nPer-query data:\n{per_query_block}\n\n{schema_instruction}"

    content = ""
    try:
        if provider == "gemini":
            data = await _call_gemini(
                user_text=user_text,
                api_key=api_key,
                model=model,
                system_text=DEFAULT_FINAL_ANALYSIS_PROMPT,
                temperature=0.2,
                json_mode=True,
            )
            content = _extract_gemini_text(data)
        else:
            messages = [
                {"role": "system", "content": DEFAULT_FINAL_ANALYSIS_PROMPT},
                {"role": "user", "content": user_text},
            ]
            data = await _call_openai(messages, api_key, model, temperature=0.2, json_mode=True)
            content = _extract_openai_text(data)

        if not content:
            return _empty_final_analysis("LLM returned empty final analysis.")
        parsed = json.loads(content)
        if not isinstance(parsed.get("executive_summary"), str):
            parsed["executive_summary"] = str(parsed.get("executive_summary") or "")
        if not isinstance(parsed.get("searchunify_vs_google"), str):
            parsed["searchunify_vs_google"] = str(parsed.get("searchunify_vs_google") or "")
        for key in ("performed_well", "performed_poorly", "recommendations"):
            if not isinstance(parsed.get(key), list):
                parsed[key] = []
        return parsed
    except json.JSONDecodeError as exc:
        logger.error("Final analysis JSON parse error: %s content=%s", exc, (content or "")[:500])
        return _empty_final_analysis("Could not parse final analysis JSON from the model.")
    except Exception as exc:
        logger.error("Error generating DQE final analysis: %s", exc)
        return _empty_final_analysis(f"Error generating final analysis: {exc}")
