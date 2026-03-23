import json
import shlex
import logging
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

def _parse_curl(curl_cmd: str) -> Tuple[str, Dict[str, str], Optional[str], str]:
    tokens = shlex.split(curl_cmd)
    if not tokens:
        logger.error("Empty cURL command provided.")
        raise ValueError("Empty cURL command.")

    method = "POST"
    headers: Dict[str, str] = {}
    data = None
    url = ""

    it = iter(tokens)
    for token in it:
        if token == "curl":
            continue
        if token in ("-X", "--request"):
            method = next(it, method).upper()
            continue
        if token in ("-H", "--header"):
            header = next(it, "")
            if ":" in header:
                name, value = header.split(":", 1)
                headers[name.strip()] = value.strip()
            continue
        if token in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii"):
            data = next(it, "")
            continue
        if token.startswith("http://") or token.startswith("https://"):
            url = token

    if not url:
        logger.error("Could not parse URL from cURL command.")
        raise ValueError("Could not parse URL from cURL command.")

    return url, headers, data, method


def _replace_query_tokens(value: Optional[str], query: str) -> Optional[str]:
    if not value:
        return value
    return value.replace("{{query}}", query).replace("{query}", query)


def _build_payload(data: Optional[str], query: str) -> Tuple[Optional[dict], Optional[str]]:
    if not data:
        return None, None
    data = _replace_query_tokens(data, query)
    try:
        payload = json.loads(data)
        if isinstance(payload, dict):
            if "searchString" in payload:
                payload["searchString"] = query
            elif "query" in payload:
                payload["query"] = query
        return payload, None
    except json.JSONDecodeError:
        return None, data


def _get_values_by_simple_path(data: object, path: str) -> List[object]:
    if not path:
        return []
    path = path.strip()
    if path.startswith("$."):
        path = path[2:]
    segments = [seg for seg in path.split(".") if seg]
    current: List[object] = [data]
    for seg in segments:
        next_items: List[object] = []
        is_list = seg.endswith("[*]")
        key = seg[:-3] if is_list else seg
        for item in current:
            if isinstance(item, dict) and key in item:
                value = item[key]
            else:
                value = None
            if value is None:
                continue
            if is_list:
                if isinstance(value, list):
                    next_items.extend(value)
                else:
                    next_items.append(value)
            else:
                next_items.append(value)
        current = next_items
    return current


def _jsonpath_values(data: object, path: str) -> List[object]:
    if not path:
        return []
    try:
        from jsonpath_ng import parse as jsonpath_parse  # type: ignore

        expr = jsonpath_parse(path)
        return [match.value for match in expr.find(data)]
    except Exception:
        return _get_values_by_simple_path(data, path)


async def fetch_results(
    query: str,
    curl_cmd: str,
    title_path: str,
    url_path: str,
    max_results: int,
) -> List[Dict]:
    url, headers, data, method = _parse_curl(curl_cmd)
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

    if response.status_code != 200:
        logger.error(f"Request failed: HTTP {response.status_code} - {response.text}")
        raise ValueError(f"Request failed: HTTP {response.status_code}")

    try:
        data_json = response.json()
    except ValueError as exc:
        logger.error(f"Response is not JSON: {exc}")
        raise ValueError(f"Response is not JSON: {exc}") from exc

    titles = _jsonpath_values(data_json, title_path)
    urls = _jsonpath_values(data_json, url_path) if url_path else []

    def _normalize_value(value: object) -> str:
        if isinstance(value, (list, tuple)):
            if not value:
                return ""
            return str(value[0])
        if value is None:
            return ""
        return str(value)

    results: List[Dict] = []
    for idx in range(max_results):
        title = titles[idx] if idx < len(titles) else ""
        url_val = urls[idx] if idx < len(urls) else ""
        results.append(
            {
                "rank": idx + 1,
                "title": _normalize_value(title),
                "url": _normalize_value(url_val),
            }
        )
    return results

