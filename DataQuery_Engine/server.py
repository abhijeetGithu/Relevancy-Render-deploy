from flask import Flask, request, jsonify, send_from_directory, Response, send_file
from dotenv import load_dotenv
import json
import logging
import os
import subprocess
import sys
import traceback
import shlex
import datetime
import tempfile
import uuid
import io
import threading
import queue
import time
import glob
import shutil
import csv as csv_module
import asyncio

# Ensure server logs are visible in terminal for debugging
logger = logging.getLogger(__name__)
if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - DQE - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
# Suppress noisy HTTP request logs from httpx/httpcore (one per OpenAI/Gemini call)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# NDJSON stream for LLM comparison progress (optional; set on worker thread for /api/stream/... route)
_llm_comparison_stream_emit = threading.local()


def _llm_comparison_emit_event(obj: dict):
    cb = getattr(_llm_comparison_stream_emit, 'emit', None)
    if cb:
        try:
            cb(obj)
        except Exception:
            pass

# Load environment from .env so child processes inherit secrets (can be overridden by real env)
load_dotenv()

# Initialize Flask app (was accidentally removed during refactor)
app = Flask(__name__)
# Get the directory where this script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMP_DOWNLOAD_DIR = os.path.join(BASE_DIR, '.temp_downloads')
os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

TEMP_STORE_LOCK = threading.Lock()

def _token_paths(token: str):
    data_path = os.path.join(TEMP_DOWNLOAD_DIR, f"{token}.bin")
    meta_path = os.path.join(TEMP_DOWNLOAD_DIR, f"{token}.json")
    return data_path, meta_path

def _cleanup_token(token: str):
    data_path, meta_path = _token_paths(token)
    for path in (data_path, meta_path):
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

def _load_token_meta(token: str):
    data_path, meta_path = _token_paths(token)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, 'r') as mf:
            meta = json.load(mf)
    except Exception:
        return None
    expires_at = meta.get('expires_at')
    if expires_at and expires_at < time.time():
        _cleanup_token(token)
        return None
    if not os.path.exists(meta.get('path', data_path)):
        _cleanup_token(token)
        return None
    meta['path'] = meta.get('path', data_path)
    meta['filename'] = meta.get('filename') or 'output.csv'
    return meta

def _purge_expired_temp_files():
    now = time.time()
    for meta_file in glob.glob(os.path.join(TEMP_DOWNLOAD_DIR, '*.json')):
        try:
            with open(meta_file, 'r') as mf:
                meta = json.load(mf)
        except Exception:
            meta = None
        token = os.path.splitext(os.path.basename(meta_file))[0]
        if not meta or meta.get('expires_at', 0) < now:
            _cleanup_token(token)

_purge_expired_temp_files()

def store_temp_file(content_bytes: bytes, filename: str, ttl: int = 600) -> str:
    """Persist bytes to a temp directory under a token, accessible across workers."""
    token = uuid.uuid4().hex
    expires_at = time.time() + ttl
    safe_filename = os.path.basename(filename) or 'output.csv'
    data_path, meta_path = _token_paths(token)
    with TEMP_STORE_LOCK:
        with open(data_path, 'wb') as df:
            df.write(content_bytes)
        meta = {
            'filename': safe_filename,
            'path': data_path,
            'expires_at': expires_at
        }
        with open(meta_path, 'w') as mf:
            json.dump(meta, mf)
    t = threading.Timer(ttl, _cleanup_token, args=(token,))
    t.daemon = True
    t.start()
    return token

