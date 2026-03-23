#!/usr/bin/env python3
"""
Small Flask frontend to paste a curl command and update curl_alteryx.json.

Run:
  pip install flask
  python3 community-pages/curl_frontend.py

Then open http://127.0.0.1:5000/ and paste the curl command.

The parser extracts URL, headers (-H), cookies (-b) and data (--data-raw / --data)
and writes a JSON template `community-pages/curl_alteryx.json` with parsed headers
and a decoded `body` dict for form submission.
"""
import re
import json
import os
from urllib.parse import unquote_plus, parse_qs
from flask import Flask, request, render_template_string, redirect, url_for, flash

ROOT = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(ROOT, 'curl_alteryx.json')
TEMPLATE_RUN_PATH = os.path.join(ROOT, 'curl_alteryx_run.json')

app = Flask(__name__)
app.secret_key = 'dev-placeholder'

HTML = '''
<!doctype html>
<html>
<head>
<title>Paste curl command</title>
<style>
  body { font-family: Arial, sans-serif; margin: 20px; }
  .form-group { margin-bottom: 15px; }
  label { display: inline-block; width: 180px; font-weight: bold; }
  input[type="text"], input[type="number"] { width: 300px; padding: 5px; }
  textarea { width: 100%; padding: 10px; font-family: monospace; }
  input[type="submit"] { padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; font-size: 16px; }
  input[type="submit"]:hover { background: #0056b3; }
  .btn-link { display: inline-block; padding: 10px 20px; background: #28a745; color: white; text-decoration: none; border-radius: 3px; font-size: 16px; }
  .btn-link:hover { background: #1e7e34; }
  .actions { display: flex; gap: 12px; align-items: center; margin-top: 10px; }
  .flash { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; padding: 10px; margin-bottom: 15px; }
</style>
</head>
<body>
<h2>Paste curl command to update curl templates</h2>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    {% for m in messages %}
      <div class="flash">{{ m }}</div>
    {% endfor %}
  {% endif %}
{% endwith %}
<form method=post>
  <div class="form-group">
    <label for="source_name">Source Name:</label>
    <input type="text" id="source_name" name="source_name" value="{{source_name|default('Alteryx Community')}}" required>
  </div>
  <div class="form-group">
    <label for="documents_per_page">Documents Per Page:</label>
    <input type="number" id="documents_per_page" name="documents_per_page" value="{{documents_per_page|default(10)}}" min="1" max="100" required>
  </div>
  <div class="form-group">
    <label for="max_pages">Max Pages:</label>
    <input type="number" id="max_pages" name="max_pages" value="{{max_pages|default(1)}}" min="1" max="50" required>
  </div>
  <div class="form-group">
    <label for="curl">Curl Command:</label>
    <textarea id="curl" name="curl" rows="18" placeholder="Paste your curl command here">{{curl|default('')}}</textarea>
  </div>
    <div class="actions">
      <input type="submit" value="Parse and Save">
      <a class="btn-link" href="https://integration.searchunify.com/relevancy-check" target="_blank" rel="noopener noreferrer">Generate Queries</a>
    </div>
</form>
</body>
</html>
'''


def parse_curl_text(curl_text: str) -> dict:
    # Extract URL: curl 'URL' or curl "URL"
    url_match = re.search(r"curl ['\"](?P<url>https?://[^'\"]+)['\"]", curl_text)
    url = url_match.group('url') if url_match else ''

    # Extract headers -H 'Name: value'
    headers = {}
    for m in re.finditer(r"-H ['\"](?P<h>[^'\"]+)['\"]", curl_text):
        h = m.group('h')
        if ':' in h:
            name, val = h.split(':', 1)
            headers[name.strip()] = val.strip()

    # Cookies from -b '...'
    bmatch = re.search(r"-b ['\"](?P<c>[^'\"]+)['\"]", curl_text)
    if bmatch and 'Cookie' not in headers:
        headers['Cookie'] = bmatch.group('c')

    # Extract data from --data-raw or --data
    data_match = re.search(r"--data-raw ['\"](?P<data>.+?)['\"]", curl_text)
    if not data_match:
        data_match = re.search(r"--data ['\"](?P<data>.+?)['\"]", curl_text)
    data_raw = data_match.group('data') if data_match else ''

    # Parse urlencoded form body into dict
    body = {}
    if data_raw:
        # percent decode then parse
        decoded = unquote_plus(data_raw)
        # parse_qs yields lists
        parsed = parse_qs(decoded, keep_blank_values=True)
        for k, vs in parsed.items():
            v = vs if len(vs) > 1 else vs[0]
            # Try to interpret JSON values
            if isinstance(v, str) and (v.startswith('{') or v.startswith('[')):
                try:
                    body[k] = json.loads(v)
                    continue
                except Exception:
                    pass
            # convert booleans/numbers
            if isinstance(v, str) and v.lower() in ('true', 'false'):
                body[k] = True if v.lower() == 'true' else False
            else:
                # keep as string or list
                body[k] = v

    # Create minimal template
    template = {
        'url': url or 'https://community.alteryx.com/plugins/custom/alteryx/alteryx/searchUnify_Endpoint',
        'method': 'POST',
        'headers': headers,
        'body': body
    }
    return template


