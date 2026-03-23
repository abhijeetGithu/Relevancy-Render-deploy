"""
Community page search client for form-encoded POST requests.

This module handles community page searches which use:
- application/x-www-form-urlencoded content type
- Different field extraction logic (SearchUnify format)
- JSON template configuration instead of raw cURL parsing
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional

import httpx


def get_config_from_template(template: Dict[str, Any]) -> Dict[str, Any]:
    """Extract config section from template with defaults."""
    config = template.get('config', {})
    return {
        'source_name': config.get('source_name', 'Community Search'),
        'documents_per_page': config.get('documents_per_page', 10),
        'max_pages': config.get('max_pages', 1)
    }


def prepare_form_data(
    body_template: Dict[str, Any],
    search_string: str,
    results_per_page: int,
    from_offset: int = 0
) -> Dict[str, str]:
    """Prepare form-encoded data from body template."""
    payload = dict(body_template)  # shallow copy
    payload['searchString'] = search_string
    payload['resultsPerPage'] = results_per_page
    payload['pageSize'] = results_per_page
    payload['from'] = from_offset
    
    # IMPORTANT: Always use _score sorting for relevance-based search results
    # This ensures documents are ranked by search relevance, not by date
    payload['sortby'] = '_score'
    payload['orderBy'] = 'desc'

    # Convert complex fields to JSON strings for form encoding
    form_data: Dict[str, str] = {}
    for k, v in payload.items():
        if isinstance(v, (dict, list)):
            form_data[k] = json.dumps(v)
        elif isinstance(v, bool):
            # Booleans should be converted to 'true'/'false' lowercase as in JS
            form_data[k] = 'true' if v else 'false'
        elif v is None:
            form_data[k] = ''
        else:
            form_data[k] = str(v)

    return form_data


def clean_highlight_text(s: str) -> str:
    """Clean text from highlight fields."""
    if not s:
        return ''
    # Unescape HTML entities
    s = html.unescape(str(s))
    # Remove long runs of underscores or repeated punctuation
    s = re.sub(r'[_]{2,}', ' ', s)
    s = re.sub(r'([\.]{2,})', ' ', s)
    # Remove odd sequences like '.__._' or '._'
    s = re.sub(r'[\._]{2,}', ' ', s)
    # Collapse multiple non-word characters into single space
    s = re.sub(r"[^\w\-&']+", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def extract_documents(response: Dict[str, Any], top_n: int = 20) -> List[Dict[str, Any]]:
    """Extract documents from SearchUnify-style response."""
    docs: List[Dict[str, Any]] = []
    result = response.get('result') or {}
    hits = result.get('hits') or []

    for hit in hits:
        # Score handling
        raw_score = hit.get('_score')
        try:
            score = float(raw_score) if raw_score not in (None, 'null', '') else 0.0
        except Exception:
            score = 0.0

        # Title extraction priority
        title = ''
        highlight = hit.get('highlight') or {}
        if highlight:
            for key in ('TitleToDisplayString', 'TitleToDisplay', 'title.en', 'title'):
                if key in highlight and highlight[key]:
                    candidate = highlight[key][0] if isinstance(highlight[key], list) else highlight[key]
                    title = clean_highlight_text(candidate)
                    if title:
                        break

        if not title:
            # Try top-level fields
            for fld in ('title', 'Title', 'NAME', 'name'):
                if fld in hit and hit[fld]:
                    title = str(hit[fld]).strip()
                    break
        if not title and '_source' in hit:
            for fld in ('title', 'Title', 'name'):
                if fld in hit['_source'] and hit['_source'][fld]:
                    title = str(hit['_source'][fld]).strip()
                    break

        # URL extraction
        url = ''
        for fld in ('href', 'clientHref', 'link', 'url', 'permalink'):
            if fld in hit and hit[fld]:
                url = str(hit[fld]).strip()
                break
        if not url and '_source' in hit:
            for fld in ('href', 'clientHref', 'link', 'url'):
                if fld in hit['_source'] and hit['_source'][fld]:
                    url = str(hit['_source'][fld]).strip()
                    break

        # Description extraction
        description = ''
        if highlight:
            for dkey in ('SummaryToDisplay', 'SummaryToDisplayString', 'Description'):
                if dkey in highlight and highlight[dkey]:
                    candidate = highlight[dkey][0] if isinstance(highlight[dkey], list) else highlight[dkey]
                    description = clean_highlight_text(candidate)
                    if description:
                        break

        if not description and 'metadata' in hit and isinstance(hit['metadata'], list):
            for m in hit['metadata']:
                if m.get('key') and m.get('value'):
                    if str(m.get('key')).lower() == 'description':
                        val = m.get('value')
                        if isinstance(val, list) and val:
                            description = clean_highlight_text(val[0])
                        else:
                            description = clean_highlight_text(val)
                        break

        docs.append({
            'rank': len(docs) + 1,
            'title': title,
            'url': url,
            'description': description,
            'score': score,
        })

    return docs[:top_n]


async def fetch_community_results(
    client: httpx.AsyncClient,
    template: Dict[str, Any],
    query: str,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch search results from a community page endpoint.
    
    Args:
        client: HTTP client for making requests
        template: Parsed JSON template with url, headers, body, and optional config
        query: Search query string
        top_n: Number of results to fetch
    
    Returns:
        List of document dicts with rank, title, url, description
    """
    url = template.get('url')
    if not url:
        raise ValueError("Template must include 'url' field")
    
    headers = template.get('headers', {})
    body_template = template.get('body', {})
    
    # Prepare form data
    form_data = prepare_form_data(body_template, query, top_n)
    
    # Make POST request with form-encoded data
    resp = await client.post(
        url,
        headers=headers,
        data=form_data,
        timeout=30.0,
    )
    resp.raise_for_status()
    
    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Response is not valid JSON: {e}")
    
    # Extract documents
    docs = extract_documents(data, top_n=top_n)
    return docs


