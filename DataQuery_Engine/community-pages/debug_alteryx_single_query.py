#!/usr/bin/env python3
"""Debug Alteryx SearchUnify results for a single query.

Why this exists
--------------
`run_alteryx_search_evaluation.py` loops over a CSV and writes output. When the
on-CSV results (what you see in the browser) don't match what the script
returns, it's hard to pinpoint the delta.

This script runs ONE query and prints the Top N results to the terminal,
optionally dumping the effective request payload fields that commonly change
ranking (sortby/orderBy/aggregations/mergeSources/versionResults/etc.).

Usage
-----
python3 community-pages/debug_alteryx_single_query.py \
  --query "Alteryx One one-time passcode not received setup" \
  --top 10 \
    --curl community-pages/curl_alteryx_run.json

Tip: If you want to reproduce the browser exactly, paste the browser curl into
`curl_frontend.py` and ensure `curl_alteryx.json` matches (especially
`aggregations`, `sortby`, `orderBy`, and `mergeSources`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

import alteryx_search_client as client  # noqa: E402


def _load_template(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pretty(v: Any, max_len: int = 240) -> str:
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _print_request_summary(tpl: Dict[str, Any], query: str, top: int) -> None:
    body = tpl.get("body") or {}
    headers = tpl.get("headers") or {}

    print("\n=== Request summary (from curl template) ===")
    print(f"URL: {tpl.get('url','')}")
    print(f"Top: {top}")
    print(f"Query: {query}")

    # Show only the fields that tend to affect ranking/result set
    interesting_fields = [
        "searchString",
        "from",
        "pageNo",
        "resultsPerPage",
        "pageSize",
        "sortby",
        "orderBy",
        "aggregations",
        "category",
        "language",
        "mergeSources",
        "versionResults",
        "getAutoTunedResult",
        "getSimilarSearches",
        "smartFacets",
    ]

    for k in interesting_fields:
        if k in body:
            print(f"body.{k} = {_pretty(body.get(k))}")

    # A quick hint if your template currently forces an index filter
    aggs = body.get("aggregations")
    if isinstance(aggs, list):
        has_index_agg = any(isinstance(a, dict) and a.get("type") == "_index" for a in aggs)
        if has_index_agg:
            print("NOTE: body.aggregations includes type='_index'. This can restrict results.")

    # Don't print full cookie, just whether it exists
    cookie_present = "Cookie" in headers and bool(headers.get("Cookie"))
    print(f"headers.Cookie present: {cookie_present}")


def _print_docs(docs: List[Dict[str, Any]]) -> None:
    print("\n=== Top results (API order) ===")
    if not docs:
        print("NO_RESULTS")
        return

    for i, d in enumerate(docs, start=1):
        title = (d.get("title") or "").strip().replace("\n", " ")
        url = (d.get("url") or "").strip()
        score = d.get("score")
        rank = d.get("rank", i)
        print(f"{i:>2}. rank={rank} score={score} title={title}")
        print(f"    url={url}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Debug a single Alteryx SearchUnify query")
    parser.add_argument(
        "--query",
        default="Alteryx One one-time passcode not received setup",
        help="Query to send",
    )
    parser.add_argument("--top", type=int, default=10, help="How many results to print")
    parser.add_argument(
        "--curl",
        default=os.path.join(THIS_DIR, "curl_alteryx_run.json"),
        help="Path to curl_alteryx.json template",
    )
    parser.add_argument(
        "--from-offset",
        type=int,
        default=0,
        help="From offset for paging (0 means first page)",
    )
    parser.add_argument(
        "--show-request",
        action="store_true",
        help="Print request summary fields from curl template",
    )

    args = parser.parse_args(argv)

    curl_path = os.path.abspath(args.curl)
    if not os.path.exists(curl_path):
        print(f"❌ curl template not found: {curl_path}")
        return 2

    tpl = _load_template(curl_path)
    if args.show_request:
        _print_request_summary(tpl, args.query, args.top)

    try:
        docs = client.fetch_documents(
            curl_path,
            search_string=args.query,
            results_per_page=args.top,
            from_offset=args.from_offset,
        )
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return 1

    _print_docs(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