def _coerce_int(v, default: int) -> int:
  try:
    return int(v)
  except Exception:
    return default


def _normalize_body_for_client(body: dict, documents_per_page: int) -> dict:
  """Normalize parsed curl body for our client.

  `alteryx_search_client.prepare_form_data()` will overwrite:
    - searchString
    - resultsPerPage
    - pageSize
    - from

  So we mainly ensure the template contains the right defaults/types.
  """
  body = dict(body or {})

  # Defaults (keep close to existing behavior)
  defaults = {
    'react': 1,
    'isRecommendationsWidget': False,
    'searchString': '',
    'from': 0,
    'resultsPerPage': documents_per_page,
    'pageSize': documents_per_page,
    'language': 'en'
  }
  for k, v in defaults.items():
    if k not in body:
      body[k] = v

  # Normalize common numeric fields to int so our form encoder produces clean numbers
  for k in ('from', 'pageNo', 'resultsPerPage', 'pageSize', 'minSummaryLength'):
    if k in body:
      body[k] = _coerce_int(body.get(k), body.get(k) if isinstance(body.get(k), int) else 0)

  # Ensure booleans remain booleans when possible
  for k in (
    'isRecommendationsWidget',
    'isWildCard',
    'mergeSources',
    'versionResults',
    'suCaseCreate',
    'paginationClicked',
    'getAutoTunedResult',
    'getSimilarSearches',
    'smartFacets',
    'showMoreSummary',
    'showContentTag',
  ):
    if k in body and isinstance(body[k], str):
      if body[k].lower() in ('true', 'false'):
        body[k] = body[k].lower() == 'true'

  return body


def _get_pasted_aggregations(pasted_body: dict | None) -> list:
  aggs = (pasted_body or {}).get('aggregations') if isinstance(pasted_body, dict) else None
  return aggs if isinstance(aggs, list) else []


def _sanitize_aggregations(aggs: list) -> list:
  """Keep only dict-shaped aggregations; do not add or remove types here."""
  return [a for a in (aggs or []) if isinstance(a, dict)]


def _apply_run_aggregations(body: dict, pasted_body: dict | None = None) -> dict:
  """For curl_alteryx_run.json, include all pasted aggregations except `_index`."""
  body = dict(body or {})
  pasted_aggs = _get_pasted_aggregations(pasted_body)
  filtered = [a for a in pasted_aggs if not (isinstance(a, dict) and a.get('type') == '_index')]
  body['aggregations'] = _sanitize_aggregations(filtered)
  return body


def _apply_eval_aggregations(body: dict, pasted_body: dict | None = None) -> dict:
  """For curl_alteryx.json, include all pasted aggregations (including `_index`).

  Do not add any aggregation that wasn't present in the pasted curl.
  """
  body = dict(body or {})
  pasted_aggs = _get_pasted_aggregations(pasted_body)
  body['aggregations'] = _sanitize_aggregations(pasted_aggs)
  return body


