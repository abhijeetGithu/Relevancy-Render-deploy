from typing import Dict, List
import logging
import httpx

logger = logging.getLogger(__name__)

async def fetch_google_top10(
    query: str,
    api_key: str,
    cse_id: str,
    sites: List[str],
    result_count: int = 10,
) -> List[Dict]:
    if not api_key or not cse_id:
        logger.error("Google API key or CSE ID missing.")
        raise ValueError("Google API key and CSE ID are required.")

    results: List[Dict] = []
    base_url = "https://www.googleapis.com/customsearch/v1"
    query_part = query
    use_site_search = len(sites) == 1
    search_query = query_part
    single_site = None

    if sites:
        if use_site_search:
            raw = sites[0].strip().replace("http://", "").replace("https://", "")
            single_site = raw.split("/")[0]
        else:
            site_restriction = " OR ".join([f"site:{s}" for s in sites])
            search_query = f"{site_restriction} {query_part}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        params = {
            "key": api_key,
            "q": search_query,
            "num": min(10, result_count),
            "start": 1,
            "cx": cse_id,
        }
        if single_site:
            params["siteSearch"] = single_site
            params["siteSearchFilter"] = "i"

        response = await client.get(base_url, params=params)
        if response.status_code != 200:
            logger.error(f"Google API request failed: HTTP {response.status_code} - {response.text}")
            raise ValueError(f"Google API request failed: HTTP {response.status_code}")
        data = response.json()
        for item in data.get("items", [])[:result_count]:
            results.append(
                {
                    "rank": len(results) + 1,
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                }
            )

    while len(results) < result_count:
        results.append({"rank": len(results) + 1, "title": "", "url": ""})
    return results