def create_tempfile_from_token(token: str):
    """Materialize a disk file from a stored token and return its path."""
    meta = _load_token_meta(token)
    if not meta:
        return None
    try:
        suffix = os.path.splitext(meta.get('filename', ''))[1] or '.csv'
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tf.close()
        with open(meta['path'], 'rb') as src, open(tf.name, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        return tf.name
    except Exception:
        return None


def _mimetype_for_download(filename: str) -> str:
    fn = (filename or '').lower()
    if fn.endswith('.xlsx') or fn.endswith('.xlsm'):
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if fn.endswith('.xls'):
        return 'application/vnd.ms-excel'
    if fn.endswith('.csv'):
        return 'text/csv; charset=utf-8'
    return 'application/octet-stream'


def _allowed_upload_extension(filename: str) -> bool:
    fn = (filename or '').lower()
    return fn.endswith('.csv') or fn.endswith('.xlsx') or fn.endswith('.xlsm') or fn.endswith('.xls')


def _strip_tabular_column_names(df):
    """Trim header whitespace so UI selections match files exported with trailing spaces in column names."""
    import pandas as pd

    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def resolve_user_tabular_column(df, requested):
    """Map UI-selected header to a column on df (headers already stripped). Exact match, then case-insensitive."""
    if requested is None:
        return None
    req = str(requested).strip()
    if not req:
        return None
    if req in df.columns:
        return req
    rlow = req.lower()
    for c in df.columns:
        if str(c).lower() == rlow:
            return c
    return None


def read_tabular_headers_only(path_or_buf, filename: str):
    """
    Load column headers only from CSV or Excel (first sheet).
    path_or_buf: filesystem path (str) or binary buffer (e.g. io.BytesIO).
    """
    import pandas as pd

    fn = (filename or '').lower()
    if fn.endswith('.csv'):
        df = pd.read_csv(path_or_buf, nrows=0)
    elif fn.endswith('.xlsx') or fn.endswith('.xlsm'):
        if hasattr(path_or_buf, 'seek'):
            path_or_buf.seek(0)
        df = pd.read_excel(path_or_buf, engine='openpyxl', sheet_name=0, nrows=0)
    elif fn.endswith('.xls'):
        if hasattr(path_or_buf, 'seek'):
            path_or_buf.seek(0)
        df = pd.read_excel(path_or_buf, sheet_name=0, nrows=0)
    else:
        raise ValueError('Unsupported format; use .csv, .xlsx, .xlsm, or .xls')
    return _strip_tabular_column_names(df)


def load_tabular_dataframe(path: str, filename: str, sheet_name=0):
    """Load full table from disk path; format chosen by original filename extension."""
    import pandas as pd

    fn = (filename or '').lower()
    if fn.endswith('.csv'):
        df = pd.read_csv(path)
    elif fn.endswith('.xlsx') or fn.endswith('.xlsm'):
        df = pd.read_excel(path, engine='openpyxl', sheet_name=sheet_name)
    elif fn.endswith('.xls'):
        df = pd.read_excel(path, sheet_name=sheet_name)
    else:
        try:
            df = pd.read_csv(path)
        except Exception:
            df = pd.read_excel(path, engine='openpyxl', sheet_name=sheet_name)
    return _strip_tabular_column_names(df)


def extract_queries_from_token(token: str, preferred_query_column: str = None):
    """Load query strings from a tokenized CSV or Excel file (first sheet), with optional query column override."""
    meta = _load_token_meta(token)
    if not meta:
        return None, None, 'Token not found or expired'

    file_path = meta['path']
    orig_name = meta.get('filename') or 'upload.csv'

    try:
        df = load_tabular_dataframe(file_path, orig_name, sheet_name=0)
    except Exception as e:
        return None, None, 'Could not read uploaded file ({}). Use .csv or Excel .xlsx/.xlsm/.xls.'.format(e)

    if df is None or df.empty:
        return None, None, 'Uploaded file has no data rows'

    columns = list(df.columns)
    if not columns:
        return None, None, 'File has no column headers'

    col_set = set(columns)
    pref = (preferred_query_column or '').strip()
    if pref and pref in col_set:
        query_column = pref
    else:
        query_column = None
        for name in (
            'Generated_Query',
            'Query',
            'query',
            'generated_query',
            'KeyWord',
            'Keyword',
            'keyword',
        ):
            if name in col_set:
                query_column = name
                break
        if not query_column:
            query_column = columns[0]

    raw = df[query_column].dropna()
    queries = []
    for x in raw:
        q = str(x).strip()
        if q:
            queries.append(q)

    if not queries:
        return None, query_column, 'No queries found (column: {})'.format(query_column)

    return queries, query_column, None
@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/')
def index():
    """Serve the frontend HTML file"""
    return send_from_directory(BASE_DIR, 'frontend.html')


@app.route('/api/config')
def get_config():
    """Return service URLs for frontend to use in cross-service navigation."""
    return jsonify({
        'portal_url': PORTAL_URL,
        'llm_comparator_url': LLM_COMPARATOR_URL,
    })


@app.route('/api/update-curl-input', methods=['POST'])
def update_curl_input():
    """Full replacement of curl_input.json with newly parsed curl command data.
    Every new curl paste completely replaces the old file -- no merging."""
    try:
        incoming = request.json or {}
        raw_curl = incoming.pop('rawCurl', None)
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')

        def try_parse_json_string(s):
            if not isinstance(s, str):
                return None
            s = s.strip()
            if len(s) < 5 or s[0] not in ('{', '['):
                return None
            try:
                return json.loads(s)
            except Exception:
                try:
                    return json.loads(s.replace('\\"', '"').replace("\\'", "'"))
                except Exception:
                    return None

        def extract_body_from_raw_curl(raw):
            """Server-side extraction of --data-raw / --data / -d body from raw curl string."""
            import re
            cleaned = re.sub(r'\\\s*\n', ' ', raw)
            for flag in ['--data-raw', '--data', '-d']:
                idx = cleaned.find(flag)
                if idx == -1:
                    continue
                pos = idx + len(flag)
                while pos < len(cleaned) and cleaned[pos] == ' ':
                    pos += 1
                if pos >= len(cleaned):
                    continue
                quote = cleaned[pos]
                if quote == "'":
                    end = cleaned.find("'", pos + 1)
                    if end != -1:
                        return cleaned[pos + 1:end]
                elif quote == '"':
                    end = pos + 1
                    while end < len(cleaned):
                        if cleaned[end] == '\\':
                            end += 2
                            continue
                        if cleaned[end] == '"':
                            break
                        end += 1
                    if end < len(cleaned):
                        return cleaned[pos + 1:end].replace('\\"', '"')
                else:
                    end = cleaned.find(' ', pos)
                    return cleaned[pos:end] if end != -1 else cleaned[pos:]
            return None

        def body_looks_valid(b):
            """Check that a parsed body dict has enough fields to be a real API payload."""
            if not isinstance(b, dict):
                return False
            known_keys = {'uid', 'authtoken', 'accessToken', 'sid', 'searchString', 'from', 'resultsPerPage'}
            return len(b) >= 5 and bool(known_keys & set(b.keys()))

        # Build the result from scratch using ONLY incoming data
        result = {}
        result['url'] = incoming.get('url', '')
        result['method'] = incoming.get('method', 'POST')

        # Headers: use exactly what was provided, with canonical casing
        raw_headers = incoming.get('headers', {})
        if isinstance(raw_headers, dict):
            canonical_map = {
                'accept': 'Accept', 'accept-language': 'Accept-Language',
                'content-type': 'Content-Type', 'user-agent': 'User-Agent',
                'referer': 'Referer', 'origin': 'Origin', 'priority': 'Priority',
                'cookie': 'Cookie', 'sec-ch-ua': 'Sec-Ch-Ua',
                'sec-ch-ua-mobile': 'Sec-Ch-Ua-Mobile',
                'sec-ch-ua-platform': 'Sec-Ch-Ua-Platform',
                'sec-fetch-dest': 'Sec-Fetch-Dest',
                'sec-fetch-mode': 'Sec-Fetch-Mode',
                'sec-fetch-site': 'Sec-Fetch-Site',
            }
            out = {}
            for k, v in raw_headers.items():
                out[canonical_map.get(k.lower(), k)] = v
            result['headers'] = out
        else:
            result['headers'] = {}

        # Body: use exactly what was provided
        raw_body = incoming.get('body', {})
        if isinstance(raw_body, dict):
            if 'data' in raw_body and len(raw_body) == 1:
                parsed = try_parse_json_string(raw_body['data'])
                result['body'] = parsed if isinstance(parsed, dict) else raw_body
            else:
                result['body'] = raw_body
        elif isinstance(raw_body, str):
            parsed = try_parse_json_string(raw_body)
            result['body'] = parsed if isinstance(parsed, dict) else {}
        else:
            result['body'] = {}

        # Fallback: if the body looks broken but we have rawCurl, re-extract server-side
        if raw_curl and not body_looks_valid(result['body']):
            raw_data_str = extract_body_from_raw_curl(raw_curl)
            if raw_data_str:
                parsed = try_parse_json_string(raw_data_str)
                if parsed and isinstance(parsed, dict) and body_looks_valid(parsed):
                    result['body'] = parsed

        # If method is GET but we have a JSON body, force POST
        if result['method'].upper() == 'GET' and result['body']:
            result['method'] = 'POST'

        with open(curl_input_path, 'w') as f:
            json.dump(result, f, indent=2)

        return jsonify({
            'success': True,
            'message': 'curl_input.json fully replaced with new curl data',
            'curl_input': result,
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/update-config', methods=['POST'])
def update_config():
    """Update the config.json file with new configuration"""
    try:
        data = request.json
        
        # Path to config.json
        config_path = os.path.join(BASE_DIR, 'config.json')
        
        # Extract aggregations if present (to save to curl_input.json instead)
        aggregations = data.pop('AGGREGATIONS', None)
        
        # Read existing config to preserve any fields not in the form
        existing_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    existing_config = json.load(f)
            except:
                pass  # If file doesn't exist or is invalid, start fresh
        
        # Update with new data
        existing_config.update(data)
        
        # Save the updated config
        with open(config_path, 'w') as f:
            json.dump(existing_config, f, indent=2)
        
        # If aggregations were provided, update curl_input.json
        if aggregations is not None:
            curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
            try:
                with open(curl_input_path, 'r') as f:
                    curl_input = json.load(f)
                
                if 'body' not in curl_input:
                    curl_input['body'] = {}
                
                curl_input['body']['aggregations'] = aggregations
                
                with open(curl_input_path, 'w') as f:
                    json.dump(curl_input, f, indent=2)
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'Failed to update curl_input.json: {str(e)}'
                }), 500
        
        return jsonify({
            'success': True,
            'message': 'config.json updated successfully' + (' and aggregations saved to curl_input.json' if aggregations else '')
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/update-config-and-run', methods=['POST'])
def update_config_and_run():
    """Update config and run main.py for each content source"""
    try:
        data = request.json
        
        # First update the config
        config_path = os.path.join(BASE_DIR, 'config.json')
        
        # Read existing config
        existing_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    existing_config = json.load(f)
            except:
                pass
        
        # Get multi-source config
        multi_source_config = data.get('MULTI_SOURCE_CONFIG', {})
        
        # Read existing curl_input for per-source aggregation updates
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
        
        # Read existing curl_input
        try:
            with open(curl_input_path, 'r') as f:
                curl_input = json.load(f)
        except:
            return jsonify({
                'success': False,
                'error': 'curl_input.json not found. Please parse a curl command first.'
            }), 400
        
        # Ensure body exists for aggregation updates
        if 'body' not in curl_input:
            curl_input['body'] = {}
        
        output_messages = []
        total_success = True
        
        output_messages.append(f"📝 Read {len(multi_source_config)} content source(s)")
        
        # Process each content source one by one
        source_downloads = {}
        source_temp_files = []  # Track temp files to combine later
        
        for source_name, source_config in multi_source_config.items():
            try:
                output_messages.append(f"\n{'='*60}")
                output_messages.append(f"Processing Content Source: {source_name}")
                output_messages.append(f"{'='*60}")
                
                # Get this source's aggregations
                source_aggregations = source_config.get('aggregations', [])
                
                # Update curl_input.json with this source's aggregations
                curl_input['body']['aggregations'] = source_aggregations
                with open(curl_input_path, 'w') as f:
                    json.dump(curl_input, f, indent=2)
                output_messages.append(f"📝 Updated curl_input.json with aggregations: {source_aggregations}")
                
                # Create a temporary config with only this source
                temp_config = {
                    'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
                    'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
                    'MULTI_SOURCE_CONFIG': {
                        source_name: {
                            'document_count': source_config.get('document_count', 50),
                            'url_field': source_config.get('url_field', 'href'),
                            'title_field': source_config.get('title_field', 'TitleToDisplay'),
                            'description_field': source_config.get('description_field', 'SummaryToDisplay'),
                            'title_highlight_field': source_config.get('title_highlight_field', ''),
                            'description_highlight_field': source_config.get('description_highlight_field', '')
                        }
                    }
                }
                
                # Update config.json with this source only
                with open(config_path, 'w') as f:
                    json.dump(temp_config, f, indent=2)
                
                output_messages.append(f"📝 Updated config for {source_name}")
                
                # Run main.py for this source - write output to a temp file so we don't persist it
                output_messages.append(f"🚀 Running main.py for {source_name}...")
                main_py_path = os.path.join(BASE_DIR, 'main.py')
                tf = tempfile.NamedTemporaryFile(delete=False, suffix='_output.csv')
                planned_output = tf.name
                tf.close()
                env = os.environ.copy()
                env['OUTPUT_CSV'] = planned_output
                env['PYTHONUNBUFFERED'] = '1'
                result = subprocess.run(
                    [sys.executable, '-u', main_py_path],
                    cwd=BASE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=400,  # 5 minute timeout
                    env=env
                )
                
                if result.returncode == 0:
                    output_messages.append(f"✅ SUCCESS for {source_name}:")
                    output_messages.append(result.stdout)
                    if result.stderr:
                        output_messages.append(f"Warnings: {result.stderr}")
                    # Track the temp file for later combination
                    if os.path.exists(planned_output):
                        source_temp_files.append((source_name, planned_output))
                        # Also store individual source token for backwards compatibility
                        try:
                            with open(planned_output, 'rb') as rf:
                                file_bytes = rf.read()
                            token = store_temp_file(file_bytes, f"{source_name}_output.csv")
                            source_downloads[source_name] = token
                        except Exception:
                            pass
                else:
                    output_messages.append(f"❌ ERROR for {source_name}:")
                    output_messages.append(f"Exit code: {result.returncode}")
                    output_messages.append(f"STDOUT: {result.stdout}")
                    output_messages.append(f"STDERR: {result.stderr}")
                    total_success = False
                    # Cleanup temp file on failure
                    try:
                        if os.path.exists(planned_output):
                            os.unlink(planned_output)
                    except Exception:
                        pass
                
            except subprocess.TimeoutExpired:
                output_messages.append(f"⏰ TIMEOUT for {source_name}: Process took longer than 5 minutes")
                total_success = False
            except Exception as e:
                output_messages.append(f"❌ EXCEPTION for {source_name}: {str(e)}")
                total_success = False
        
        # Combine all source outputs into a single CSV file
        combined_token = None
        combined_download_url = None
        if source_temp_files:
            try:
                import csv
                combined_tf = tempfile.NamedTemporaryFile(delete=False, suffix='_combined_output.csv', mode='w', newline='', encoding='utf-8')
                combined_writer = csv.writer(combined_tf)
                total_rows = 0
                
                # Always write the column headers first
                combined_writer.writerow(["Source Name", "Offset", "Title", "Description", "URL"])
                
                for source_name, temp_file_path in source_temp_files:
                    rows_added = 0
                    try:
                        with open(temp_file_path, 'r', newline='', encoding='utf-8') as rf:
                            reader = csv.reader(rf)
                            rows = list(reader)
                            if rows:
                                # Skip header row from source file (first row), write only data rows
                                # main.py always writes header: ["Source", "FromOffset", "Title", "Description", "Document URL"]
                                data_rows = rows[1:] if len(rows) > 1 else []
                                for row in data_rows:
                                    combined_writer.writerow(row)
                                    total_rows += 1
                                    rows_added += 1
                        output_messages.append(f"📄 Added {rows_added} rows from {source_name}")
                    except Exception as read_err:
                        output_messages.append(f"⚠️ Error reading temp file for {source_name}: {read_err}")
                    finally:
                        # Cleanup individual temp file
                        try:
                            if os.path.exists(temp_file_path):
                                os.unlink(temp_file_path)
                        except Exception:
                            pass
                
                combined_tf.close()
                
                # Store the combined file
                if os.path.exists(combined_tf.name):
                    with open(combined_tf.name, 'rb') as rf:
                        combined_bytes = rf.read()
                    combined_token = store_temp_file(combined_bytes, 'combined_output.csv')
                    combined_download_url = f"/api/download-temp?token={combined_token}"
                    output_messages.append(f"📊 Combined CSV created with {total_rows} total rows from all sources")
                    # Cleanup combined temp file
                    try:
                        os.unlink(combined_tf.name)
                    except Exception:
                        pass
            except Exception as combine_err:
                output_messages.append(f"⚠️ Error combining source outputs: {combine_err}")
        
        # Create final combined config (optional - keep the last single source or combine all)
        final_config = {
            'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
            'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
            'MULTI_SOURCE_CONFIG': multi_source_config
        }
        
        # Save final combined config
        with open(config_path, 'w') as f:
            json.dump(final_config, f, indent=2)
        
        output_messages.append(f"\n{'='*60}")
        output_messages.append("Final Summary")
        output_messages.append(f"{'='*60}")
        output_messages.append(f"📊 Processed {len(multi_source_config)} content sources")
        output_messages.append(f"📁 Final config saved with all sources")
        
        if total_success:
            output_messages.append("✅ All sources processed successfully!")
        else:
            output_messages.append("⚠️ Some sources had errors - check output above")
        
        return jsonify({
            'success': total_success,
            'message': 'Processing completed',
            'output': '\n'.join(output_messages),
            'download_tokens_by_source': source_downloads,
            'combined_download_token': combined_token,
            'combined_download_url': combined_download_url
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'output': f"Fatal error: {str(e)}"
        }), 500

@app.route('/api/stream/update-config-and-run', methods=['POST'])
def stream_update_config_and_run():
    """Stream update config and run main.py for each content source with real-time output"""
    try:
        data = request.json
        
        config_path = os.path.join(BASE_DIR, 'config.json')
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
        
        # Read existing curl_input
        try:
            with open(curl_input_path, 'r') as f:
                curl_input = json.load(f)
        except:
            return Response("ERROR: curl_input.json not found. Please parse a curl command first.\n", 
                          mimetype='text/plain', status=400)
        
        if 'body' not in curl_input:
            curl_input['body'] = {}
        
        multi_source_config = data.get('MULTI_SOURCE_CONFIG', {})
        
        def generate():
            source_temp_files = []
            total_success = True
            
            yield f"📝 Processing {len(multi_source_config)} content source(s)\n"
            yield f"{'='*60}\n"
            
            for source_name, source_config in multi_source_config.items():
                yield f"\n🔍 Processing Content Source: {source_name}\n"
                yield f"{'='*60}\n"
                
                try:
                    # Get this source's aggregations
                    source_aggregations = source_config.get('aggregations', [])
                    
                    # Update curl_input.json with this source's aggregations
                    curl_input['body']['aggregations'] = source_aggregations
                    with open(curl_input_path, 'w') as f:
                        json.dump(curl_input, f, indent=2)
                    yield f"📝 Updated aggregations: {source_aggregations}\n"
                    
                    # Create a temporary config with only this source
                    temp_config = {
                        'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
                        'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
                        'MULTI_SOURCE_CONFIG': {
                            source_name: {
                                'document_count': source_config.get('document_count', 50),
                                'url_field': source_config.get('url_field', 'href'),
                                'title_field': source_config.get('title_field', 'TitleToDisplay'),
                                'description_field': source_config.get('description_field', 'SummaryToDisplay'),
                                'title_highlight_field': source_config.get('title_highlight_field', ''),
                                'description_highlight_field': source_config.get('description_highlight_field', '')
                            }
                        }
                    }
                    
                    with open(config_path, 'w') as f:
                        json.dump(temp_config, f, indent=2)
                    
                    yield f"🚀 Running main.py for {source_name}...\n"
                    
                    # Run main.py with streaming output
                    main_py_path = os.path.join(BASE_DIR, 'main.py')
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix='_output.csv')
                    planned_output = tf.name
                    tf.close()
                    
                    env = os.environ.copy()
                    env['OUTPUT_CSV'] = planned_output
                    env['PYTHONUNBUFFERED'] = '1'
                    
                    proc = subprocess.Popen(
                        [sys.executable, '-u', main_py_path],
                        cwd=BASE_DIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        env=env
                    )
                    
                    import threading
                    import queue
                    
                    q = queue.Queue()
                    
                    def stream_reader():
                        try:
                            for line in iter(proc.stdout.readline, ''):
                                if line:
                                    q.put(line)
                        except Exception:
                            pass
                        finally:
                            try:
                                q.put(None)
                            except Exception:
                                pass
                                
                    reader_thread = threading.Thread(target=stream_reader, daemon=True)
                    reader_thread.start()
                    
                    # Stream output line by line
                    try:
                        while True:
                            try:
                                line = q.get(timeout=15.0)
                                if line is None:
                                    break
                                yield line if line.endswith('\n') else line + '\n'
                            except queue.Empty:
                                yield " KEEP_ALIVE_PING\n"
                    except (GeneratorExit, SystemExit):
                        try:
                            if proc.poll() is None:
                                proc.terminate()
                                proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        raise
                    
                    rc = proc.wait()
                    
                    if rc == 0:
                        yield f"\n✅ SUCCESS for {source_name}\n"
                        if os.path.exists(planned_output):
                            source_temp_files.append((source_name, planned_output))
                    else:
                        yield f"\n❌ ERROR for {source_name}: Exit code {rc}\n"
                        total_success = False
                        try:
                            if os.path.exists(planned_output):
                                os.unlink(planned_output)
                        except Exception:
                            pass
                    
                except Exception as e:
                    yield f"❌ EXCEPTION for {source_name}: {str(e)}\n"
                    total_success = False
            
            # Combine all source outputs into a single CSV file
            yield f"\n{'='*60}\n"
            yield "📊 Combining outputs...\n"
            
            combined_token = None
            if source_temp_files:
                try:
                    import csv
                    combined_tf = tempfile.NamedTemporaryFile(delete=False, suffix='_combined_output.csv', mode='w', newline='', encoding='utf-8')
                    combined_writer = csv.writer(combined_tf)
                    total_rows = 0
                    
                    # Always write the column headers first
                    combined_writer.writerow(["Source Name", "Offset", "Title", "Description", "URL"])
                    
                    for source_name, temp_file_path in source_temp_files:
                        rows_added = 0
                        try:
                            with open(temp_file_path, 'r', newline='', encoding='utf-8') as rf:
                                reader = csv.reader(rf)
                                rows = list(reader)
                                if rows:
                                    data_rows = rows[1:] if len(rows) > 1 else []
                                    for row in data_rows:
                                        combined_writer.writerow(row)
                                        total_rows += 1
                                        rows_added += 1
                            yield f"📄 Added {rows_added} rows from {source_name}\n"
                        except Exception as read_err:
                            yield f"⚠️ Error reading temp file for {source_name}: {read_err}\n"
                        finally:
                            try:
                                if os.path.exists(temp_file_path):
                                    os.unlink(temp_file_path)
                            except Exception:
                                pass
                    
                    combined_tf.close()
                    
                    if os.path.exists(combined_tf.name):
                        with open(combined_tf.name, 'rb') as rf:
                            combined_bytes = rf.read()
                        combined_token = store_temp_file(combined_bytes, 'combined_output.csv')
                        yield f"📊 Combined CSV created with {total_rows} total rows\n"
                        try:
                            os.unlink(combined_tf.name)
                        except Exception:
                            pass
                except Exception as combine_err:
                    yield f"⚠️ Error combining outputs: {combine_err}\n"
            
            # Save final combined config
            final_config = {
                'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
                'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
                'MULTI_SOURCE_CONFIG': multi_source_config
            }
            with open(config_path, 'w') as f:
                json.dump(final_config, f, indent=2)
            
            yield f"\n{'='*60}\n"
            yield "📋 Final Summary\n"
            yield f"{'='*60}\n"
            yield f"📊 Processed {len(multi_source_config)} content sources\n"
            
            if total_success:
                yield "✅ All sources processed successfully!\n"
            else:
                yield "⚠️ Some sources had errors - check output above\n"
            
            # Output download token for frontend to capture
            if combined_token:
                yield f"COMBINED_DOWNLOAD_TOKEN: {combined_token}\n"
            
            yield "STREAM_DONE\n"
        
        return Response(generate(), mimetype='text/plain')
    
    except Exception as e:
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)

@app.route('/api/run-main', methods=['POST'])
def run_main():
    """Run main.py with current configuration"""
    try:
        main_py_path = os.path.join(BASE_DIR, 'main.py')
        # Run main.py but direct output.csv to a temp file so it isn't persisted
        tf = tempfile.NamedTemporaryFile(delete=False, suffix='_output.csv')
        planned_output = tf.name
        tf.close()
        env = os.environ.copy()
        env['OUTPUT_CSV'] = planned_output
        env['PYTHONUNBUFFERED'] = '1'
        result = subprocess.run(
            [sys.executable, '-u', main_py_path],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            env=env
        )

        download_token = None
        download_url = None
        if result.returncode == 0:
            # If script wrote the output file, capture it in-memory and expose token
            try:
                if os.path.exists(planned_output):
                    with open(planned_output, 'rb') as rf:
                        file_bytes = rf.read()
                    download_token = store_temp_file(file_bytes, os.path.basename(planned_output))
                    download_url = f"/api/download-temp?token={download_token}"
            except Exception:
                pass
            try:
                if os.path.exists(planned_output):
                    os.unlink(planned_output)
            except Exception:
                pass
            return jsonify({
                'success': True,
                'output': result.stdout,
                'stderr': result.stderr,
                'output_csv_token': download_token,
                'download_url': download_url
            })
        else:
            try:
                if os.path.exists(planned_output):
                    os.unlink(planned_output)
            except Exception:
                pass
            return jsonify({
                'success': False,
                'error': f'main.py exited with code {result.returncode}',
                'output': result.stdout,
                'stderr': result.stderr
            })
    
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': 'Process timed out (5 minutes)',
            'output': 'The process was terminated due to timeout'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/get-config', methods=['GET'])
def get_config():
    """Get current configuration"""
    try:
        config_path = os.path.join(BASE_DIR, 'config.json')
        
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
            return jsonify({
                'success': True,
                'config': config
            })
        else:
            return jsonify({
                'success': False,
                'error': 'config.json not found'
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/get-curl-input', methods=['GET'])
def get_curl_input():
    """Get current curl input configuration"""
    try:
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
        
        if os.path.exists(curl_input_path):
            with open(curl_input_path, 'r') as f:
                curl_input = json.load(f)
            return jsonify({
                'success': True,
                'curl_input': curl_input
            })
        else:
            return jsonify({
                'success': False,
                'error': 'curl_input.json not found'
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/generate-queries-distribution', methods=['POST'])
def generate_queries_distribution():
    """Run generate_search_queries_distribution.py in non-interactive mode.
    Expects optional JSON: { "input_csv": "path", "weights": "0.35,0.45,0.20", "prompt_types": ["easy","medium",...] }
    Uses output.csv in project root if input not provided.
    """
    try:
        data = request.json or {}
        input_csv = data.get('input_csv') or os.path.join(BASE_DIR, 'output.csv')
        weights = data.get('weights')  # string like "0.35,0.45,0.20" or None
        prompt_types = data.get('prompt_types')  # list or comma string of prompt types (optional)
        script_path = os.path.join(BASE_DIR, 'scripts', 'generate_search_queries_distribution.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Distribution script not found'}), 400
        # Use a temporary file (OS temp dir) so results are not persisted in repo folders
        tf = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        planned_output = tf.name
        tf.close()
        cmd = [sys.executable, script_path, f'--input={input_csv}', '--non-interactive', '--deterministic', f'--output={planned_output}']
        if weights:
            cmd.append(f'--weights={weights}')
        if prompt_types:
            if isinstance(prompt_types, list):
                joined = ','.join([str(p).strip() for p in prompt_types if str(p).strip()])
            else:
                joined = str(prompt_types).strip()
            if joined:
                cmd.append(f'--prompt-types={joined}')
        result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=600)
        success = result.returncode == 0
        output_csv = None
        download_token = None
        download_url = None
        if success:
            import re
            # Prefer explicit machine-readable line
            m = re.search(r"OUTPUT_CSV:\s*(.*\.csv)", result.stdout)
            if m:
                candidate = m.group(1).strip()
                if os.path.exists(candidate):
                    output_csv = candidate
            if not output_csv:
                # Secondary patterns
                for pat in [r"Saved results to:?\s*(.*\.csv)"]:
                    m2 = re.search(pat, result.stdout)
                    if m2:
                        candidate = m2.group(1).strip().strip('\n\r "')
                        if os.path.exists(candidate):
                            output_csv = candidate
                            break
            if not output_csv:
                # Fallback to most recent file
                # We wrote to a temp path (planned_output), prefer it
                if os.path.exists(planned_output):
                    output_csv = planned_output
            # If we have an output file path, read and store it in-memory and return a token
            if output_csv and os.path.exists(output_csv):
                try:
                    with open(output_csv, 'rb') as rf:
                        file_bytes = rf.read()
                    fname = os.path.basename(output_csv)
                    download_token = store_temp_file(file_bytes, fname)
                    download_url = f"/api/download-temp?token={download_token}"
                except Exception:
                    pass
            # Clean up temp file if it exists on disk
            try:
                if os.path.exists(planned_output):
                    os.unlink(planned_output)
            except Exception:
                pass
        return jsonify({
            'success': success,
            'command': ' '.join(cmd),
            'stdout': result.stdout,
            'stderr': result.stderr,
            'exit_code': result.returncode,
            'output_csv_token': download_token,
            'download_url': download_url
        }), (200 if success else 500)
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Query distribution generation timed out (10m)'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stream/generate-queries-distribution', methods=['POST'])
def stream_generate_queries_distribution():
    """Stream distribution query generation output line-by-line."""
    try:
        data = request.json or {}
        # Determine input CSV: prefer explicit path; else accept uploaded token (input_token) which we materialize
        input_csv = data.get('input_csv')
        created_temp_input = None
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')
        weights = data.get('weights')
        prompt_types = data.get('prompt_types')  # list or comma separated string
        
        # API Key configuration - ONLY from frontend, NO environment variable fallback
        llm_provider = data.get('llm_provider', 'openai')
        openai_key = data.get('openai_api_key', '')
        gemini_key = data.get('gemini_api_key', '')
        model = data.get('model', '')
        custom_prompts = data.get('custom_prompts', {})  # Get custom prompts from frontend
        
        # Validate API key is provided from frontend
        if llm_provider == 'openai' and not openai_key:
            return Response("ERROR: OpenAI API key is required. Please enter your API key in the frontend UI.\n", 
                          mimetype='text/plain', status=400)
        elif llm_provider == 'gemini' and not gemini_key:
            return Response("ERROR: Gemini API key is required. Please enter your API key in the frontend UI.\n", 
                          mimetype='text/plain', status=400)
        
        # Save custom prompts to temporary JSON file if provided
        custom_prompts_file = None
        if custom_prompts:
            import json
            temp_fd, custom_prompts_file = tempfile.mkstemp(suffix='.json', prefix='custom_prompts_')
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(custom_prompts, f)
        
        # Use a temporary file path so we don't persist results to repo folders
        tf = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        planned_output = tf.name
        tf.close()
        script_path = os.path.join(BASE_DIR, 'scripts', 'generate_search_queries_distribution.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Distribution script not found'}), 400
        cmd = [sys.executable, script_path, f'--input={input_csv}', '--non-interactive', '--deterministic', f'--output={planned_output}']
        if weights:
            cmd.append(f'--weights={weights}')
        if prompt_types:
            if isinstance(prompt_types, list):
                joined = ','.join([str(p).strip() for p in prompt_types if str(p).strip()])
            else:
                joined = str(prompt_types).strip()
            if joined:
                cmd.append(f'--prompt-types={joined}')
        
        # Add API key arguments
        cmd.append(f'--llm-provider={llm_provider}')
        if openai_key:
            cmd.append(f'--openai-api-key={openai_key}')
        if gemini_key:
            cmd.append(f'--gemini-api-key={gemini_key}')
        if model:
            cmd.append(f'--model={model}')
        if custom_prompts_file:
            cmd.append(f'--custom-prompts={custom_prompts_file}')
        
        # Add column selection parameters
        title_column = data.get('title_column')
        description_column = data.get('description_column')
        url_column = data.get('url_column')
        if title_column:
            cmd.append(f'--title-column={title_column}')
        if description_column:
            cmd.append(f'--description-column={description_column}')
        if url_column:
            cmd.append(f'--url-column={url_column}')

        def generate():
            yield f"COMMAND: {' '.join([c if not c.startswith('--openai-api-key=') and not c.startswith('--gemini-api-key=') else c.split('=')[0]+'=***' for c in cmd])}\n"
            
            # Set up environment with API keys
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            if openai_key:
                env['OPENAI_API_KEY'] = openai_key
            if gemini_key:
                env['GEMINI_API_KEY'] = gemini_key
            
            try:
                proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                        text=True, bufsize=1, universal_newlines=True, env=env)
                
                import threading
                import queue
                q = queue.Queue()
                def stream_reader():
                    try:
                        for line in iter(proc.stdout.readline, ''):
                            if line:
                                q.put(line)
                    except Exception:
                        pass
                    finally:
                        try:
                            q.put(None)
                        except Exception:
                            pass
                reader_thread = threading.Thread(target=stream_reader, daemon=True)
                reader_thread.start()
                
                output_csv = None
                while True:
                    try:
                        line = q.get(timeout=15.0)
                        if line is None:
                            break
                        if 'OUTPUT_CSV:' in line and output_csv is None:
                            # capture file path
                            parts = line.strip().split('OUTPUT_CSV:',1)
                            if len(parts)==2:
                                candidate = parts[1].strip()
                                if os.path.exists(candidate):
                                    output_csv = candidate
                        yield line if line.endswith('\n') else line + '\n'
                    except queue.Empty:
                        yield " KEEP_ALIVE_PING\n"
                rc = proc.wait()
                # If the script wrote to our planned_output temp file, capture it and create a download token
                if os.path.exists(planned_output):
                    try:
                        with open(planned_output, 'rb') as rf:
                            file_bytes = rf.read()
                        token = store_temp_file(file_bytes, os.path.basename(planned_output))
                        yield f"TEMP_DOWNLOAD_TOKEN: {token}\n"
                    except Exception:
                        pass
                if output_csv and os.path.exists(output_csv):
                    yield f"OUTPUT_CSV_FINAL: {output_csv}\n"
                yield f"EXIT_CODE: {rc}\n"
                yield "STREAM_DONE\n"
            finally:
                # Clean up temporary custom prompts file
                if custom_prompts_file and os.path.exists(custom_prompts_file):
                    try:
                        os.unlink(custom_prompts_file)
                    except:
                        pass
                # Remove planned_output temp file if present
                try:
                    if os.path.exists(planned_output):
                        os.unlink(planned_output)
                except Exception:
                    pass
                # Remove any temp input file we materialized from token
                try:
                    if created_temp_input and os.path.exists(created_temp_input):
                        os.unlink(created_temp_input)
                except Exception:
                    pass
        return Response(generate(), mimetype='text/plain')
    except Exception as e:
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)

