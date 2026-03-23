import asyncio
from typing import Dict, List, Optional, Tuple

import httpx

from services.fetchers import fetch_results
from su_search_test import MAX_RESULTS, SU_CURL, TITLE_PATH, URL_PATH, _unique_queries


LinkResult = Tuple[Optional[int], str, Optional[str]]


async def _check_link(client: httpx.AsyncClient, url: str) -> LinkResult:
    try:
        head_resp = await client.head(url, follow_redirects=True)
        status = head_resp.status_code
        final_url = str(head_resp.url)
        if status >= 400 or status in (403, 405):
            get_resp = await client.get(url, follow_redirects=True)
            status = get_resp.status_code
            final_url = str(get_resp.url)
        return status, final_url, None
    except Exception as exc:
        return None, url, str(exc)


async def run(file_path: str) -> None:
    with open(file_path, "r", encoding="utf-8") as f:
        queries = _unique_queries(f.readlines())

    cache: Dict[str, LinkResult] = {}
    ok_count = 0
    bad_count = 0

    async with httpx.AsyncClient(timeout=20.0) as client:
        for idx, query in enumerate(queries, start=1):
            print(f"\n[{idx}/{len(queries)}] 🔍 {query}")
            results = await fetch_results(
                query,
                SU_CURL,
                TITLE_PATH,
                URL_PATH,
                MAX_RESULTS,
            )
            urls: List[str] = [item.get("url", "") for item in results if item.get("url")]
            for rank, url in enumerate(urls, start=1):
                if url in cache:
                    status, final_url, error = cache[url]
                else:
                    status, final_url, error = await _check_link(client, url)
                    cache[url] = (status, final_url, error)

                if status is not None and 200 <= status < 400:
                    ok_count += 1
                    status_label = f"{status} OK"
                elif status is not None:
                    bad_count += 1
                    status_label = f"{status} FAIL"
                else:
                    bad_count += 1
                    status_label = f"ERROR: {error}"

                print(f"{rank}. {status_label} -> {final_url}")

    print(f"\nDone. OK: {ok_count}, Fail/Error: {bad_count}")


if __name__ == "__main__":
    asyncio.run(run("queries.txt"))





