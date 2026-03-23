"""
Community page request handler for form-encoded POST requests.

This module handles community page searches which use:
- application/x-www-form-urlencoded content type
- Different field extraction logic (SearchUnify community format)
- JSON template configuration from curl_community.json
"""
import json
import os
import re
import html
import requests
from typing import Any, Dict, List, Optional, Tuple


def load_community_template(path: str) -> Dict[str, Any]:
    """Load community curl template from JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_community_curl(curl_text: str) -> Dict[str, Any]:
    """Parse a community-style cURL command into a template dictionary."""
    template = {
        'url': '',
        'headers': {},
        'body': {},
        'config': {
            'source_name': 'Community',
            'documents_per_page': 10,
            'max_pages': 1
        }
    }
    
    lines = curl_text.replace('\\\n', ' ').strip().split('\n')
    full_cmd = ' '.join(lines)
    
    # Extract URL
    url_match = re.search(r"curl\s+['\"]?([^'\"]+)['\"]?", full_cmd)
    if not url_match:
        url_match = re.search(r"'(https?://[^']+)'", full_cmd)
    if url_match:
        template['url'] = url_match.group(1).strip()
    
    # Extract headers
    header_pattern = re.compile(r"-H\s+['\"]([^:]+):\s*([^'\"]+)['\"]", re.IGNORECASE)
    for match in header_pattern.finditer(full_cmd):
        key = match.group(1).strip()
        value = match.group(2).strip()
        template['headers'][key] = value
    
    # Extract body data (--data or -d)
    data_match = re.search(r"(?:--data-raw|--data|-d)\s+['\"](.+?)['\"](?:\s+-|$)", full_cmd, re.DOTALL)
    if not data_match:
        data_match = re.search(r"(?:--data-raw|--data|-d)\s+'(.+?)'", full_cmd, re.DOTALL)
    
    if data_match:
        data_str = data_match.group(1)
        # Parse form-encoded data
        for pair in data_str.split('&'):
            if '=' in pair:
                key, value = pair.split('=', 1)
                key = requests.utils.unquote(key)
                value = requests.utils.unquote(value)
                # Try to parse JSON values
                try:
                    template['body'][key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    template['body'][key] = value
    
    return template


def prepare_form_data(
    body_template: Dict[str, Any],
    search_string: str,
    results_per_page: int,
    from_offset: int = 0
) -> Dict[str, str]:
    """Prepare form-encoded data for community page search."""
    payload = dict(body_template)
    payload['searchString'] = search_string
    payload['resultsPerPage'] = results_per_page
    payload['pageSize'] = results_per_page
    payload['from'] = from_offset
    
    # Force _score sorting for relevance-based results
    payload['sortby'] = '_score'
    payload['orderBy'] = 'desc'
    
    # Convert complex fields to JSON strings for form encoding
    form_data: Dict[str, str] = {}
    for k, v in payload.items():
        if isinstance(v, (dict, list)):
            form_data[k] = json.dumps(v)
        elif isinstance(v, bool):
            form_data[k] = 'true' if v else 'false'
        elif v is None:
            form_data[k] = ''
        else:
            form_data[k] = str(v)
    
    return form_data


def clean_highlight_text(s: str) -> str:
    """Clean highlight text from HTML entities and artifacts."""
    if not s:
        return ''
    s = html.unescape(str(s))
    s = re.sub(r'[_]{2,}', ' ', s)
    s = re.sub(r'([\.]{2,})', ' ', s)
    s = re.sub(r'[\._]{2,}', ' ', s)
    s = re.sub(r"[^\w\-&']+", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def extract_documents(response: Dict[str, Any], top_n: int = 20) -> List[Dict[str, Any]]:
    """Extract documents from community page search response."""
    docs: List[Dict[str, Any]] = []
    result = response.get('result') or {}
    hits = result.get('hits') or []
    
    for hit in hits:
        # Extract score
        raw_score = hit.get('_score')
        try:
            score = float(raw_score) if raw_score not in (None, 'null', '') else 0.0
        except Exception:
            score = 0.0
        
        # Title extraction
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
            for fld in ('title', 'Title', 'NAME', 'name', 'objName'):
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
                if m.get('key') and str(m.get('key')).lower() == 'description':
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
            'raw': hit
        })
    
    return docs[:top_n]


def hit_community_request(
    template: Dict[str, Any],
    from_offset: int,
    results_per_page: int = 10,
    search_string: str = ''
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Make a community page API request and extract a single document.
    Returns (title, description, url) tuple.
    """
    try:
        url = template.get('url')
        headers = template.get('headers', {})
        body_template = template.get('body', {})
        
        # Prepare form data
        form_data = prepare_form_data(body_template, search_string, results_per_page, from_offset)
        
        print(f"🔍 DEBUG: Making community API request for from={from_offset}")
        print(f"📤 Request URL: {url}")
        print(f"📤 Content-Type: application/x-www-form-urlencoded")
        
        # Make POST request with form-encoded data
        response = requests.post(url, headers=headers, data=form_data, timeout=30)
        
        print(f"📥 Response status code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ API Error Response: {response.text[:500]}")
            response.raise_for_status()
        
        resp_json = response.json()
        
        # Extract documents
        docs = extract_documents(resp_json, top_n=1)
        
        if docs:
            doc = docs[0]
            return doc.get('title'), doc.get('description', ''), doc.get('url')
        else:
            print("❌ No hits found in community response")
            return None, None, None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error in hit_community_request: {e}")
        return None, None, None
    except Exception as e:
        print(f"❌ General error in hit_community_request: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        return None, None, None


def fetch_community_documents(
    template: Dict[str, Any],
    search_string: str = '',
    results_per_page: int = 10,
    from_offset: int = 0
) -> List[Dict[str, Any]]:
    """Fetch multiple documents from community page search."""
    try:
        url = template.get('url')
        headers = template.get('headers', {})
        body_template = template.get('body', {})
        
        form_data = prepare_form_data(body_template, search_string, results_per_page, from_offset)
        
        response = requests.post(url, headers=headers, data=form_data, timeout=30)
        response.raise_for_status()
        
        resp_json = response.json()
        docs = extract_documents(resp_json, top_n=results_per_page)
        
        return docs
        
    except Exception as e:
        print(f"❌ Error fetching community documents: {e}")
        return []