@app.route('/api/generate-queries-single', methods=['POST'])
def generate_queries_single():
    """Run generate_search_queries.py with a single prompt type in non-interactive mode.
    Expects JSON: { "input_csv": "path(optional)", "input_token": "token(from upload)", "prompt_type": "medium" }
    """
    try:
        data = request.json or {}
        input_csv = data.get('input_csv')
        created_temp_input = None
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')
        prompt_type = (data.get('prompt_type') or 'medium').lower()
        script_path = os.path.join(BASE_DIR, 'scripts', 'generate_search_queries.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Single prompt script not found'}), 400
        # Use temporary file path so outputs are not left in repo folders
        tf = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        planned_output = tf.name
        tf.close()
        cmd = [sys.executable, script_path, f'--input={input_csv}', f'--prompt-type={prompt_type}', '--non-interactive', f'--output={planned_output}']
        result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=600)
        success = result.returncode == 0
        output_csv = None
        download_token = None
        download_url = None
        if success:
            import re
            m = re.search(r"OUTPUT_CSV:\s*(.*\.csv)", result.stdout)
            if m:
                candidate = m.group(1).strip()
                if os.path.exists(candidate):
                    output_csv = candidate
            if not output_csv:
                for pat in [r"Expanded CSV saved to:\s*(.*\.csv)", r"Optimized Output CSV:\s*(.*\.csv)"]:
                    m2 = re.search(pat, result.stdout)
                    if m2:
                        candidate = m2.group(1).strip().strip('\n\r "')
                        if os.path.exists(candidate):
                            output_csv = candidate
                            break
            if not output_csv:
                qo_dir = os.path.join(BASE_DIR, 'query_outputs', 'generated_queries')
                if os.path.isdir(qo_dir):
                    q_files = [os.path.join(qo_dir, f) for f in os.listdir(qo_dir) if f.startswith(f'queries_{prompt_type}_') and f.endswith('.csv')]
                    if q_files:
                        q_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        output_csv = q_files[0]
            if not output_csv and os.path.exists(planned_output):
                output_csv = planned_output
            if output_csv and os.path.exists(output_csv):
                try:
                    with open(output_csv, 'rb') as rf:
                        data_bytes = rf.read()
                    fname = os.path.basename(output_csv)
                    download_token = store_temp_file(data_bytes, fname)
                    download_url = f"/api/download-temp?token={download_token}"
                except Exception:
                    pass
        try:
            if os.path.exists(planned_output):
                os.unlink(planned_output)
        except Exception:
            pass
        try:
            if created_temp_input and os.path.exists(created_temp_input):
                os.unlink(created_temp_input)
        except Exception:
            pass
        return jsonify({
            'success': success,
            'command': ' '.join(cmd),
            'stdout': result.stdout,
            'stderr': result.stderr,
            'exit_code': result.returncode,
            'output_csv_token': download_token,
            'download_url': download_url
        }), (200 if success else 500)
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Single prompt query generation timed out (10m)'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stream/generate-queries-single', methods=['POST'])
def stream_generate_queries_single():
    """Stream single prompt query generation output."""
    try:
        data = request.json or {}
        input_csv = data.get('input_csv')
        created_temp_input = None
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')
        prompt_type = (data.get('prompt_type') or 'medium').lower()

        llm_provider = data.get('llm_provider', 'openai')
        openai_key = data.get('openai_api_key', '')
        gemini_key = data.get('gemini_api_key', '')
        model = data.get('model', '')
        custom_prompts = data.get('custom_prompts', {})
        title_column = data.get('title_column', 'Title')
        description_column = data.get('description_column', 'Description')
        url_column = data.get('url_column', 'Document URL')

        if llm_provider == 'openai' and not openai_key:
            return Response("ERROR: OpenAI API key is required. Please enter your API key in the frontend UI.\n", mimetype='text/plain', status=400)
        if llm_provider == 'gemini' and not gemini_key:
            return Response("ERROR: Gemini API key is required. Please enter your API key in the frontend UI.\n", mimetype='text/plain', status=400)

        custom_prompts_file = None
        if custom_prompts:
            temp_fd, custom_prompts_file = tempfile.mkstemp(suffix='.json', prefix='custom_prompts_')
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(custom_prompts, f)

        tf = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        planned_output = tf.name
        tf.close()

        script_path = os.path.join(BASE_DIR, 'scripts', 'generate_search_queries.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Single prompt script not found'}), 400

        cmd = [sys.executable, script_path, f'--input={input_csv}', f'--prompt-type={prompt_type}', '--non-interactive', f'--output={planned_output}']
        cmd.append(f'--llm-provider={llm_provider}')
        if openai_key:
            cmd.append(f'--openai-api-key={openai_key}')
        if gemini_key:
            cmd.append(f'--gemini-api-key={gemini_key}')
        if model:
            cmd.append(f'--model={model}')
        if custom_prompts_file:
            cmd.append(f'--custom-prompts={custom_prompts_file}')
        if title_column:
            cmd.append(f'--title-column={title_column}')
        if description_column:
            cmd.append(f'--description-column={description_column}')
        if url_column:
            cmd.append(f'--url-column={url_column}')

        def generate():
            yield f"USING_INPUT_CSV: {input_csv}\n"
            yield f"COMMAND: {' '.join([c if not c.startswith('--openai-api-key=') and not c.startswith('--gemini-api-key=') else c.split('=')[0]+'=***' for c in cmd])}\n"
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            if openai_key:
                env['OPENAI_API_KEY'] = openai_key
            if gemini_key:
                env['GEMINI_API_KEY'] = gemini_key

            try:
                proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True, env=env)
                
                import threading
                import queue
                q = queue.Queue()
                def stream_reader():
                    try:
                        for line in iter(proc.stdout.readline, ''):
                            if line:
                                q.put(line)
                    except Exception:
                        pass
                    finally:
                        try:
                            q.put(None)
                        except Exception:
                            pass
                reader_thread = threading.Thread(target=stream_reader, daemon=True)
                reader_thread.start()
                
                output_csv = None
                while True:
                    try:
                        line = q.get(timeout=15.0)
                        if line is None:
                            break
                        if 'OUTPUT_CSV:' in line and output_csv is None:
                            parts = line.strip().split('OUTPUT_CSV:',1)
                            if len(parts) == 2:
                                candidate = parts[1].strip()
                                if os.path.exists(candidate):
                                    output_csv = candidate
                        yield line if line.endswith('\n') else line + '\n'
                    except queue.Empty:
                        yield " KEEP_ALIVE_PING\n"
                rc = proc.wait()
                # If the script wrote to our planned_output temp file, capture it and create a download token
                if os.path.exists(planned_output):
                    try:
                        with open(planned_output, 'rb') as rf:
                            data_bytes = rf.read()
                        token = store_temp_file(data_bytes, os.path.basename(planned_output))
                        yield f"TEMP_DOWNLOAD_TOKEN: {token}\n"
                    except Exception:
                        pass
                if output_csv and os.path.exists(output_csv):
                    yield f"OUTPUT_CSV_FINAL: {output_csv}\n"
                yield f"EXIT_CODE: {rc}\n"
                yield "STREAM_DONE\n"
            finally:
                if custom_prompts_file and os.path.exists(custom_prompts_file):
                    try:
                        os.unlink(custom_prompts_file)
                    except:
                        pass
                try:
                    if os.path.exists(planned_output):
                        os.unlink(planned_output)
                except Exception:
                    pass
                try:
                    if created_temp_input and os.path.exists(created_temp_input):
                        os.unlink(created_temp_input)
                except Exception:
                    pass

        return Response(generate(), mimetype='text/plain')
    except Exception as e:
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)