def parse_community_curl_to_template(curl_text: str) -> Dict[str, Any]:
    """Parse a community page cURL command to JSON template format.
    
    Community page cURLs use form-encoded data instead of JSON body.
    This function extracts URL, headers, and parses form fields.
    """
    import shlex
    
    curl_text = curl_text.strip()
    if not curl_text:
        raise ValueError("cURL command is empty")
    
    # Remove line-continuation backslashes and normalize
    normalized = re.sub(r"\\\s*\n", " ", curl_text).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    
    # Tokenize
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
    
    if not tokens or tokens[0] != "curl":
        raise ValueError("cURL command must start with 'curl'")
    
    # Extract URL (first http/https token or after curl)
    url = ""
    headers: Dict[str, str] = {}
    body_text: Optional[str] = None
    
    i = 1
    while i < len(tokens):
        t = tokens[i]
        
        if t in ("-H", "--header"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing header value after -H/--header")
            hv = tokens[i]
            if ":" in hv:
                k, v = hv.split(":", 1)
                headers[k.strip()] = v.strip()
        elif t in ("--data", "--data-raw", "--data-binary", "--data-urlencode", "-d"):
            i += 1
            if i >= len(tokens):
                raise ValueError("Missing payload after --data")
            body_text = tokens[i]
        elif t.startswith("http://") or t.startswith("https://"):
            if not url:
                url = t
        
        i += 1
    
    if not url:
        raise ValueError("Could not find URL in cURL command")
    
    # Parse form-encoded body
    body: Dict[str, Any] = {}
    if body_text:
        # Check if it's JSON
        if body_text.strip().startswith('{'):
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                # Try parsing as form-encoded
                for pair in body_text.split('&'):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        # Try to parse value as JSON
                        try:
                            body[k] = json.loads(v)
                        except:
                            body[k] = v
        else:
            # Parse as form-encoded
            from urllib.parse import parse_qs, unquote
            parsed = parse_qs(body_text, keep_blank_values=True)
            for k, v in parsed.items():
                val = v[0] if len(v) == 1 else v
                # Try to parse JSON values
                if isinstance(val, str):
                    try:
                        body[k] = json.loads(val)
                    except:
                        body[k] = unquote(val)
                else:
                    body[k] = val
    
    # Ensure searchString field exists
    if 'searchString' not in body:
        body['searchString'] = ''
    
    return {
        'url': url,
        'method': 'POST',
        'headers': headers,
        'body': body,
        'config': {
            'source_name': 'Community Search',
            'documents_per_page': 10,
            'max_pages': 1
        }
    }
