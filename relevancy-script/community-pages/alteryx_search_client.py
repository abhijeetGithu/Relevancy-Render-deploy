#!/usr/bin/env python3
"""
Standalone Alteryx SearchUnify client.

Creates a form-urlencoded POST to the Alteryx community SearchUnify endpoint
using `curl_alteryx.json` as the template. It sends the payload (converting
lists/dicts to JSON strings) and extracts top documents (title, url,
description, score and metadata).

Usage:
  python3 scripts/alteryx_search_client.py "your search string here" --n 20

This file intentionally lives standalone and does not modify workspace
configuration files.
"""
import json
import os
import sys
import argparse
from typing import Any, Dict, List
import requests
import html
import re


def load_curl_template(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_config_from_template(template: Dict[str, Any]) -> Dict[str, Any]:
    """Extract config section from template with defaults."""
    config = template.get('config', {})
    return {
        'source_name': config.get('source_name', 'Alteryx Community'),
        'documents_per_page': config.get('documents_per_page', 10),
        'max_pages': config.get('max_pages', 1)
    }


def prepare_form_data(body_template: Dict[str, Any], search_string: str, results_per_page: int, from_offset: int = 0) -> Dict[str, str]:
    payload = dict(body_template)  # shallow copy
    payload['searchString'] = search_string
    payload['resultsPerPage'] = results_per_page
    payload['pageSize'] = results_per_page
    payload['from'] = from_offset
    # DO NOT send aggregations - just use whatever is in the template

    # Convert complex fields to JSON strings for form encoding
    form_data: Dict[str, str] = {}
    for k, v in payload.items():
        if isinstance(v, (dict, list)):
            form_data[k] = json.dumps(v)
        else:
            # Booleans should be converted to 'true'/'false' lowercase as in JS
            if isinstance(v, bool):
                form_data[k] = 'true' if v else 'false'
            elif v is None:
                form_data[k] = ''
            else:
                form_data[k] = str(v)

    return form_data


def post_form(url: str, headers: Dict[str, str], form_data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    # Ensure content-type header is present; requests will set its own boundary if files used,
    # but for simple form-encoded data we explicitly send the header as in the curl.
    hdrs = dict(headers or {})
    resp = requests.post(url, headers=hdrs, data=form_data, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError('Response is not valid JSON')


def extract_documents(response: Dict[str, Any], top_n: int = 20) -> List[Dict[str, Any]]:
    # Collect docs from hits, compute numeric score, then sort by score descending
    docs: List[Dict[str, Any]] = []
    result = response.get('result') or {}
    hits = result.get('hits') or []

    for hit in hits:
        # score may be null in sample -> handle gracefully
        raw_score = hit.get('_score')
        try:
            score = float(raw_score) if raw_score not in (None, 'null', '') else 0.0
        except Exception:
            score = 0.0

        def clean_highlight_text(s: str) -> str:
            if not s:
                return ''
            # Unescape HTML entities
            s = html.unescape(str(s))
            # Remove long runs of underscores or repeated punctuation often used as redaction/placeholders
            s = re.sub(r'[_]{2,}', ' ', s)
            s = re.sub(r'([\.]{2,})', ' ', s)
            # Remove odd sequences like '.__._' or '._' -> replace any mix of dots/underscores with single space
            s = re.sub(r'[\._]{2,}', ' ', s)
            # Collapse multiple non-word characters (except & and -) into single space to clean artifacts
            s = re.sub(r"[^\w\-&']+", ' ', s)
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        # Title extraction priority: highlight.TitleToDisplayString -> highlight.TitleToDisplay -> highlight.title.en -> top-level fields
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

        # Description extraction: prefer highlight 'SummaryToDisplay' (cleaned), then metadata 'Description'
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

        # Also collect metadata as a simple dict
        meta = {}
        if 'metadata' in hit and isinstance(hit['metadata'], list):
            for m in hit['metadata']:
                k = m.get('key')
                v = m.get('value')
                if k:
                    # Normalize single-value lists
                    if isinstance(v, list) and len(v) == 1:
                        meta[k] = v[0]
                    else:
                        meta[k] = v

        docs.append({
            'rank': len(docs) + 1,  # Assign rank in API order (1-indexed)
            'title': title,
            'url': url,
            'description': description,
            'score': score,
            'metadata': meta,
            'raw': hit
        })

    # Return top_n documents in the EXACT order from API response (no sorting)
    return docs[:top_n]


def fetch_documents(curl_json_path: str, search_string: str = '', results_per_page: int = None, from_offset: int = 0) -> List[Dict[str, Any]]:
    tpl = load_curl_template(curl_json_path)
    config = get_config_from_template(tpl)
    
    # Use config value if results_per_page not explicitly provided
    if results_per_page is None:
        results_per_page = config['documents_per_page']
    
    url = tpl.get('url')
    headers = tpl.get('headers', {})
    body_template = tpl.get('body', {})

    # Prepare form data (no aggregations override)
    form = prepare_form_data(body_template, search_string, results_per_page, from_offset=from_offset)

    # Post
    resp_json = post_form(url, headers, form)

    # Extract documents in EXACT API order
    docs = extract_documents(resp_json, top_n=results_per_page)
    return docs


def fetch_all_pages(curl_json_path: str, search_string: str = '', max_pages: int = None) -> List[Dict[str, Any]]:
    """Fetch multiple pages of results based on config or provided max_pages."""
    tpl = load_curl_template(curl_json_path)
    config = get_config_from_template(tpl)
    
    # Use config values
    docs_per_page = config['documents_per_page']
    if max_pages is None:
        max_pages = config['max_pages']
    
    all_docs = []
    for page_num in range(max_pages):
        from_offset = page_num * docs_per_page
        try:
            docs = fetch_documents(curl_json_path, search_string, results_per_page=docs_per_page, from_offset=from_offset)
            if not docs:
                break
            # Update rank to be global across all pages
            for doc in docs:
                doc['rank'] = len(all_docs) + doc['rank']
            all_docs.extend(docs)
        except Exception as e:
            print(f"⚠️  Error fetching page {page_num + 1}: {e}")
            break
    
    return all_docs


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('query', nargs='?', default='', help='Search string to send')
    parser.add_argument('--n', type=int, default=None, help='Number of documents to retrieve (overrides config)')
    parser.add_argument('--pages', type=int, default=None, help='Number of pages to fetch (overrides config)')
    # Default to curl_alteryx.json in the same folder as this script (makes path dynamic when both files sit together)
    parser.add_argument('--curl', default=os.path.join(os.path.dirname(__file__), 'curl_alteryx.json'), help='Path to curl JSON template')
    parser.add_argument('--output', default=None, help='Output CSV path (default: alteryx_results.csv in script folder)')
    parser.add_argument('--source', default=None, help='Source label to write in CSV (overrides config)')
    parser.add_argument('--from-offset', type=int, default=0, help='From offset used for this request (only for single-page mode)')
    args = parser.parse_args(argv)

    curl_path = os.path.abspath(args.curl)

    # If the provided path doesn't exist, try common fallback locations
    def find_curl_template(proposed: str):
        tried = []
        if proposed and os.path.exists(proposed):
            return proposed, tried
        tried.append(proposed)

        # candidate locations relative to this script
        script_dir = os.path.dirname(__file__)
        # Prefer same-directory template, then a few common fallbacks
        candidates = [
            os.path.join(script_dir, 'curl_alteryx.json'),
            os.path.join(script_dir, '..', 'curl_alteryx.json'),
            os.path.join(script_dir, '..', 'community-pages', 'curl_alteryx.json'),
            os.path.join(script_dir, '..', '..', 'curl_alteryx.json'),
        ]

        for c in candidates:
            c_abs = os.path.abspath(c)
            tried.append(c_abs)
            if os.path.exists(c_abs):
                return c_abs, tried

        return None, tried

    curl_found, tried_paths = find_curl_template(curl_path)
    if not curl_found:
        print("❌ curl template not found. Paths tried:")
        for p in tried_paths:
            print(f"  - {p}")
        return 1
    curl_path = curl_found

    # Load config from template
    try:
        tpl = load_curl_template(curl_path)
        config = get_config_from_template(tpl)
    except Exception as e:
        print(f"❌ Error loading curl template: {e}")
        return 1

    # Determine final values (CLI args override config)
    source_name = args.source if args.source else config['source_name']
    docs_per_page = args.n if args.n else config['documents_per_page']
    max_pages = args.pages if args.pages else config['max_pages']
    
    # Output path
    if args.output:
        out_path = os.path.abspath(args.output)
    else:
        out_path = os.path.join(os.path.dirname(__file__), 'alteryx_results.csv')

    print(f"📋 Config: Source='{source_name}', Docs/Page={docs_per_page}, Max Pages={max_pages}")

    try:
        if max_pages > 1:
            print(f"🔍 Fetching {max_pages} pages for query: {args.query}")
            docs = fetch_all_pages(curl_path, search_string=args.query, max_pages=max_pages)
        else:
            print(f"🔍 Fetching single page for query: {args.query}")
            docs = fetch_documents(curl_path, search_string=args.query, results_per_page=docs_per_page, from_offset=args.from_offset)
    except Exception as e:
        print(f"❌ Error fetching documents: {e}")
        import traceback
        traceback.print_exc()
        return 2

    print(f"✅ Retrieved {len(docs)} documents")

    # Write CSV
    try:
        import csv
        # Ensure directory exists
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        write_header = True
        if os.path.exists(out_path):
            # If file exists and has header already, do not duplicate header
            write_header = False

        with open(out_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            if write_header:
                writer.writerow(['Source', 'FromOffset', 'Title', 'Description', 'Document URL'])

            for d in docs:
                title = d.get('title', '') or ''
                desc = d.get('description', '') or ''
                url = d.get('url', '') or ''
                from_offset = args.from_offset if max_pages == 1 else ((d['rank'] - 1) // docs_per_page) * docs_per_page
                writer.writerow([source_name, from_offset, title, desc, url])

        print(f"✅ Results saved to: {out_path}")
    except Exception as e:
        print(f"❌ Error writing CSV: {e}")
        return 3

    # Also print a brief summary to stdout
    for d in docs:
        print(f"{d['rank']}. {d['title'][:100]}")
        if d['url']:
            print(f"   URL: {d['url'][:100]}")
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