@app.route('/api/run-search-evaluation', methods=['POST'])
def run_search_evaluation():
    """Run the search evaluation script using a queries CSV.
    Ensures curl_input.json has empty aggregations before running.
    JSON body: { "input_csv": "path(optional)", "output_csv": "path(optional)", "delay": 0.5, "analyze": true }
    Defaults: input_csv -> latest queries_* file or output.csv derivative.
    """
    logger.info("[Search Evaluation] Request received (non-stream)")
    try:
        data = request.json or {}
        # Ensure aggregations cleared
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
        if os.path.exists(curl_input_path):
            try:
                with open(curl_input_path, 'r') as f:
                    curl_obj = json.load(f)
            except Exception:
                curl_obj = {}
            body = curl_obj.get('body') if isinstance(curl_obj.get('body'), dict) else {}
            body['aggregations'] = []
            curl_obj['body'] = body
            with open(curl_input_path, 'w') as f:
                json.dump(curl_obj, f, indent=2)

        # Determine input CSV: explicit path preferred; else try uploaded token; else find latest queries_*; else fallback
        input_csv = data.get('input_csv')
        created_temp_input = None
        # uploaded token path (in-memory) support
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        # If still none, pick latest queries_* file in query_outputs
        if not input_csv:
            qo_dir = os.path.join(BASE_DIR, 'query_outputs')
            latest = None
            if os.path.isdir(qo_dir):
                for fname in os.listdir(qo_dir):
                    if fname.startswith('queries_') and fname.endswith('.csv'):
                        full = os.path.join(qo_dir, fname)
                        if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                            latest = full
            if latest:
                input_csv = latest
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')

        output_csv = data.get('output_csv')
        # If not provided, use a temporary output file so we don't persist results in repo
        if not output_csv:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix='_search_results.csv')
            output_csv = tf.name
            tf.close()

        delay = float(data.get('delay', 0.5))
        analyze = bool(data.get('analyze', True))
        has_ground_truth = bool(data.get('has_ground_truth', True))
        using_upload = bool(data.get('input_token') or data.get('uploaded_token'))
        query_column = (data.get('query_column') or '').strip()
        title_column = (data.get('title_column') or '').strip() or None
        url_column = (data.get('url_column') or '').strip() or None

        if using_upload:
            if not query_column:
                return jsonify({'success': False, 'error': 'Select a Query column for the uploaded sheet (query_column).'}), 400
            if has_ground_truth and (not title_column or not url_column):
                return jsonify({'success': False, 'error': 'Ground truth mode requires Expected Title and Expected URL columns for uploaded sheets.'}), 400

        script_path = os.path.join(BASE_DIR, 'scripts', 'run_search_evaluation-verizon.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Search evaluation script not found'}), 400

        cmd = [sys.executable, script_path, f'--input={input_csv}', f'--output={output_csv}', f'--delay={delay}']
        if not analyze:
            cmd.append('--no-analyze')
        if query_column:
            cmd.append(f'--query-column={query_column}')
        if has_ground_truth:
            if title_column:
                cmd.append(f'--title-column={title_column}')
            if url_column:
                cmd.append(f'--url-column={url_column}')
        else:
            cmd.append('--no-ground-truth')

        logger.info("[Search Evaluation] Starting: input=%s, delay=%s, analyze=%s, has_ground_truth=%s", input_csv, delay, analyze, has_ground_truth)
        result = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=1200)
        success = result.returncode == 0
        if success:
            logger.info("[Search Evaluation] Completed successfully (exit_code=0)")
        else:
            logger.warning("[Search Evaluation] Failed with exit_code=%s stderr=%s", result.returncode, (result.stderr or '')[:500])
        download_token = None
        download_url = None
        # If script wrote to output_csv, capture and store it in-memory
        try:
            if os.path.exists(output_csv):
                with open(output_csv, 'rb') as rf:
                    file_bytes = rf.read()
                fname = os.path.basename(output_csv)
                download_token = store_temp_file(file_bytes, fname)
                download_url = f"/api/download-temp?token={download_token}"
        except Exception:
            pass
        # Clean up temp files: output and any temp input we materialized
        try:
            if os.path.exists(output_csv):
                os.unlink(output_csv)
        except Exception:
            pass
        try:
            if created_temp_input and os.path.exists(created_temp_input):
                os.unlink(created_temp_input)
        except Exception:
            pass
        return jsonify({
            'success': success,
            'command': ' '.join(cmd),
            'stdout': result.stdout,
            'stderr': result.stderr,
            'exit_code': result.returncode,
            'input_csv': input_csv,
            'output_csv_token': download_token,
            'download_url': download_url,
            'aggregations_cleared': True
        }), (200 if success else 500)
    except subprocess.TimeoutExpired:
        logger.error("[Search Evaluation] Timed out (20m)")
        return jsonify({'success': False, 'error': 'Search evaluation timed out (20m)'}), 500
    except Exception as e:
        logger.exception("[Search Evaluation] Error: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stream/run-search-evaluation', methods=['POST'])
def stream_run_search_evaluation():
    """Stream search evaluation output (stdout and stderr merged)."""
    logger.info("[Search Evaluation] Request received (stream)")
    try:
        data = request.json or {}
        # Clear aggregations like non-stream version
        curl_input_path = os.path.join(BASE_DIR, 'curl_input.json')
        if os.path.exists(curl_input_path):
            try:
                with open(curl_input_path, 'r') as f:
                    curl_obj = json.load(f)
            except Exception:
                curl_obj = {}
            body = curl_obj.get('body') if isinstance(curl_obj.get('body'), dict) else {}
            body['aggregations'] = []
            curl_obj['body'] = body
            with open(curl_input_path, 'w') as f:
                json.dump(curl_obj, f, indent=2)

        input_csv = data.get('input_csv')
        created_temp_input = None
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        # If still none, pick latest generated queries file
        if not input_csv:
            qo_dir = os.path.join(BASE_DIR, 'query_outputs')
            latest = None
            if os.path.isdir(qo_dir):
                for fname in os.listdir(qo_dir):
                    if fname.startswith('queries_') and fname.endswith('.csv'):
                        full = os.path.join(qo_dir, fname)
                        if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                            latest = full
                gq_dir = os.path.join(qo_dir, 'generated_queries')
                if os.path.isdir(gq_dir):
                    for fname in os.listdir(gq_dir):
                        if fname.endswith('.csv') and (fname.startswith('queries_') or fname.startswith('Distributed_queries_')):
                            full = os.path.join(gq_dir, fname)
                            if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                                latest = full
            if latest:
                input_csv = latest
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')

        output_csv = data.get('output_csv')
        # Use a temporary results file if caller didn't provide explicit path
        if not output_csv:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix='_search_results.csv')
            output_csv = tf.name
            tf.close()

        delay = float(data.get('delay', 0.5))
        analyze = bool(data.get('analyze', True))
        has_ground_truth = bool(data.get('has_ground_truth', True))
        using_upload = bool(data.get('input_token') or data.get('uploaded_token'))
        query_column = (data.get('query_column') or '').strip()
        title_column = (data.get('title_column') or '').strip() or None
        url_column = (data.get('url_column') or '').strip() or None

        if using_upload:
            if not query_column:
                return jsonify({'success': False, 'error': 'Select a Query column for the uploaded sheet (query_column).'}), 400
            if has_ground_truth and (not title_column or not url_column):
                return jsonify({'success': False, 'error': 'Ground truth mode requires Expected Title and Expected URL columns for uploaded sheets.'}), 400

        script_path = os.path.join(BASE_DIR, 'scripts', 'run_search_evaluation-verizon.py')
        if not os.path.exists(script_path):
            return jsonify({'success': False, 'error': 'Search evaluation script not found'}), 400

        cmd = [sys.executable, script_path, f'--input={input_csv}', f'--output={output_csv}', f'--delay={delay}']
        if not analyze:
            cmd.append('--no-analyze')

        if query_column:
            cmd.append(f'--query-column={query_column}')
        if has_ground_truth:
            if title_column:
                cmd.append(f'--title-column={title_column}')
            if url_column:
                cmd.append(f'--url-column={url_column}')
        else:
            cmd.append('--no-ground-truth')

        def generate():
            # Inform caller what input/command will be used
            logger.info("[Search Evaluation] Stream started: input_csv=%s, delay=%s, cmd=%s", input_csv, delay, ' '.join(cmd))
            yield f"USING_INPUT_CSV: {input_csv}\n"
            if not os.path.exists(input_csv):
                logger.error("[Search Evaluation] Input file not found: %s", input_csv)
                yield f"❌ Uploaded/selected input file not found: {input_csv}\n"
                yield "EXIT_CODE: 1\nSTREAM_DONE\n"
                return
            yield f"COMMAND: {' '.join(cmd)}\n"

            eval_env = os.environ.copy()
            eval_env['PYTHONUNBUFFERED'] = '1'
            logger.info("[Search Evaluation] Streaming Subprocess Starting: %s", ' '.join(cmd))
            proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True, env=eval_env)
            logger.info("[Search Evaluation] Subprocess PID: %s", proc.pid)
            
            import queue
            import threading
            
            q = queue.Queue()
            
            def stream_reader():
                try:
                    # Read line by line until EOF
                    for line in iter(proc.stdout.readline, ''):
                        if line:
                            q.put(line)
                except Exception as e:
                    logger.error(f"[Search Evaluation] stream_reader exception: {e}")
                finally:
                    try:
                        q.put(None)
                    except Exception:
                        pass
            
            reader_thread = threading.Thread(target=stream_reader, daemon=True)
            reader_thread.start()
            
            try:
                # Read and stream lines as they arrive. If the generator is closed
                # (client disconnected) or Gunicorn aborts the worker (SystemExit),
                # ensure the subprocess is terminated so it doesn't keep running.
                try:
                    while True:
                        try:
                            # 15s timeout to send keep-alive, preventing proxy (e.g., NGINX/ALB) 504 timeouts
                            line = q.get(timeout=15.0)
                            if line is None:
                                break  # EOF reached
                            logger.info("[Search Evaluation] %s", line.rstrip())
                            yield line if line.endswith('\n') else line + '\n'
                        except queue.Empty:
                            # Keep-alive heartbeat (invisible or ignored by frontend's `value` processing)
                            logger.info("[Search Evaluation] Keeping stream alive (15s idle)... PID: %s", proc.pid)
                            yield " KEEP_ALIVE_PING\n"
                except (GeneratorExit, SystemExit):
                    logger.warning("[Search Evaluation] Stream Interrupted (GeneratorExit/SystemExit). Terminating PID %s", proc.pid)
                    # Client disconnected or worker abort; terminate child process
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            proc.wait(timeout=5)
                    except Exception as te:
                        logger.error("[Search Evaluation] Error terminating process: %s", te)
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    # Re-raise so caller/worker can handle the abort as expected
                    raise
                except Exception as e:
                    # Streaming error; try to stop subprocess and report the error
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    yield f"ERROR: {str(e)}\n"

                # Normal termination path
                rc = proc.wait()
                # Check for Excel output first (xlsx), then CSV
                output_xlsx = output_csv.replace('.csv', '.xlsx') if output_csv.endswith('.csv') else output_csv + '.xlsx'
                xlsx_token = None
                csv_token = None
                
                # Store Excel file if it exists
                if os.path.exists(output_xlsx):
                    try:
                        with open(output_xlsx, 'rb') as rf:
                            file_bytes = rf.read()
                        xlsx_token = store_temp_file(file_bytes, os.path.basename(output_xlsx))
                        yield f"TEMP_DOWNLOAD_TOKEN_XLSX: {xlsx_token}\n"
                    except Exception:
                        pass
                
                # Also store CSV file if it exists
                if os.path.exists(output_csv):
                    try:
                        with open(output_csv, 'rb') as rf:
                            file_bytes = rf.read()
                        csv_token = store_temp_file(file_bytes, os.path.basename(output_csv))
                        yield f"TEMP_DOWNLOAD_TOKEN: {csv_token}\n"
                    except Exception:
                        pass
                
                yield f"OUTPUT_CSV_FINAL: {output_csv}\n" if os.path.exists(output_csv) else ''
                yield f"OUTPUT_XLSX_FINAL: {output_xlsx}\n" if os.path.exists(output_xlsx) else ''
                yield f"EXIT_CODE: {rc}\n"
                yield "STREAM_DONE\n"
                logger.info("[Search Evaluation] Stream finished for PID %s: exit_code=%s", proc.pid, rc)
                if rc != 0:
                    logger.warning("[Search Evaluation] Subprocess finished with non-zero exit code: %s", rc)
            finally:
                # Always attempt to cleanup the subprocess and any temp files. Do not
                # yield in this block since generator may be closed; just perform cleanup.
                try:
                    if proc.poll() is None:
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                except Exception:
                    pass

                # cleanup temp output file
                try:
                    if os.path.exists(output_csv):
                        os.unlink(output_csv)
                except Exception:
                    pass
                # Remove any temp input file we materialized from token
                try:
                    if created_temp_input and os.path.exists(created_temp_input):
                        os.unlink(created_temp_input)
                except Exception:
                    pass
        return Response(generate(), mimetype='text/plain')
    except Exception as e:
        logger.exception("[Search Evaluation] Stream error: %s", e)
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)

