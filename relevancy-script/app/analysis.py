from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx


_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class CurlRequestTemplate:
    method: str
    url: str
    headers: Dict[str, str]
    cookies: Dict[str, str]
    json_body: Optional[Dict[str, Any]]


_CURL_URL_RE = re.compile(r"^curl\s+(?P<quote>['\"])(?P<url>.+?)(?P=quote)")


def _shlex_like_split_multiline(curl_text: str) -> List[str]:
    """Lightweight split for common multi-line curl format using backslashes.

    We intentionally keep it simple (no full shell parsing) since users often
    paste from devtools with '\\' newlines.
    """
    # Remove line-continuation backslashes
    normalized = re.sub(r"\\\s*\n", " ", curl_text).strip()
    # Collapse repeated whitespace
    normalized = re.sub(r"\s+", " ", normalized)

    # Very small tokenizer that respects single/double quotes.
    tokens: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(normalized):
        c = normalized[i]
        if quote:
            if c == quote:
                quote = None
            else:
                buf.append(c)
            i += 1
            continue

        if c in ("'", '"'):
            quote = c
            i += 1
            continue

        if c.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
            continue

        buf.append(c)
        i += 1

    if buf:
        tokens.append("".join(buf))

    return tokens


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def parse_curl_to_template(curl_text: str) -> CurlRequestTemplate:
    """Parse a typical devtools 'Copy as cURL' command.

    Supports:
    - curl 'https://...'
    - -X METHOD
    - -H 'key: value'
    - -b 'a=b; c=d'
    - --data / --data-raw / -d (expects JSON payload)

    Contract: user must include a JSON body field named "searchString".
    We'll replace it per query.
    """

    curl_text = curl_text.strip()
    if not curl_text:
        raise ValueError("cURL command is empty")

    # Extract URL from the first curl '...'
    m = _CURL_URL_RE.search(curl_text)
    if not m:
        raise ValueError("Could not find URL in cURL command. Expected: curl 'https://...' ...")
    url = m.group("url")

    tokens = _shlex_like_split_multiline(curl_text)
    if not tokens or tokens[0] != "curl":
        raise ValueError("cURL command must start with 'curl'")

    method = "POST"  # devtools is usually POST; curl defaults to GET but your sample is POST
    headers: Dict[str, str] = {}
    cookies: Dict[str, str] = {}
    body_text: Optional[str] = None

    i = 1
    while i < len(tokens):
        t = tokens[i]

        if t in ("-X", "--request"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing HTTP method after -X/--request")
            method = tokens[i].upper()
        elif t in ("-H", "--header"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing header value after -H/--header")
            hv = tokens[i]
            if ":" in hv:
                k, v = hv.split(":", 1)
                headers[k.strip()] = v.strip()
        elif t in ("-b", "--cookie"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing cookie string after -b/--cookie")
            cookies.update(parse_cookie_header(tokens[i]))
        elif t in ("--data", "--data-raw", "--data-binary", "-d"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing payload after --data/--data-raw/-d")
            body_text = tokens[i]
            if method == "GET":
                method = "POST"
        elif t.startswith("http://") or t.startswith("https://"):
            # Sometimes URL is not quoted; ignore
            pass

        i += 1

    json_body: Optional[Dict[str, Any]] = None
    if body_text is not None:
        try:
            json_body = json.loads(body_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                "Request body must be valid JSON (from --data-raw). "
                "Tip: ensure it starts with '{' and uses double quotes."
            ) from e

    if json_body is None:
        raise ValueError("cURL must include a JSON body via --data-raw/-d")

    if "searchString" not in json_body:
        raise ValueError("JSON body must include a 'searchString' field to inject the query")

    return CurlRequestTemplate(method=method, url=url, headers=headers, cookies=cookies, json_body=json_body)


def inject_query(template: CurlRequestTemplate, query: str, results_per_page: int = 50) -> CurlRequestTemplate:
    body = dict(template.json_body or {})
    body["searchString"] = query

    # Ensure we request a larger page size.
    # SearchUnify-like payloads use `resultsPerPage` (int) and `pageSize`.
    body["resultsPerPage"] = results_per_page
    body["pageSize"] = str(results_per_page)

    return CurlRequestTemplate(
        method=template.method,
        url=template.url,
        headers=template.headers,
        cookies=template.cookies,
        json_body=body,
    )


def _pick_first_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list) and val:
        # often response has ["Title"]
        return _pick_first_str(val[0])
    return str(val)


def strip_html(text: str) -> str:
    """Remove simple HTML tags (e.g., <span class='highlight'>) from API text fields."""
    if not text:
        return ""
    # Remove tags
    cleaned = _HTML_TAG_RE.sub("", text)
    # Convert common entities and normalize whitespace
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_hits(response_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract normalized hits from the upstream response.

    Expected structure (based on screenshots):
      { "result": { "hits": [ { "highlight": {"TitleToDisplay": [...], "SummaryToDisplay": [...]}, "href": "..." } ] } }
    """
    result = response_json.get("result") or {}
    hits = result.get("hits") or []
    if not isinstance(hits, list):
        return []

    out: List[Dict[str, Any]] = []
    for idx, hit in enumerate(hits):
        if not isinstance(hit, dict):
            continue
        highlight = hit.get("highlight") or {}
        title = strip_html(_pick_first_str(highlight.get("TitleToDisplay") or hit.get("TitleToDisplay")))
        desc = strip_html(
            _pick_first_str(
                highlight.get("SummaryToDisplay")
                or hit.get("SummaryToDisplay")
                or highlight.get("Description")
                or hit.get("Description")
            )
        )
        url = _pick_first_str(hit.get("href") or hit.get("clientHref") or hit.get("_id"))

        out.append(
            {
                "rank": idx + 1,
                "title": title,
                "description": desc,
                "url": url,
            }
        )

    return out


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def is_match(expected_title: str, expected_url: str, actual_title: str, actual_url: str) -> bool:
    # Matching rule per your requirement: BOTH must match if both are provided.
    # If one of expected fields is empty, we match on the other.
    et = normalize_text(expected_title)
    eu = normalize_text(expected_url)
    at = normalize_text(actual_title)
    au = normalize_text(actual_url)

    if et and eu:
        return et == at and eu == au
    if et:
        return et == at
    if eu:
        return eu == au
    return False


async def fetch_top_results(
    client: httpx.AsyncClient,
    template: CurlRequestTemplate,
    query: str,
    top_n: int = 50,
) -> List[Dict[str, Any]]:
    req = inject_query(template, query=query, results_per_page=top_n)

    resp = await client.request(
        req.method,
        req.url,
        headers=req.headers,
        cookies=req.cookies,
        json=req.json_body,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    hits = extract_hits(data)
    return hits[:top_n]