def _build_eval_template(parsed_tpl: dict, source_name: str, documents_per_page: int, max_pages: int) -> dict:
  """Template for evaluation runs (curl_alteryx.json).

  Keep it stable/minimal: always ensure `rootCategoryId=external` exists, and
  don't force `_index` unless the user pasted it.
  """
  tpl = {
    'url': parsed_tpl.get('url') ,
    'method': parsed_tpl.get('method', 'POST'),
    'headers': parsed_tpl.get('headers', {}),
    'body': _normalize_body_for_client(parsed_tpl.get('body', {}), documents_per_page),
    'config': {
      'source_name': source_name,
      'documents_per_page': documents_per_page,
      'max_pages': max_pages
    }
  }

  body = tpl['body']
  # For evaluation template, include `_index` only if it existed in the pasted curl.
  body = _apply_eval_aggregations(body, pasted_body=parsed_tpl.get('body', {}))
  tpl['body'] = body
  # Evaluation default ordering: keep whatever is pasted, but if missing, use post_time/desc
  body.setdefault('sortby', 'post_time')
  body.setdefault('orderBy', 'desc')

  return tpl


def _build_run_template(parsed_tpl: dict, source_name: str, documents_per_page: int, max_pages: int) -> dict:
  """Template for debug/run (curl_alteryx_run.json).

  Aligns with the browser-style curl you pasted: sort by _score by default.
  """
  tpl = {
    'url': parsed_tpl.get('url') or '',
    'method': parsed_tpl.get('method', 'POST'),
    'headers': parsed_tpl.get('headers', {}),
    'body': _normalize_body_for_client(parsed_tpl.get('body', {}), documents_per_page),
    'config': {
      'source_name': f'{source_name} (run)',
      'documents_per_page': documents_per_page,
      'max_pages': max_pages
    }
  }

  body = tpl['body']
  # For run/debug template, exclude `_index` but preserve all other pasted aggregations.
  body = _apply_run_aggregations(body, pasted_body=parsed_tpl.get('body', {}))
  tpl['body'] = body
  # Run/debug ordering: ALWAYS sort by _score regardless of what was pasted
  body['sortby'] = '_score'
  body.setdefault('orderBy', 'desc')

  return tpl


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        curl_text = request.form.get('curl', '')
        source_name = request.form.get('source_name', 'Alteryx Community').strip()
        documents_per_page = int(request.form.get('documents_per_page', 10))
        max_pages = int(request.form.get('max_pages', 1))

        if not curl_text.strip():
            flash('Please paste a curl command')
            return redirect(url_for('index'))

        try:
            parsed = parse_curl_text(curl_text)

            # Build both templates from the same pasted curl
            eval_tpl = _build_eval_template(parsed, source_name, documents_per_page, max_pages)
            run_tpl = _build_run_template(parsed, source_name, documents_per_page, max_pages)

            # Write both files
            with open(TEMPLATE_PATH, 'w', encoding='utf-8') as f:
                json.dump(eval_tpl, f, indent=2)
            with open(TEMPLATE_RUN_PATH, 'w', encoding='utf-8') as f:
                json.dump(run_tpl, f, indent=2)

            flash(
                f'✅ Saved templates to {TEMPLATE_PATH} and {TEMPLATE_RUN_PATH} '
                f'(Source: {source_name}, Docs/Page: {documents_per_page}, Max Pages: {max_pages})'
            )
            return render_template_string(HTML, curl=curl_text, source_name=source_name, 
                                        documents_per_page=documents_per_page, max_pages=max_pages)
        except Exception as e:
            flash(f'Error parsing curl: {e}')
            return redirect(url_for('index'))

    # GET request - load existing config if available
    source_name = 'Alteryx Community'
    documents_per_page = 10
    max_pages = 1
    
    # Prefer loading existing values from curl_alteryx.json; fall back to run template
    candidate_paths = [TEMPLATE_PATH, TEMPLATE_RUN_PATH]
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
                config = existing.get('config', {})
                source_name = config.get('source_name', source_name)
                documents_per_page = config.get('documents_per_page', documents_per_page)
                max_pages = config.get('max_pages', max_pages)
            break
        except Exception:
            continue
    
    return render_template_string(HTML, source_name=source_name, 
                                documents_per_page=documents_per_page, max_pages=max_pages)


if __name__ == '__main__':
    print('Starting curl frontend on http://127.0.0.1:5000')
    app.run(debug=True)