@app.route('/api/download-result', methods=['GET'])
def download_result():
    """Download a CSV result by filename; searches query_outputs and generated subfolder."""
    try:
        filename = request.args.get('file')
        if not filename:
            return jsonify({'success': False, 'error': 'Missing file parameter'}), 400
        if os.path.basename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name'}), 400
        search_dirs = [
            os.path.join(BASE_DIR, 'query_outputs'),
            os.path.join(BASE_DIR, 'query_outputs', 'generated_queries'),
            os.path.join(BASE_DIR, 'query_outputs', 'uploads'),
            BASE_DIR  # last resort (e.g., output_search_results.csv at project root)
        ]
        for d in search_dirs:
            target_path = os.path.join(d, filename)
            if os.path.exists(target_path):
                return send_from_directory(d, filename, as_attachment=True)
        return jsonify({'success': False, 'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/download-output-csv', methods=['GET'])
def download_output_csv():
    """Download the primary output.csv generated by main.py (project root)."""
    try:
        target = os.path.join(BASE_DIR, 'output.csv')
        if os.path.exists(target):
            return send_from_directory(BASE_DIR, 'output.csv', as_attachment=True)
        return jsonify({'success': False, 'error': 'output.csv not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/download-temp', methods=['GET'])
def download_temp():
    """Download a server-held temp result by token."""
    try:
        token = request.args.get('token')
        if not token:
            return jsonify({'success': False, 'error': 'Missing token'}), 400
        meta = _load_token_meta(token)
        if not meta:
            return jsonify({'success': False, 'error': 'Token not found or expired'}), 404
        return send_file(
            meta['path'],
            mimetype=_mimetype_for_download(meta.get('filename', '')),
            as_attachment=True,
            download_name=meta['filename'],
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/temp-file-columns', methods=['GET'])
def temp_file_columns():
    """Read column headers from a temp file token (CSV/Excel) without persisting to repo."""
    try:
        token = request.args.get('token')
        if not token:
            return jsonify({'success': False, 'error': 'Missing token'}), 400

        meta = _load_token_meta(token)
        if not meta:
            return jsonify({'success': False, 'error': 'Token not found or expired'}), 404

        file_path = meta.get('path')
        filename = meta.get('filename') or 'output.csv'
        if not file_path or not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'File not found for token'}), 404

        df = read_tabular_headers_only(file_path, filename)
        columns = df.columns.tolist()

        return jsonify({
            'success': True,
            'filename': filename,
            'columns': columns
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Browser-facing URL for LLM Comparator (used in /api/config for frontend nav)
LLM_COMPARATOR_URL = os.environ.get('LLM_COMPARATOR_URL', 'http://localhost:8005')
PORTAL_URL = os.environ.get('PORTAL_URL', '/')
# Internal HTTP URL for server-to-server calls (urllib). Falls back to
# LLM_COMPARATOR_URL when not set (standalone mode).
_LLM_COMPARATOR_INTERNAL_URL = os.environ.get('LLM_COMPARATOR_INTERNAL_URL', LLM_COMPARATOR_URL)


def _llm_comparator_404_message(http_error, base_url):
    """When LLM Comparator returns 404, check /api/health to give a clearer message."""
    from urllib.request import urlopen
    from urllib.error import HTTPError as UrllibHTTPError, URLError as UrllibURLError
    try:
        health_url = '{}/api/health'.format(base_url.rstrip('/'))
        r = urlopen(health_url, timeout=5)
        if r.getcode() == 200:
            return (
                'Web Scraper endpoint not found (404) but LLM Comparator is running at {}. '
                'Restart the LLM Comparator app so it loads the latest code (with /api/google-only-stream), '
                'or use "Google Search API" and enter your API key & CSE ID.'
            ).format(base_url)
    except Exception:
        pass
    return (
        'LLM Comparator returned 404 for Web Scraper. If the app is running at {}, '
        'restart it to load the latest code. Otherwise use "Google Search API" and enter your API key & CSE ID.'
    ).format(base_url)


@app.route('/api/run-google-search', methods=['POST'])
def run_google_search():
    """
    Take a token for the generated-queries CSV, extract query column, call LLM Comparator
    /api/google-only to run Google search for each query, then store the result and return
    a download token for the Google results CSV.
    """
    from urllib.request import Request as UrlRequest, urlopen
    from urllib.error import HTTPError, URLError
    logger.info("[Google Search Result] Request received (Web Scraper / google-only)")
    try:
        data = request.get_json() or {}
        token = data.get('token')
        preferred_query_column = (data.get('query_column') or '').strip() or None
        if not token:
            return jsonify({'success': False, 'error': 'Missing token'}), 400
        queries, query_column, err = extract_queries_from_token(token, preferred_query_column)
        if err:
            status = 404 if 'expired' in err else 400
            return jsonify({'success': False, 'error': err}), status
        logger.info("[Google Search Result] Starting: queries_count=%s", len(queries))
        url = '{}/api/google-only'.format(_LLM_COMPARATOR_INTERNAL_URL.rstrip('/'))
        payload = json.dumps({'queries': queries, 'max_results': 10}).encode('utf-8')
        req_obj = UrlRequest(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
        resp = urlopen(req_obj, timeout=600)
        csv_content = resp.read()
        download_token = store_temp_file(csv_content, 'google_search_results.csv', ttl=1800)
        logger.info("[Google Search Result] Completed successfully: queries_count=%s", len(queries))
        return jsonify({
            'success': True,
            'download_token': download_token,
            'filename': 'google_search_results.csv',
            'queries_count': len(queries),
        })
    except HTTPError as e:
        logger.warning("[Google Search Result] LLM Comparator HTTP error: %s", e.code)
        return jsonify({'success': False, 'error': 'LLM Comparator error: {}. Is the LLM Comparator app running at {}? Try "Web Scraper" only when it is running, or use "Google Search API" instead.'.format(e.code, LLM_COMPARATOR_URL)}), 502
    except URLError as e:
        logger.warning("[Google Search Result] Cannot reach LLM Comparator: %s", e.reason)
        return jsonify({'success': False, 'error': 'Cannot reach LLM Comparator: {}. Ensure it is running at {} or use "Google Search API" instead.'.format(str(e.reason), LLM_COMPARATOR_URL)}), 502
    except Exception as e:
        logger.exception("[Google Search Result] Error: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/run-google-search-api', methods=['POST'])
def run_google_search_api():
    """
    Use Google Custom Search API: get queries from token, call LLM Comparator
    /api/google-only-api with api_key and cse_id, store CSV, return download_token.
    Does not require LLM Comparator to run Selenium.
    """
    from urllib.request import Request as UrlRequest, urlopen
    from urllib.error import HTTPError, URLError
    logger.info("[Google Search Result] Request received (run-google-search-api)")
    try:
        data = request.get_json() or {}
        token = data.get('token')
        preferred_query_column = (data.get('query_column') or '').strip() or None
        api_key = (data.get('api_key') or '').strip()
        cse_id = (data.get('cse_id') or '').strip()
        if not token:
            return jsonify({'success': False, 'error': 'Missing token'}), 400
        if not api_key or not cse_id:
            return jsonify({'success': False, 'error': 'API key and CSE ID are required for Google Search API'}), 400
        queries, _query_column, err = extract_queries_from_token(token, preferred_query_column)
        if err:
            status = 404 if 'expired' in err else 400
            logger.warning("[Google Search Result] extract_queries failed: %s", err)
            return jsonify({'success': False, 'error': err}), status
        logger.info("[Google Search Result] Starting (API): queries_count=%s", len(queries))
        url = '{}/api/google-only-api'.format(_LLM_COMPARATOR_INTERNAL_URL.rstrip('/'))
        payload = json.dumps({'queries': queries, 'max_results': 10, 'api_key': api_key, 'cse_id': cse_id}).encode('utf-8')
        req_obj = UrlRequest(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
        resp = urlopen(req_obj, timeout=300)
        csv_content = resp.read()
        download_token = store_temp_file(csv_content, 'google_search_results.csv', ttl=1800)
        logger.info("[Google Search Result] Completed successfully (API): queries_count=%s", len(queries))
        return jsonify({
            'success': True,
            'download_token': download_token,
            'filename': 'google_search_results.csv',
            'queries_count': len(queries),
        })
    except HTTPError as e:
        logger.error("[Google Search Result] LLM Comparator API HTTP error: %s", e.code)
        return jsonify({'success': False, 'error': 'LLM Comparator API error: {}. Is the app running at {}?'.format(e.code, LLM_COMPARATOR_URL)}), 502
    except URLError as e:
        logger.error("[Google Search Result] Cannot reach LLM Comparator: %s", e.reason)
        return jsonify({'success': False, 'error': 'Cannot reach LLM Comparator: {}'.format(str(e.reason))}), 502
    except Exception as e:
        logger.exception("[Google Search Result] Error: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stream/run-google-search', methods=['POST'])
def stream_run_google_search():
    """
    Streaming variant: get queries from token, call LLM Comparator google-only-stream,
    forward NDJSON log lines to the client, and on 'done' store CSV and send download_token.
    """
    from urllib.request import Request as UrlRequest, urlopen
    from urllib.error import HTTPError, URLError
    import base64
    logger.info("[Google Search Result] Request received (stream)")
    try:
        data = request.get_json() or {}
        token = data.get('token')
        preferred_query_column = (data.get('query_column') or '').strip() or None
        if not token:
            return jsonify({'success': False, 'error': 'Missing token'}), 400
        queries, _query_column, err = extract_queries_from_token(token, preferred_query_column)
        if err:
            status = 404 if 'expired' in err else 400
            logger.warning("[Google Search Result] Stream extract_queries failed: %s", err)
            return jsonify({'success': False, 'error': err}), status

        site = (data.get('site') or data.get('selenium_site') or '').strip() or None
        logger.info("[Google Search Result] Stream started: queries_count=%s, site=%s", len(queries), site or '(none)')
        payload_dict = {'queries': queries, 'max_results': 10}
        if site:
            payload_dict['site'] = site
        payload = json.dumps(payload_dict).encode('utf-8')
        url = '{}/api/google-only-stream'.format(_LLM_COMPARATOR_INTERNAL_URL.rstrip('/'))
        req_obj = UrlRequest(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
        bufsize = 8192

        def generate():
            buffer = b''
            download_token = None
            filename = 'google_search_results.csv'
            try:
                # Emit immediate progress so the UI shows activity before upstream handshake completes.
                start_msg = f"Starting Google search for {len(queries)} queries via LLM Comparator..."
                if site:
                    start_msg += f" (site:{site})"
                yield json.dumps({'type': 'log', 'message': start_msg}) + '\n'

                try:
                    resp = urlopen(req_obj, timeout=600)
                except HTTPError as e:
                    err_msg = _llm_comparator_404_message(e, _LLM_COMPARATOR_INTERNAL_URL)
                    logger.error("[Google Search Result] Stream HTTP error: %s", err_msg)
                    yield json.dumps({'type': 'log', 'message': err_msg}) + '\n'
                    yield json.dumps({'type': 'error', 'message': err_msg}) + '\n'
                    return
                except URLError as e:
                    err_msg = 'Cannot reach LLM Comparator ({}). Start it at {} or use "Google Search API" instead.'.format(str(e.reason), LLM_COMPARATOR_URL)
                    logger.error("[Google Search Result] Stream URLError: %s", e.reason)
                    yield json.dumps({'type': 'log', 'message': err_msg}) + '\n'
                    yield json.dumps({'type': 'error', 'message': err_msg}) + '\n'
                    return

                while True:
                    chunk = resp.read(bufsize)
                    if not chunk:
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        line = line.decode('utf-8', errors='replace').strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get('type') == 'done' and obj.get('csv_base64'):
                                csv_b64 = obj['csv_base64']
                                csv_content = base64.b64decode(csv_b64)
                                download_token = store_temp_file(csv_content, filename, ttl=1800)
                                logger.info("[Google Search Result] Stream completed: queries_count=%s", len(queries))
                                yield json.dumps({
                                    'type': 'done',
                                    'download_token': download_token,
                                    'filename': filename,
                                    'queries_count': len(queries),
                                }) + '\n'
                            elif obj.get('type') == 'error':
                                logger.error("[Google Search Result] Stream error: %s", obj.get('message', line))
                                yield line + '\n'
                            else:
                                # Forward progress/log events exactly as provided by LLM Comparator.
                                yield line + '\n'
                        except (ValueError, KeyError):
                            yield line + '\n'
                if buffer.strip():
                    try:
                        obj = json.loads(buffer.decode('utf-8'))
                        if obj.get('type') == 'done' and obj.get('csv_base64') and not download_token:
                            csv_b64 = obj['csv_base64']
                            csv_content = base64.b64decode(csv_b64)
                            download_token = store_temp_file(csv_content, filename, ttl=1800)
                            yield json.dumps({
                                'type': 'done',
                                'download_token': download_token,
                                'filename': filename,
                                'queries_count': len(queries),
                            }) + '\n'
                    except (ValueError, KeyError):
                        pass
            except Exception as e:
                logger.exception("[Google Search Result] Stream generator error: %s", e)
                yield json.dumps({'type': 'log', 'message': 'Error: ' + str(e)}) + '\n'

        return Response(
            generate(),
            mimetype='application/x-ndjson',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )
    except Exception as e:
        logger.exception("[Google Search Result] Stream request error: %s", e)
        def err_stream():
            yield json.dumps({'type': 'log', 'message': 'Error: ' + str(e)}) + '\n'
            yield json.dumps({'type': 'error', 'message': str(e)}) + '\n'
        return Response(err_stream(), mimetype='application/x-ndjson', headers={'Cache-Control': 'no-cache'})


@app.route('/api/run-llm-comparison-from-results', methods=['POST'])
def run_llm_comparison_from_results():
    """Run LLM comparison using existing SU search results + Google results tokens."""
    logger.info("[LLM Comparison] Request received")
    try:
        import pandas as pd

        data = request.get_json() or {}
        su_token = data.get('su_token')
        google_token = data.get('google_token')
        has_ground_truth = bool(data.get('has_ground_truth', True))

        llm_provider = (data.get('llm_provider') or 'openai').strip().lower()
        openai_key = (data.get('openai_api_key') or '').strip()
        gemini_key = (data.get('gemini_api_key') or '').strip()
        model = (data.get('model') or '').strip()
        llm_prompt_custom = (data.get('llm_prompt') or '').strip()

        if not su_token or not google_token:
            logger.warning("[LLM Comparison] Missing tokens: su_token=%s google_token=%s", bool(su_token), bool(google_token))
            return jsonify({'success': False, 'error': 'su_token and google_token are required'}), 400

        if llm_provider == 'openai' and not openai_key:
            return jsonify({'success': False, 'error': 'OpenAI API key is required for LLM comparison'}), 400
        if llm_provider == 'gemini' and not gemini_key:
            return jsonify({'success': False, 'error': 'Gemini API key is required for LLM comparison'}), 400

        su_meta = _load_token_meta(su_token)
        google_meta = _load_token_meta(google_token)
        if not su_meta:
            logger.warning("[LLM Comparison] SU result token not found or expired")
            return jsonify({'success': False, 'error': 'SU result token not found or expired'}), 404
        if not google_meta:
            logger.warning("[LLM Comparison] Google result token not found or expired")
            return jsonify({'success': False, 'error': 'Google result token not found or expired'}), 404

        su_path = su_meta['path']
        google_path = google_meta['path']

        # Import LLM judge logic from LLM_Comparator app so behavior stays aligned.
        llm_app_dir = os.path.abspath(os.path.join(BASE_DIR, '..', 'LLM_Comparator', 'LLM_Comparator'))
        if llm_app_dir not in sys.path:
            sys.path.append(llm_app_dir)
        from services.llm_judge import (
            judge_results,
            DEFAULT_LLM_PROMPT,
            DEFAULT_OPENAI_MODEL,
            generate_dqe_final_analysis,
        )

        llm_model = (model.strip() if (model and model.strip()) else DEFAULT_OPENAI_MODEL)
        prompt_template = llm_prompt_custom if llm_prompt_custom else DEFAULT_LLM_PROMPT
        logger.info("[LLM Comparison] Starting: provider=%s, model=%s, has_ground_truth=%s, custom_prompt=%s", llm_provider, llm_model, has_ground_truth, bool(llm_prompt_custom))

        def _read_dataframe(path: str, original_filename: str = ''):
            """Read token-backed file by original filename (path is always .bin)."""
            lower_name = (original_filename or '').lower()

            if lower_name.endswith('.xlsx') or lower_name.endswith('.xlsm'):
                xls = pd.ExcelFile(path, engine='openpyxl')
                sheet_name = 'Search Results' if 'Search Results' in xls.sheet_names else xls.sheet_names[0]
                return pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
            if lower_name.endswith('.xls'):
                xls = pd.ExcelFile(path)
                sheet_name = 'Search Results' if 'Search Results' in xls.sheet_names else xls.sheet_names[0]
                return pd.read_excel(path, sheet_name=sheet_name)

            if lower_name.endswith('.csv'):
                return pd.read_csv(path)

            # Fallback for unknown names: try CSV first, then Excel.
            try:
                return pd.read_csv(path)
            except Exception:
                try:
                    xls = pd.ExcelFile(path, engine='openpyxl')
                    sheet_name = 'Search Results' if 'Search Results' in xls.sheet_names else xls.sheet_names[0]
                    return pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
                except Exception:
                    xls = pd.ExcelFile(path)
                    sheet_name = 'Search Results' if 'Search Results' in xls.sheet_names else xls.sheet_names[0]
                    return pd.read_excel(path, sheet_name=sheet_name)

        def _to_int_rank(v):
            try:
                if pd.isna(v):
                    return None
                return int(float(v))
            except Exception:
                return None

        def _norm(s):
            return str(s or '').strip().lower()

        def _find_rank(results, expected_title, expected_url):
            t = _norm(expected_title)
            u = _norm(expected_url)
            if not t and not u:
                return None
            for item in results:
                rt = _norm(item.get('title'))
                ru = _norm(item.get('url'))
                if t and u:
                    if rt == t and ru == u:
                        return item.get('rank')
                elif t and rt == t:
                    return item.get('rank')
                elif u and ru == u:
                    return item.get('rank')
            return None

        su_df = _read_dataframe(su_path, su_meta.get('filename', ''))
        google_df = _read_dataframe(google_path, google_meta.get('filename', ''))

        def _cell_str(val):
            """Treat pandas NaN / literal 'nan' as empty (avoid bogus query groups)."""
            if val is None:
                return ''
            try:
                if pd.isna(val):
                    return ''
            except Exception:
                pass
            s = str(val).strip()
            if s.lower() in ('nan', 'none', 'nat'):
                return ''
            return s

        def _hits_blob(hits, max_n: int) -> str:
            sl = sorted(hits, key=lambda x: x.get('rank') or 999)[:max_n]
            lines = []
            for r in sl:
                lines.append('[{}] {} | {}'.format(
                    r.get('rank', ''), (r.get('title') or '').replace('\n', ' '), (r.get('url') or '')))
            return '\n'.join(lines)

        # One group per distinct query: SU export often repeats Generated_Query on every result row,
        # or leaves it blank after the first row — do NOT start a new group per row.
        _SU_STORE_MAX = 50
        _SU_TOP_FOR_LLM = 10
        _GOOGLE_TOP_FOR_LLM = 5
        _GOOGLE_TOP_FOR_SHEET = 10

        # Query-only Search Eval export uses Query, Search Result Title/URL (no Result_Rank).
        if 'Generated_Query' not in su_df.columns and 'Query' in su_df.columns:
            _su_rn = {'Query': 'Generated_Query'}
            if 'Search Result Title' in su_df.columns:
                _su_rn['Search Result Title'] = 'Result_Title'
            if 'Search Result URL' in su_df.columns:
                _su_rn['Search Result URL'] = 'Result_URL'
            su_df = su_df.rename(columns=_su_rn)
            if 'Result_Rank' not in su_df.columns:
                _rank_list = []
                _rc = 0
                for __idx, __row in su_df.iterrows():
                    __q = str(__row.get('Generated_Query') or '').strip()
                    if __q and __q.lower() not in ('nan', 'none', 'nat'):
                        _rc = 0
                    _rc += 1
                    __t = str(__row.get('Result_Title') or '').strip()
                    __u = str(__row.get('Result_URL') or '').strip()
                    if not __t or __t == 'NO_RESULTS_FOUND' or __t.startswith('ERROR:'):
                        _rank_list.append(0)
                    else:
                        _rank_list.append(_rc)
                su_df = su_df.copy()
                su_df['Result_Rank'] = _rank_list

        su_groups = []
        current = None
        for _idx, row in su_df.iterrows():
            q = _cell_str(row.get('Generated_Query'))

            if q:
                if current is None or q != current['query']:
                    current = {
                        'query': q,
                        'expected_title': _cell_str(row.get('Original_Title')),
                        'expected_url': _cell_str(row.get('Original_URL')),
                        'su_results': [],
                    }
                    su_groups.append(current)
            elif current is None:
                continue
            else:
                et = _cell_str(row.get('Original_Title'))
                eu = _cell_str(row.get('Original_URL'))
                if et:
                    current['expected_title'] = et
                if eu:
                    current['expected_url'] = eu

            if current is None:
                continue

            rank = _to_int_rank(row.get('Result_Rank'))
            title = _cell_str(row.get('Result_Title'))
            url = _cell_str(row.get('Result_URL'))
            if rank and rank > 0 and title and url and len(current['su_results']) < _SU_STORE_MAX:
                current['su_results'].append({'rank': rank, 'title': title, 'url': url})

        # Google: keep top 10 for sheet / recall; LLM still uses top 5 only.
        # Query may appear only on the first row per block (blank continuation rows).
        google_map = {}
        current_gq = None
        for _idx, row in google_df.iterrows():
            q = _cell_str(row.get('Query'))
            if q:
                current_gq = q
            elif current_gq is None:
                continue
            rank = _to_int_rank(row.get('Rank'))
            title = _cell_str(row.get('Title'))
            url = _cell_str(row.get('URL'))
            if not rank or rank <= 0 or not title or not url:
                continue
            google_map.setdefault(current_gq, []).append({'rank': rank, 'title': title, 'url': url})

        for q in list(google_map.keys()):
            google_map[q] = sorted(google_map[q], key=lambda x: x['rank'])[:_GOOGLE_TOP_FOR_SHEET]

        # Fallback lookup when SU "Generated_Query" and Google "Query" differ only by whitespace/case
        google_map_by_norm = {}
        for k, v in google_map.items():
            kn = str(k).strip().lower()
            if kn and kn not in google_map_by_norm:
                google_map_by_norm[kn] = v

        def _google_results_for_query(q: str):
            if q in google_map:
                return google_map[q]
            kn = str(q).strip().lower()
            return google_map_by_norm.get(kn, [])

        if not su_groups:
            logger.warning("[LLM Comparison] No usable Search Evaluation rows in SU result file")
            return jsonify({'success': False, 'error': 'No usable Search Evaluation rows found in SU result file'}), 400

        llm_api_key = openai_key if llm_provider == 'openai' else gemini_key
        # llm_model already set above (with trim and default)

        def _flush_logs():
            try:
                for h in logging.root.handlers:
                    if hasattr(h, 'flush'):
                        h.flush()
                sys.stderr.flush()
            except Exception:
                pass

        n_su = len(su_df)
        n_goog = len(google_df)
        logger.info("[LLM Comparison] Loaded files: SU rows=%s, Google rows=%s", n_su, n_goog)
        logger.info("[LLM Comparison] Parsed %s queries; Google map has %s distinct query keys", len(su_groups), len(google_map))
        # Help debug query-string mismatches (Google CSV Query must match SU Generated_Query exactly)
        if su_groups and google_map:
            sample_q = su_groups[0]['query']
            if not _google_results_for_query(sample_q):
                logger.warning(
                    "[LLM Comparison] First SU query has no Google rows (exact or normalized match). "
                    "Sample SU query: %r — Google keys sample: %s",
                    sample_q[:120],
                    list(google_map.keys())[:3],
                )
        _flush_logs()

        async def _run_all_llm_judgments():
            """One row per distinct query in the workbook. SU + Google LLM when data exists; Google columns blank if no Google rows."""
            summary_rows = []
            queries_without_google = 0
            total = len(su_groups)
            for idx, item in enumerate(su_groups, start=1):
                query = item['query']
                expected_title = item['expected_title'] if has_ground_truth else ''
                expected_url = item['expected_url'] if has_ground_truth else ''
                su_results_raw = item['su_results']
                google_results_full = _google_results_for_query(query)

                su_for_llm = sorted(su_results_raw, key=lambda x: x.get('rank') or 999)[:_SU_TOP_FOR_LLM]
                google_for_llm = sorted(google_results_full, key=lambda x: x.get('rank') or 999)[:_GOOGLE_TOP_FOR_LLM]

                q_preview = (query[:100] + '…') if len(query) > 100 else query
                has_google = bool(google_for_llm)

                if not has_google:
                    queries_without_google += 1
                    msg = 'Distinct query {}/{}: {} — no Google rows; Google columns left blank, SU still evaluated'.format(idx, total, q_preview)
                    logger.info('[LLM Comparison] %s', msg)
                    _llm_comparison_emit_event({'type': 'log', 'message': msg, 'skipped': False})
                    _llm_comparison_emit_event({
                        'type': 'progress',
                        'current': idx,
                        'total': total,
                        'stage': 'no_google_rows',
                        'query_preview': q_preview,
                        'distinct_queries': total,
                        'su_hits_stored': len(su_results_raw),
                        'su_top_for_llm': len(su_for_llm),
                        'google_hits_stored': 0,
                        'google_top_for_llm': 0,
                    })
                    _flush_logs()
                else:
                    logger.info(
                        "[LLM Comparison] Distinct query %s/%s %r | SU hits in file=%s (LLM uses top %s) | Google hits=%s (LLM uses top %s)",
                        idx, total, q_preview, len(su_results_raw), _SU_TOP_FOR_LLM, len(google_results_full), _GOOGLE_TOP_FOR_LLM,
                    )
                    _llm_comparison_emit_event({
                        'type': 'progress',
                        'current': idx,
                        'total': total,
                        'stage': 'query_start',
                        'query_index': idx,
                        'query_preview': q_preview,
                        'distinct_queries': total,
                        'su_hits_stored': len(su_results_raw),
                        'su_top_for_llm': len(su_for_llm),
                        'google_hits_stored': len(google_results_full),
                        'google_top_for_llm': len(google_for_llm),
                    })
                    _llm_comparison_emit_event({'type': 'log', 'message': (
                        'Distinct query {}/{}: {} — SU: {} results in sheet, top {} to LLM; Google: {} results, top {} to LLM'
                    ).format(idx, total, q_preview, len(su_results_raw), _SU_TOP_FOR_LLM, len(google_results_full), _GOOGLE_TOP_FOR_LLM)})
                    _flush_logs()

                su_rank = _find_rank(su_results_raw, expected_title, expected_url) if has_ground_truth else None
                google_rank = _find_rank(google_results_full, expected_title, expected_url) if has_ground_truth else None

                su_score = None
                google_score = None
                su_reason = ''
                google_reason = ''

                if su_for_llm:
                    try:
                        logger.info("[LLM Comparison] Query %s/%s: LLM SearchUnify (top %s)...", idx, total, len(su_for_llm))
                        _llm_comparison_emit_event({'type': 'log', 'message': '  → LLM judging SearchUnify results...'})
                        _flush_logs()
                        su_out = await judge_results(
                            query,
                            expected_title,
                            expected_url,
                            su_for_llm,
                            llm_api_key,
                            llm_model,
                            prompt_template,
                            provider=llm_provider,
                        )
                        if su_out:
                            su_score = su_out.get('score')
                            su_reason = str(su_out.get('reason') or '')
                            logger.info("[LLM Comparison] Query %s/%s: SU LLM score=%s", idx, total, su_score)
                            _llm_comparison_emit_event({'type': 'log', 'message': '  → SearchUnify LLM score: {}'.format(su_score)})
                        else:
                            logger.warning("[LLM Comparison] Query %s/%s: SU LLM returned empty", idx, total)
                            _llm_comparison_emit_event({'type': 'log', 'message': '  → SearchUnify LLM returned empty'})
                    except Exception as su_exc:
                        logger.exception("[LLM Comparison] Query %s/%s: SU LLM failed: %s", idx, total, su_exc)
                        _llm_comparison_emit_event({'type': 'log', 'message': '  → SearchUnify LLM error: {}'.format(su_exc)})
                    _flush_logs()
                else:
                    logger.info("[LLM Comparison] Query %s/%s: no SU rows for LLM", idx, total)
                    _llm_comparison_emit_event({'type': 'log', 'message': '  → No SearchUnify results to judge'})

                if google_for_llm:
                    try:
                        logger.info("[LLM Comparison] Query %s/%s: LLM Google (top %s)...", idx, total, len(google_for_llm))
                        _llm_comparison_emit_event({'type': 'log', 'message': '  → LLM judging Google results...'})
                        _flush_logs()
                        google_out = await judge_results(
                            query,
                            expected_title,
                            expected_url,
                            google_for_llm,
                            llm_api_key,
                            llm_model,
                            prompt_template,
                            provider=llm_provider,
                        )
                        if google_out:
                            google_score = google_out.get('score')
                            google_reason = str(google_out.get('reason') or '')
                            logger.info("[LLM Comparison] Query %s/%s: Google LLM score=%s", idx, total, google_score)
                            _llm_comparison_emit_event({'type': 'log', 'message': '  → Google LLM score: {}'.format(google_score)})
                        else:
                            logger.warning("[LLM Comparison] Query %s/%s: Google LLM returned empty", idx, total)
                            _llm_comparison_emit_event({'type': 'log', 'message': '  → Google LLM returned empty'})
                    except Exception as g_exc:
                        logger.exception("[LLM Comparison] Query %s/%s: Google LLM failed: %s", idx, total, g_exc)
                        _llm_comparison_emit_event({'type': 'log', 'message': '  → Google LLM error: {}'.format(g_exc)})
                    _flush_logs()
                else:
                    logger.info("[LLM Comparison] Query %s/%s: no Google rows — skipping Google LLM", idx, total)
                    _llm_comparison_emit_event({'type': 'log', 'message': '  → No Google results — Google LLM skipped'})
                    _flush_logs()

                su_blob = _hits_blob(su_results_raw, 10)
                google_blob = _hits_blob(google_results_full, 10)
                summary_row = {
                    'Query': query,
                    'Expected_Title': expected_title,
                    'Expected_URL': expected_url,
                    'SU_Recall_Rank': su_rank if su_rank is not None else '',
                    'SU Search Result (top 10)': su_blob,
                    'Google_Recall_Rank': google_rank if google_rank is not None else '',
                    'Google Search Result (top 10)': google_blob,
                    'SU_LLM_Score': su_score if su_score is not None else '',
                    'Google_LLM_Score': google_score if google_score is not None else '',
                    'SU_LLM_Reason': su_reason,
                    'Google_LLM_Reason': google_reason,
                }
                summary_rows.append(summary_row)
                _llm_comparison_emit_event({
                    'type': 'query_complete',
                    'row': {
                        'query': query,
                        'expected_title': expected_title,
                        'expected_url': expected_url,
                        'su_recall_rank': su_rank,
                        'google_recall_rank': google_rank,
                        'su_llm_score': su_score,
                        'google_llm_score': google_score,
                        'su_llm_reason': su_reason,
                        'google_llm_reason': google_reason,
                    },
                })

            return summary_rows, queries_without_google

        logger.info("[LLM Comparison] Beginning LLM API calls (provider=%s, model=%s)...", llm_provider, llm_model)
        _flush_logs()
        try:
            summary_rows, queries_without_google = asyncio.run(_run_all_llm_judgments())
        except RuntimeError as rexc:
            # e.g. "asyncio.run() cannot be called from a running event loop"
            logger.exception("[LLM Comparison] asyncio.run failed: %s", rexc)
            return jsonify({'success': False, 'error': 'LLM comparison async error: {}'.format(rexc)}), 500

        logger.info(
            "[LLM Comparison] Judgments done: rows=%s, queries_without_google_rows=%s",
            len(summary_rows), queries_without_google,
        )
        _flush_logs()

        if not summary_rows:
            logger.warning("[LLM Comparison] No summary rows (no SU queries)")
            return jsonify({
                'success': False,
                'error': 'No Search Evaluation queries found in the SU result file.',
                'queries_skipped_no_google': queries_without_google,
            }), 400

        def _bullets_for_sheet(items):
            if not items:
                return ''
            return '\n'.join('• {}'.format(x) for x in items if str(x).strip())

        compact_for_final = []
        for r in summary_rows:
            compact_for_final.append({
                'query': r.get('Query') or '',
                'su_score': r.get('SU_LLM_Score'),
                'google_score': r.get('Google_LLM_Score'),
                'su_reason': str(r.get('SU_LLM_Reason') or ''),
                'google_reason': str(r.get('Google_LLM_Reason') or ''),
                'su_recall': r.get('SU_Recall_Rank'),
                'google_recall': r.get('Google_Recall_Rank'),
            })

        final_analysis = None
        try:
            final_analysis = asyncio.run(
                generate_dqe_final_analysis(
                    compact_for_final,
                    has_ground_truth,
                    llm_api_key,
                    llm_model,
                    provider=llm_provider,
                )
            )
        except Exception as fa_exc:
            logger.exception('[LLM Comparison] Final analysis failed: %s', fa_exc)
            final_analysis = {
                'executive_summary': 'Final analysis could not be generated: {}'.format(fa_exc),
                'performed_well': [],
                'performed_poorly': [],
                'searchunify_vs_google': '',
                'recommendations': [],
            }

        if final_analysis:
            try:
                _llm_comparison_emit_event({'type': 'final_analysis', 'data': final_analysis})
            except Exception:
                pass
        _flush_logs()

        logger.info("[LLM Comparison] Building Excel (%s summary rows)", len(summary_rows))
        _flush_logs()

        _LLM_COL_ORDER = [
            'Query', 'Expected_Title', 'Expected_URL', 'SU_Recall_Rank', 'SU Search Result (top 10)',
            'Google_Recall_Rank', 'Google Search Result (top 10)', 'SU_LLM_Score', 'Google_LLM_Score',
            'SU_LLM_Reason', 'Google_LLM_Reason',
        ]
        summary_df = pd.DataFrame(summary_rows)
        summary_df = summary_df.reindex(columns=_LLM_COL_ORDER)

        xlsx_bytes = io.BytesIO()
        with pd.ExcelWriter(xlsx_bytes, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='LLM Comparison', index=False)
            fa = final_analysis or {}
            fa_df = pd.DataFrame([
                {'Section': 'Executive summary', 'Content': fa.get('executive_summary') or ''},
                {'Section': 'SearchUnify vs Google', 'Content': fa.get('searchunify_vs_google') or ''},
                {'Section': 'Performed well', 'Content': _bullets_for_sheet(fa.get('performed_well'))},
                {'Section': 'Performed poorly', 'Content': _bullets_for_sheet(fa.get('performed_poorly'))},
                {'Section': 'Recommendations', 'Content': _bullets_for_sheet(fa.get('recommendations'))},
            ])
            fa_df.to_excel(writer, sheet_name='Final Analysis', index=False)
            pd.DataFrame({'Info': [
                'Source file tokens reused from this session (no re-upload required).',
                'One row per distinct Generated_Query from Search Evaluation.',
                'SU / Google result columns are top-10 text blobs on the same row; Google fields are blank when no Google rows matched that query.',
                'LLM judges use top 10 SU hits and top 5 Google hits when Google data exists.',
                'Sheet "Final Analysis" is a batch-level strict LLM summary (SU vs Google, strong/weak queries, recommendations).',
            ]}).to_excel(
                writer, sheet_name='Metadata', index=False
            )

        token = store_temp_file(xlsx_bytes.getvalue(), 'llm_comparison_analysis.xlsx', ttl=1800)

        logger.info("[LLM Comparison] Completed successfully: queries_processed=%s queries_without_google=%s", len(summary_rows), queries_without_google)
        return jsonify({
            'success': True,
            'download_token': token,
            'filename': 'llm_comparison_analysis.xlsx',
            'queries_processed': len(summary_rows),
            'queries_skipped_no_google': queries_without_google,
            'llm_provider': llm_provider,
            'model_used': llm_model,
            'has_ground_truth': has_ground_truth,
            'final_analysis': final_analysis,
        })
    except Exception as e:
        logger.exception("[LLM Comparison] Error: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        setattr(_llm_comparison_stream_emit, 'emit', None)


@app.route('/api/stream/run-llm-comparison-from-results', methods=['POST'])
def stream_run_llm_comparison_from_results():
    """Same body as sync LLM comparison; streams NDJSON log/progress lines, then type done or error."""
    data = request.get_json() or {}
    out_q = queue.Queue()

    def worker():
        def emit(obj):
            out_q.put(json.dumps(obj, ensure_ascii=False) + '\n')

        setattr(_llm_comparison_stream_emit, 'emit', emit)
        try:
            with app.test_request_context(
                '/api/run-llm-comparison-from-results',
                method='POST',
                json=data,
                content_type='application/json',
            ):
                rv = run_llm_comparison_from_results()
            if isinstance(rv, tuple):
                resp, status_code = rv[0], rv[1]
            else:
                resp, status_code = rv, getattr(rv, 'status_code', None) or 200
            try:
                body = resp.get_json(silent=True) or {}
            except Exception:
                body = {}
            if status_code == 200 and body.get('success'):
                out_q.put(json.dumps({
                    'type': 'done',
                    'download_token': body.get('download_token'),
                    'filename': body.get('filename'),
                    'queries_processed': body.get('queries_processed'),
                    'queries_skipped_no_google': body.get('queries_skipped_no_google'),
                    'model_used': body.get('model_used'),
                    'final_analysis': body.get('final_analysis'),
                }, ensure_ascii=False) + '\n')
            else:
                msg = body.get('error') if isinstance(body, dict) else None
                if not msg and hasattr(resp, 'get_data'):
                    try:
                        msg = resp.get_data(as_text=True) or 'LLM comparison failed'
                    except Exception:
                        msg = 'LLM comparison failed'
                msg = msg or 'LLM comparison failed'
                out_q.put(json.dumps({'type': 'error', 'message': str(msg)}, ensure_ascii=False) + '\n')
        except Exception as ex:
            logger.exception("[LLM Comparison] stream worker: %s", ex)
            out_q.put(json.dumps({'type': 'error', 'message': str(ex)}, ensure_ascii=False) + '\n')
        finally:
            setattr(_llm_comparison_stream_emit, 'emit', None)
            out_q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            line = out_q.get()
            if line is None:
                break
            yield line if isinstance(line, bytes) else line.encode('utf-8')

    return Response(
        generate(),
        mimetype='application/x-ndjson',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/upload-csv', methods=['POST'])
def upload_csv():
    """Handle CSV/Excel upload for query generation or search evaluation.
    Stores the uploaded file in-memory (no repo-disk persistence) and returns a short-lived token.
    Also parses column headers and returns them for UI selection.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file part'}), 400
        f = request.files['file']
        if f.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
        filename = os.path.basename(f.filename)

        if not _allowed_upload_extension(filename):
            return jsonify({
                'success': False,
                'error': 'Only CSV or Excel is allowed: .csv, .xlsx, .xlsm, or .xls',
            }), 400

        # Read bytes and store in-memory with a token
        data = f.read()
        token = store_temp_file(data, filename, ttl=3600)
        download_url = f"/api/download-temp?token={token}"
        
        # Parse columns from the file
        columns = []
        try:
            file_bytes = io.BytesIO(data)
            df = read_tabular_headers_only(file_bytes, filename)
            columns = df.columns.tolist()
        except Exception as parse_err:
            # If parsing fails, still return success but without columns
            print(f"Warning: Could not parse columns from {filename}: {parse_err}")
        
        return jsonify({
            'success': True, 
            'token': token, 
            'filename': filename, 
            'download_url': download_url,
            'columns': columns
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# Community Page Endpoints
# ============================================

@app.route('/api/update-community-curl', methods=['POST'])
def update_community_curl():
    """Parse and save community page curl command to curl_community.json"""
    try:
        from utils.community_request_handler import parse_community_curl
        
        incoming = request.json or {}
        raw_curl = incoming.get('rawCurl', '')
        
        if not raw_curl:
            return jsonify({'success': False, 'error': 'No curl command provided'}), 400
        
        # Parse the curl command
        template = parse_community_curl(raw_curl)
        
        if not template.get('url'):
            return jsonify({'success': False, 'error': 'Could not extract URL from curl command'}), 400
        
        # Save to curl_community.json
        curl_community_path = os.path.join(BASE_DIR, 'curl_community.json')
        with open(curl_community_path, 'w') as f:
            json.dump(template, f, indent=2)
        
        return jsonify({
            'success': True,
            'message': 'Community curl saved successfully',
            'curl_community': template,
            'url': template.get('url', '')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/get-community-curl', methods=['GET'])
def get_community_curl():
    """Get current community curl configuration"""
    try:
        curl_community_path = os.path.join(BASE_DIR, 'curl_community.json')
        
        if os.path.exists(curl_community_path):
            with open(curl_community_path, 'r') as f:
                curl_community = json.load(f)
            return jsonify({
                'success': True,
                'curl_community': curl_community
            })
        else:
            return jsonify({
                'success': False,
                'error': 'curl_community.json not found'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stream/update-config-and-run-community', methods=['POST'])
def stream_update_config_and_run_community():
    """Stream update config and run main_community.py for community page extraction.
    Now supports curl per content source - each source has its own curl command.
    """
    try:
        from utils.community_request_handler import parse_community_curl
        
        data = request.json
        config_path = os.path.join(BASE_DIR, 'config.json')
        curl_community_path = os.path.join(BASE_DIR, 'curl_community.json')
        
        multi_source_config = data.get('MULTI_SOURCE_CONFIG', {})
        
        # Validate that each source has a curl
        for source_name, source_config in multi_source_config.items():
            if not source_config.get('curl'):
                return Response(f"ERROR: No curl command provided for source '{source_name}'.\n", 
                              mimetype='text/plain', status=400)
        
        def generate():
            source_temp_files = []
            total_success = True
            
            yield f"📝 Processing {len(multi_source_config)} content source(s) [COMMUNITY MODE - Multi-cURL]\n"
            yield f"{'='*60}\n"
            
            for source_name, source_config in multi_source_config.items():
                yield f"\n🔍 Processing Content Source: {source_name}\n"
                yield f"{'='*60}\n"
                
                try:
                    # Parse the curl for this source
                    raw_curl = source_config.get('curl', '')
                    yield f"📋 Parsing cURL for {source_name}...\n"
                    
                    template = parse_community_curl(raw_curl)
                    
                    if not template.get('url'):
                        yield f"❌ ERROR: Could not extract URL from curl for {source_name}\n"
                        total_success = False
                        continue
                    
                    yield f"✅ Parsed URL: {template.get('url', '')[:80]}...\n"
                    
                    # Save this source's curl template to curl_community.json
                    with open(curl_community_path, 'w') as f:
                        json.dump(template, f, indent=2)
                    
                    # Create a temporary config with only this source
                    temp_config = {
                        'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
                        'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
                        'MULTI_SOURCE_CONFIG': {
                            source_name: {
                                'document_count': source_config.get('document_count', 50)
                            }
                        }
                    }
                    
                    with open(config_path, 'w') as f:
                        json.dump(temp_config, f, indent=2)
                    
                    yield f"🚀 Running main_community.py for {source_name}...\n"
                    
                    # Run main_community.py
                    main_community_path = os.path.join(BASE_DIR, 'main_community.py')
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix='_output.csv')
                    planned_output = tf.name
                    tf.close()
                    
                    env = os.environ.copy()
                    env['OUTPUT_CSV'] = planned_output
                    env['PYTHONUNBUFFERED'] = '1'
                    
                    proc = subprocess.Popen(
                        [sys.executable, '-u', main_community_path],
                        cwd=BASE_DIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        env=env
                    )
                    
                    import threading
                    import queue
                    q = queue.Queue()
                    def stream_reader():
                        try:
                            for line in iter(proc.stdout.readline, ''):
                                if line:
                                    q.put(line)
                        except Exception:
                            pass
                        finally:
                            try:
                                q.put(None)
                            except Exception:
                                pass
                    reader_thread = threading.Thread(target=stream_reader, daemon=True)
                    reader_thread.start()
                    
                    try:
                        while True:
                            try:
                                line = q.get(timeout=15.0)
                                if line is None:
                                    break
                                yield line if line.endswith('\n') else line + '\n'
                            except queue.Empty:
                                yield " KEEP_ALIVE_PING\n"
                    except (GeneratorExit, SystemExit):
                        try:
                            if proc.poll() is None:
                                proc.terminate()
                                proc.wait(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        raise
                    
                    rc = proc.wait()
                    
                    if rc == 0:
                        yield f"\n✅ SUCCESS for {source_name}\n"
                        if os.path.exists(planned_output):
                            source_temp_files.append((source_name, planned_output))
                    else:
                        yield f"\n❌ ERROR for {source_name}: Exit code {rc}\n"
                        total_success = False
                        try:
                            if os.path.exists(planned_output):
                                os.unlink(planned_output)
                        except Exception:
                            pass
                    
                except Exception as e:
                    yield f"❌ EXCEPTION for {source_name}: {str(e)}\n"
                    total_success = False
            
            # Combine all source outputs
            yield f"\n{'='*60}\n"
            yield "📊 Combining outputs...\n"
            
            combined_token = None
            if source_temp_files:
                try:
                    import csv
                    combined_tf = tempfile.NamedTemporaryFile(delete=False, suffix='_combined_output.csv', mode='w', newline='', encoding='utf-8')
                    combined_writer = csv.writer(combined_tf)
                    total_rows = 0
                    
                    combined_writer.writerow(["Source Name", "Offset", "Title", "Description", "URL"])
                    
                    for source_name, temp_file_path in source_temp_files:
                        rows_added = 0
                        try:
                            with open(temp_file_path, 'r', newline='', encoding='utf-8') as rf:
                                reader = csv.reader(rf)
                                rows = list(reader)
                                if rows:
                                    data_rows = rows[1:] if len(rows) > 1 else []
                                    for row in data_rows:
                                        combined_writer.writerow(row)
                                        total_rows += 1
                                        rows_added += 1
                            yield f"📄 Added {rows_added} rows from {source_name}\n"
                        except Exception as read_err:
                            yield f"⚠️ Error reading temp file for {source_name}: {read_err}\n"
                        finally:
                            try:
                                if os.path.exists(temp_file_path):
                                    os.unlink(temp_file_path)
                            except Exception:
                                pass
                    
                    combined_tf.close()
                    
                    if os.path.exists(combined_tf.name):
                        with open(combined_tf.name, 'rb') as rf:
                            combined_bytes = rf.read()
                        combined_token = store_temp_file(combined_bytes, 'combined_output.csv')
                        yield f"📊 Combined CSV created with {total_rows} total rows\n"
                        try:
                            os.unlink(combined_tf.name)
                        except Exception:
                            pass
                except Exception as combine_err:
                    yield f"⚠️ Error combining outputs: {combine_err}\n"
            
            # Save final config
            final_config = {
                'REGENERATE_QUERIES': data.get('REGENERATE_QUERIES', 'yes'),
                'APPEND_TO_CSV': data.get('APPEND_TO_CSV', True),
                'MULTI_SOURCE_CONFIG': multi_source_config
            }
            with open(config_path, 'w') as f:
                json.dump(final_config, f, indent=2)
            
            yield f"\n{'='*60}\n"
            yield "📋 Final Summary\n"
            yield f"{'='*60}\n"
            yield f"📊 Processed {len(multi_source_config)} content sources [COMMUNITY MODE]\n"
            
            if total_success:
                yield "✅ All sources processed successfully!\n"
            else:
                yield "⚠️ Some sources had errors - check output above\n"
            
            if combined_token:
                yield f"COMBINED_DOWNLOAD_TOKEN: {combined_token}\n"
            
            yield "STREAM_DONE\n"
        
        return Response(generate(), mimetype='text/plain')
    
    except Exception as e:
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)


@app.route('/api/stream/run-search-evaluation-community', methods=['POST'])
def stream_run_search_evaluation_community():
    """Stream search evaluation for community pages with analysis and Excel output."""
    try:
        data = request.json or {}
        
        # Check if community curl exists
        curl_community_path = os.path.join(BASE_DIR, 'curl_community.json')
        if not os.path.exists(curl_community_path):
            return Response("ERROR: curl_community.json not found. Please parse a community curl command first.\n", 
                          mimetype='text/plain', status=400)
        
        input_csv = data.get('input_csv')
        created_temp_input = None
        input_token = data.get('input_token') or data.get('uploaded_token')
        if not input_csv and input_token:
            created_temp_input = create_tempfile_from_token(input_token)
            if created_temp_input:
                input_csv = created_temp_input
        
        if not input_csv:
            # Find latest queries file
            qo_dir = os.path.join(BASE_DIR, 'query_outputs')
            latest = None
            if os.path.isdir(qo_dir):
                for fname in os.listdir(qo_dir):
                    if fname.startswith('queries_') and fname.endswith('.csv'):
                        full = os.path.join(qo_dir, fname)
                        if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                            latest = full
                gq_dir = os.path.join(qo_dir, 'generated_queries')
                if os.path.isdir(gq_dir):
                    for fname in os.listdir(gq_dir):
                        if fname.endswith('.csv') and (fname.startswith('queries_') or fname.startswith('Distributed_queries_')):
                            full = os.path.join(gq_dir, fname)
                            if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                                latest = full
            if latest:
                input_csv = latest
        if not input_csv:
            input_csv = os.path.join(BASE_DIR, 'output.csv')

        output_csv = data.get('output_csv')
        if not output_csv:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix='_community_search_results.csv')
            output_csv = tf.name
            tf.close()

        delay = float(data.get('delay', 0.5))
        # Default to top 50 results for community pages
        top_n = int(data.get('top_n', 50))
        has_ground_truth = bool(data.get('has_ground_truth', True))
        using_upload = bool(data.get('input_token') or data.get('uploaded_token'))
        query_column = (data.get('query_column') or '').strip()
        title_column = (data.get('title_column') or '').strip() or None
        url_column = (data.get('url_column') or '').strip() or None

        if using_upload:
            if not query_column:
                return Response(
                    "ERROR: Select a Query column for the uploaded sheet.\n",
                    mimetype='text/plain',
                    status=400,
                )
            if has_ground_truth and (not title_column or not url_column):
                return Response(
                    "ERROR: Ground truth mode requires Expected Title and Expected URL columns for uploaded sheets.\n",
                    mimetype='text/plain',
                    status=400,
                )
        else:
            if not query_column:
                query_column = 'Generated_Query'
            if has_ground_truth:
                if not title_column:
                    title_column = 'Title'
                if not url_column:
                    url_column = 'Document_URL'

        def generate():
            yield f"USING_INPUT_CSV: {input_csv}\n"
            if not os.path.exists(input_csv):
                yield f"❌ Input file not found: {input_csv}\n"
                yield "EXIT_CODE: 1\nSTREAM_DONE\n"
                return
            
            yield f"🔍 Running community search evaluation...\n"
            yield f"📄 Query column: {query_column}\n"
            if has_ground_truth and title_column and url_column:
                yield f"📄 Ground truth columns: {title_column}, {url_column}\n"
            else:
                yield f"📄 Mode: query-only (no ground-truth matching)\n"
            yield f"📄 Top N results: {top_n}\n"
            
            output_xlsx = None
            
            try:
                import pandas as pd
                import re
                from utils.community_request_handler import load_community_template, fetch_community_documents
                import time
                
                # Load community template
                template = load_community_template(curl_community_path)
                yield f"✅ Loaded community template: {template.get('url', 'N/A')[:60]}...\n"
                
                # Load input (CSV or Excel — same uploads as main search eval)
                df = load_tabular_dataframe(input_csv, os.path.basename(input_csv))
                total_queries = len(df)
                yield f"📄 Loaded {total_queries} queries from input\n"
                
                q_col = resolve_user_tabular_column(df, query_column)
                if not q_col:
                    yield f"❌ Query column '{query_column}' not found in input (check spelling vs sheet headers).\n"
                    yield "EXIT_CODE: 1\nSTREAM_DONE\n"
                    return
                if has_ground_truth:
                    t_col = resolve_user_tabular_column(df, title_column)
                    u_col = resolve_user_tabular_column(df, url_column)
                    if not t_col:
                        yield f"❌ Expected title column '{title_column}' not found in input.\n"
                        yield "EXIT_CODE: 1\nSTREAM_DONE\n"
                        return
                    if not u_col:
                        yield f"❌ Expected URL column '{url_column}' not found in input.\n"
                        yield "EXIT_CODE: 1\nSTREAM_DONE\n"
                        return
                else:
                    t_col = u_col = None
                
                rows = []
                successful_searches = 0
                failed_searches = 0
                
                def clean_title_for_matching(title):
                    if not title:
                        return ""
                    s = str(title).lower().strip()
                    s = re.sub(r'<[^>]+>', '', s)
                    s = re.sub(r'[\W_]+', ' ', s)
                    s = re.sub(r'\s+', ' ', s).strip()
                    return s
                
                def clean_url(url):
                    if not url:
                        return ""
                    s = str(url).strip().lower()
                    s = re.sub(r'^https?://', '', s)
                    s = s.rstrip('/')
                    return s
                
                def is_title_match(original, candidate):
                    if not original or not candidate:
                        return False
                    o = clean_title_for_matching(original)
                    c = clean_title_for_matching(candidate)
                    return (o == c) or (o in c) or (c in o)
                
                def is_url_match(original, candidate):
                    if not original or not candidate:
                        return False
                    return clean_url(original) == clean_url(candidate)
                
                yield f"\n{'='*60}\n"
                yield "🔍 PERFORMING SEARCH EVALUATION\n"
                yield f"{'='*60}\n"
                
                for idx, row in df.iterrows():
                    qid = idx + 1
                    query = str(row.get(q_col, '')).strip()
                    orig_title = row.get(t_col, '') if t_col is not None else ''
                    orig_url = row.get(u_col, '') if u_col is not None else ''
                    
                    yield f"\n📝 Query {qid}/{total_queries}: '{query[:60]}{'...' if len(query) > 60 else ''}'\n"
                    if orig_title:
                        yield f"📰 Original: {str(orig_title)[:60]}{'...' if len(str(orig_title)) > 60 else ''}\n"
                    
                    try:
                        docs = fetch_community_documents(template, search_string=query, results_per_page=top_n)
                        
                        if docs:
                            yield f"📋 Found {len(docs)} results:\n"
                            successful_searches += 1
                            
                            # Show first 3 results
                            for i, d in enumerate(docs[:3]):
                                yield f"  {d.get('rank', i+1)}. {d.get('title', '')[:50]}{'...' if len(d.get('title', '')) > 50 else ''}\n"
                                yield f"     URL: {d.get('url', '')[:60]}\n"
                                yield f"     Score: {d.get('score', 0):.3f}\n"
                                
                                # Check for match
                                title_match = is_title_match(orig_title, d.get('title', ''))
                                url_match = is_url_match(orig_url, d.get('url', ''))
                                
                                if title_match and url_match:
                                    yield f"     🎯 ORIGINAL DOCUMENT FOUND (Title + URL Match)!\n"
                                elif title_match:
                                    yield f"     ⚠️  Title Match Only (URL differs)\n"
                                elif url_match:
                                    yield f"     ⚠️  URL Match Only (Title differs)\n"
                            
                            if len(docs) > 3:
                                yield f"     ... and {len(docs) - 3} more results\n"
                            
                            # Add all results to rows
                            for i, d in enumerate(docs[:top_n]):
                                is_title = 'YES' if is_title_match(orig_title, d.get('title','')) else 'NO'
                                is_url = 'YES' if is_url_match(orig_url, d.get('url','')) else 'NO'
                                is_orig = 'YES' if (is_title == 'YES' and is_url == 'YES') else 'NO'
                                
                                rows.append({
                                    'Query_ID': qid if i == 0 else '',
                                    'Generated_Query': query if i == 0 else '',
                                    'Original_Title': orig_title if i == 0 else '',
                                    'Original_URL': orig_url if i == 0 else '',
                                    'Result_Rank': d.get('rank', i+1),
                                    'Result_Title': d.get('title',''),
                                    'Result_URL': d.get('url',''),
                                    'Result_Description': d.get('description','')[:300] if d.get('description') else '',
                                    'Search_Score': d.get('score', 0.0),
                                    'Is_Original_Match': is_orig,
                                    'Is_Title_Match': is_title,
                                    'Is_URL_Match': is_url
                                })
                        else:
                            yield f"❌ No results found\n"
                            failed_searches += 1
                            rows.append({
                                'Query_ID': qid,
                                'Generated_Query': query,
                                'Original_Title': orig_title,
                                'Original_URL': orig_url,
                                'Result_Rank': 0,
                                'Result_Title': 'NO_RESULTS_FOUND',
                                'Result_URL': '',
                                'Result_Description': '',
                                'Search_Score': 0.0,
                                'Is_Original_Match': 'NO',
                                'Is_Title_Match': 'NO',
                                'Is_URL_Match': 'NO'
                            })
                            
                    except Exception as e:
                        yield f"❌ Error: {str(e)[:80]}\n"
                        failed_searches += 1
                        rows.append({
                            'Query_ID': qid,
                            'Generated_Query': query,
                            'Original_Title': orig_title,
                            'Original_URL': orig_url,
                            'Result_Rank': -1,
                            'Result_Title': f'ERROR: {e}',
                            'Result_URL': '',
                            'Result_Description': '',
                            'Search_Score': 0.0,
                            'Is_Original_Match': 'NO',
                            'Is_Title_Match': 'NO',
                            'Is_URL_Match': 'NO'
                        })
                    
                    time.sleep(delay)
                    yield f"{'-'*60}\n"
                
                # Create results DataFrame
                results_df = pd.DataFrame(rows)
                
                # Calculate analysis
                original_matches = len(results_df[results_df['Is_Original_Match'] == 'YES'])
                original_ranks = results_df[results_df['Is_Original_Match'] == 'YES']['Result_Rank']
                
                # Print analysis to log
                yield f"\n{'='*60}\n"
                yield "📊 SEARCH EVALUATION ANALYSIS\n"
                yield f"{'='*60}\n"
                
                yield f"\n📈 BASIC STATISTICS:\n"
                yield f"   Total queries processed: {total_queries}\n"
                yield f"   Successful searches: {successful_searches}\n"
                yield f"   Failed searches: {failed_searches}\n"
                yield f"   Success rate: {(successful_searches/total_queries)*100:.1f}%\n"
                yield f"   Original documents found: {original_matches}\n"
                yield f"   Original match rate: {(original_matches/total_queries)*100:.1f}%\n"
                
                yield f"\n⭐ Original document ranking analysis:\n"
                if len(original_ranks) > 0:
                    rank_1 = len(original_ranks[original_ranks == 1])
                    rank_3 = len(original_ranks[original_ranks <= 3])
                    rank_5 = len(original_ranks[original_ranks <= 5])
                    rank_10 = len(original_ranks[original_ranks <= 10])
                    rank_20 = len(original_ranks[original_ranks <= 20])
                    rank_30 = len(original_ranks[original_ranks <= 30])
                    rank_40 = len(original_ranks[original_ranks <= 40])
                    rank_50 = len(original_ranks[original_ranks <= 50])
                    avg_rank = original_ranks.mean()
                    
                    yield f"   Found at rank 1: {rank_1}\n"
                    yield f"   Found at rank 1-3: {rank_3}\n"
                    yield f"   Found at rank 1-5: {rank_5}\n"
                    yield f"   Found at rank 1-10: {rank_10}\n"
                    yield f"   Found at rank 1-20: {rank_20}\n"
                    yield f"   Found at rank 1-30: {rank_30}\n"
                    yield f"   Found at rank 1-40: {rank_40}\n"
                    yield f"   Found at rank 1-50: {rank_50}\n"
                    yield f"   Average rank: {avg_rank:.1f}\n"
                else:
                    yield f"   ❌ No original documents were found in search results\n"
                
                # Build analysis data for Excel
                analysis_data = []
                analysis_data.append({'Metric': 'BASIC STATISTICS', 'Value': '', 'Details': ''})
                analysis_data.append({'Metric': 'Total Queries Processed', 'Value': total_queries, 'Details': ''})
                analysis_data.append({'Metric': 'Successful Searches', 'Value': successful_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Failed Searches', 'Value': failed_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Success Rate', 'Value': f'{(successful_searches/total_queries)*100:.1f}%', 'Details': ''})
                analysis_data.append({'Metric': 'Original Documents Found', 'Value': original_matches, 'Details': ''})
                analysis_data.append({'Metric': 'Original Match Rate', 'Value': f'{(original_matches/total_queries)*100:.1f}%', 'Details': ''})
                analysis_data.append({'Metric': '', 'Value': '', 'Details': ''})
                
                analysis_data.append({'Metric': 'RANKING ANALYSIS (Original Documents)', 'Value': '', 'Details': ''})
                analysis_data.append({'Metric': 'Total Queries', 'Value': total_queries, 'Details': ''})
                
                if len(original_ranks) > 0:
                    analysis_data.append({'Metric': 'Found at Rank 1', 'Value': rank_1, 'Details': f'{(rank_1/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-3', 'Value': rank_3, 'Details': f'{(rank_3/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-5', 'Value': rank_5, 'Details': f'{(rank_5/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-10', 'Value': rank_10, 'Details': f'{(rank_10/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-20', 'Value': rank_20, 'Details': f'{(rank_20/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-30', 'Value': rank_30, 'Details': f'{(rank_30/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-40', 'Value': rank_40, 'Details': f'{(rank_40/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-50', 'Value': rank_50, 'Details': f'{(rank_50/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Average Rank', 'Value': f'{avg_rank:.1f}', 'Details': ''})
                else:
                    analysis_data.append({'Metric': 'No Original Documents Found', 'Value': 'N/A', 'Details': ''})
                
                analysis_data.append({'Metric': '', 'Value': '', 'Details': ''})
                
                # Top queries by score
                successful_results = results_df[results_df['Result_Rank'] > 0]
                if len(successful_results) > 0:
                    # Filter to only rows with query data
                    query_results = successful_results[successful_results['Generated_Query'] != '']
                    if len(query_results) > 0:
                        avg_scores = query_results.groupby('Generated_Query')['Search_Score'].mean().sort_values(ascending=False)
                        analysis_data.append({'Metric': 'TOP QUERIES BY AVERAGE SCORE', 'Value': '', 'Details': ''})
                        for i, (query, score) in enumerate(avg_scores.head(10).items()):
                            analysis_data.append({'Metric': f'Top {i+1}', 'Value': f'{score:.3f}', 'Details': str(query)[:100]})
                
                analysis_df = pd.DataFrame(analysis_data)
                
                # Determine output xlsx path
                if output_csv.endswith('.csv'):
                    output_xlsx = output_csv.replace('.csv', '.xlsx')
                else:
                    output_xlsx = output_csv + '.xlsx'
                
                # Write to Excel with two sheets
                try:
                    with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
                        analysis_df.to_excel(writer, sheet_name='Analysis', index=False)
                        results_df.to_excel(writer, sheet_name='Search Results', index=False)
                    
                    yield f"\n✅ Results saved to Excel: {output_xlsx}\n"
                    yield f"   📊 Sheet 1: Analysis (Summary statistics)\n"
                    yield f"   📋 Sheet 2: Search Results ({len(results_df)} rows)\n"
                except Exception as excel_err:
                    yield f"⚠️ Error writing Excel: {excel_err}\n"
                
                # Also save CSV
                results_df.to_csv(output_csv, index=False)
                yield f"✅ CSV also saved: {output_csv}\n"
                
                # Create download tokens
                xlsx_token = None
                csv_token = None
                
                if os.path.exists(output_xlsx):
                    try:
                        with open(output_xlsx, 'rb') as rf:
                            file_bytes = rf.read()
                        xlsx_token = store_temp_file(file_bytes, os.path.basename(output_xlsx))
                        yield f"TEMP_DOWNLOAD_TOKEN_XLSX: {xlsx_token}\n"
                    except Exception:
                        pass
                
                if os.path.exists(output_csv):
                    try:
                        with open(output_csv, 'rb') as rf:
                            file_bytes = rf.read()
                        csv_token = store_temp_file(file_bytes, os.path.basename(output_csv))
                        yield f"TEMP_DOWNLOAD_TOKEN: {csv_token}\n"
                    except Exception:
                        pass
                
                yield f"OUTPUT_CSV_FINAL: {output_csv}\n"
                yield f"OUTPUT_XLSX_FINAL: {output_xlsx}\n" if output_xlsx else ''
                yield "EXIT_CODE: 0\n"
                yield "STREAM_DONE\n"
                
            except Exception as e:
                yield f"❌ Error: {str(e)}\n"
                import traceback
                yield f"{traceback.format_exc()}\n"
                yield "EXIT_CODE: 1\n"
                yield "STREAM_DONE\n"
            finally:
                # Cleanup temp files
                try:
                    if os.path.exists(output_csv):
                        os.unlink(output_csv)
                except Exception:
                    pass
                try:
                    if output_xlsx and os.path.exists(output_xlsx):
                        os.unlink(output_xlsx)
                except Exception:
                    pass
                try:
                    if created_temp_input and os.path.exists(created_temp_input):
                        os.unlink(created_temp_input)
                except Exception:
                    pass
        
        return Response(generate(), mimetype='text/plain')
    
    except Exception as e:
        return Response(f"ERROR: {str(e)}\n", mimetype='text/plain', status=500)


if __name__ == '__main__':
    print("🚀 Starting Document Data Generation Server...")
    print(f"📁 Working directory: {BASE_DIR}")
    print(f"🌐 Server will be available at: http://localhost:5051")
    print("📋 Make sure your config.json and curl_input.json files are in the same directory")
    print("-" * 60)
    
    # launch.py sets DATAQUERY_NO_RELOADER=1 so process supervision does not
    # mistake Flask's reloader parent exit for a crash.
    use_reloader = os.environ.get('DATAQUERY_NO_RELOADER') != '1'
    app.run(debug=True, host='0.0.0.0', port=5051, use_reloader=use_reloader)