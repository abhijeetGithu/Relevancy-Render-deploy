import asyncio
import json
from typing import List, Optional

import httpx

from services.fetchers import _build_payload, _parse_curl, _replace_query_tokens, fetch_results


SU_CURL = r"""curl 'https://rs122503s.searchunify.com/search/searchResultByPost' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en-US;q=0.9,en;q=0.8' \
  -H 'content-type: application/json' \
  -H 'origin: https://d2ui5v4xff83ed.cloudfront.net' \
  -H 'priority: u=1, i' \
  -H 'referer: https://d2ui5v4xff83ed.cloudfront.net/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "macOS"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: cross-site' \
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
  --data-raw '{"langAttr":"en","react":1,"isRecommendationsWidget":false,"searchString":"","from":0,"sortby":"_score","orderBy":"desc","pageNo":1,"aggregations":[],"clonedAggregations":[],"uid":"e4b68005-f82d-11f0-8a3d-0242ac12000f","resultsPerPage":50,"exactPhrase":"","withOneOrMore":"","withoutTheWords":"","pageSize":"50","sid":"1770197522653658","language":"en","mergeSources":true,"versionResults":true,"suCaseCreate":false,"visitedtitle":"","paginationClicked":false,"email":"","storeContext":true,"searchUid":"24323e72-b71e-4bda-8f95-0f4bebaf3560","accessToken":"9ad5ad4164aea64521fd3c00a19b76c8","getAutoTunedResult":true,"getSimilarSearches":true,"smartFacets":true,"showMoreSummary":false,"minSummaryLength":100,"showContentTag":true,"pagingAggregation":[]}'"""

TITLE_PATH = "$.result.hits[*].highlight.TitleToDisplayString"
URL_PATH = "$.result.hits[*].href"
MAX_RESULTS = 5
DEBUG_ON_EMPTY = True
DEBUG_DUMP_PATH = "su_debug_response.json"


def _unique_queries(lines: List[str]) -> List[str]:
    seen = set()
    unique = []
    for line in lines:
        query = line.strip()
        if not query or query in seen:
            continue
        seen.add(query)
        unique.append(query)
    return unique


async def _dump_raw_response(query: str) -> None:
    url, headers, data, method = _parse_curl(SU_CURL)
    url = _replace_query_tokens(url, query) or url
    payload, raw_data = _build_payload(data, query)

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            if payload is not None:
                response = await client.post(url, headers=headers, json=payload)
            else:
                response = await client.post(url, headers=headers, content=raw_data or "")

    try:
        data_json = response.json()
    except ValueError:
        data_json = {"raw_text": response.text}

    with open(DEBUG_DUMP_PATH, "w", encoding="utf-8") as f:
        json.dump(data_json, f, indent=2, ensure_ascii=True)
    print(f"   Raw response saved to {DEBUG_DUMP_PATH}.")


async def run(file_path: str) -> None:
    with open(file_path, "r", encoding="utf-8") as f:
        queries = _unique_queries(f.readlines())

    for idx, query in enumerate(queries, start=1):
        print(f"\n[{idx}/{len(queries)}] 🔍 {query}")
        results = await fetch_results(
            query,
            SU_CURL,
            TITLE_PATH,
            URL_PATH,
            MAX_RESULTS,
        )
        if DEBUG_ON_EMPTY and all(not item.get("title") for item in results):
            print("   No titles returned. Dumping raw response for debugging...")
            await _dump_raw_response(query)
        for rank, item in enumerate(results, start=1):
            title = item.get("title", "")
            url = item.get("url", "")
            print(f"{rank}. {title}")
            if url:
                print(f"   {url}")


if __name__ == "__main__":
    asyncio.run(run("queries.txt"))

