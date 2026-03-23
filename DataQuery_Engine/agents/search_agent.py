from typing import List, Dict
import os, json, uuid, time, requests, html, copy

class SearchAgent:
    """Generic search agent that derives EVERYTHING from editable config files.

    Users only need to modify:
      - curl_input.json -> contains url, headers, body skeleton (uid, accessToken, etc.)
      - config.json      -> (optional) can hold overrides such as default results_per_page.

    No code edits required when switching endpoints. The agent auto-detects whether to send
    JSON or form-urlencoded based on the Content-Type header inside curl_input.json.
    """

    def __init__(self, curl_file: str = "curl_input.json", config_file: str = "config.json"):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.base_dir = base_dir
        self.curl_path = os.path.join(base_dir, curl_file)
        self.config_path = os.path.join(base_dir, config_file)

        self.curl_config: Dict = self._safe_load_json(self.curl_path)
        self.app_config: Dict = self._safe_load_json(self.config_path)

        self.base_url = self.curl_config.get("url", "")
        body = self.curl_config.get("body", {}) or {}
        self.uid = body.get("uid") or body.get("userId") or ""
        self.access_token = body.get("accessToken") or body.get("token") or ""
        self.headers: Dict = self.curl_config.get("headers", {}) or {}

        # Provide sensible defaults if missing
        if not self.headers.get("Content-Type"):
            # Default to JSON
            self.headers["Content-Type"] = "application/json"

    # ---------- Internal helpers ----------
    def _safe_load_json(self, path: str) -> Dict:
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"⚠️  Could not load {path}: {e}")
        return {}

    def _generate_search_uid(self) -> str:
        return str(uuid.uuid4())

    def _generate_sid(self) -> str:
        return str(int(time.time() * 1000000))

    def _is_form_request(self) -> bool:
        ct = self.headers.get("Content-Type", "").lower()
        return "application/x-www-form-urlencoded" in ct or "form-urlencoded" in ct

    def _prepare_payload(self, search_string: str, results_per_page: int) -> Dict:
        """Return a deep-copied payload with searchString + pagination fields injected."""
        body = copy.deepcopy(self.curl_config.get("body", {}) or {})
        # Canonical search fields
        body["searchString"] = search_string
        # Prefer existing keys if present; otherwise insert
        for k in ("resultsPerPage", "pageSize"):
            if k in body:
                body[k] = results_per_page
        # If neither key existed, set resultsPerPage
        if "resultsPerPage" not in body and "pageSize" not in body:
            body["resultsPerPage"] = results_per_page
        # Always ensure uid & accessToken if available
        if self.uid:
            body["uid"] = self.uid
        if self.access_token:
            body["accessToken"] = self.access_token
        # Ensure a sid/searchUid if commonly required
        if "sid" not in body:
            body["sid"] = self._generate_sid()
        if "searchUid" not in body:
            body["searchUid"] = self._generate_search_uid()
        return body

    # ---------- Public API ----------
    def search(self, search_string: str, results_per_page: int = 10) -> Dict:
        if not self.base_url:
            print("❌ Missing base URL in curl_input.json (key: url)")
            return {}
        payload = self._prepare_payload(search_string, results_per_page)
        try:
            if self._is_form_request():
                # Convert list/dict fields (like aggregations) to JSON strings to mimic original curl form submissions
                form_data = {}
                for k, v in payload.items():
                    if isinstance(v, (dict, list)):
                        form_data[k] = json.dumps(v)
                    else:
                        form_data[k] = str(v) if v is not None else ""
                resp = requests.post(self.base_url, headers=self.headers, data=form_data, timeout=30)
            else:
                resp = requests.post(self.base_url, headers=self.headers, json=payload, timeout=30)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    print("⚠️  Response is not JSON")
                    return {}
            else:
                print(f"⚠️  Search API status {resp.status_code}")
                snippet = resp.text[:300] if resp.text else ''
                print(f"Body snippet: {snippet}")
                return {}
        except Exception as e:
            print(f"Error performing search: {e}")
            return {}

    def extract_top_documents(self, search_response: Dict, top_n: int = 10) -> List[Dict]:
        """
        Extract top N documents from search response
        
        Args:
            search_response: Response from search API
            top_n: Number of top documents to extract
            
        Returns:
            List of dictionaries containing document info
        """
        documents: List[Dict] = []
        try:
            if 'result' in search_response and 'hits' in search_response['result'] and isinstance(search_response['result']['hits'], list):
                hits = search_response['result']['hits']
                for i, hit in enumerate(hits[:top_n]):
                    # Extract document information using generic field extraction
                    # Initialize doc info, guard against null _score
                    raw_score = hit.get('_score', 0)
                    doc_info = {
                        'rank': i + 1,
                        'title': '',
                        'url': '',
                        'description': '',
                        'score': 0 if raw_score in (None, "null") else float(raw_score) if raw_score else 0,
                    }
                    
                    # Extract title - Alteryx specific logic
                    title_extracted = False
                    
                    # For Alteryx, prefer TitleToDisplayString from highlight (clean version)
                    if 'highlight' in hit and 'TitleToDisplayString' in hit['highlight'] and hit['highlight']['TitleToDisplayString']:
                        title_text = hit['highlight']['TitleToDisplayString'][0] if isinstance(hit['highlight']['TitleToDisplayString'], list) else hit['highlight']['TitleToDisplayString']
                        doc_info['title'] = str(title_text).strip()
                        title_extracted = True
                    
                    # Fallback to TitleToDisplay if TitleToDisplayString not available
                    if not title_extracted and 'highlight' in hit and 'TitleToDisplay' in hit['highlight'] and hit['highlight']['TitleToDisplay']:
                        title_text = hit['highlight']['TitleToDisplay'][0] if isinstance(hit['highlight']['TitleToDisplay'], list) else hit['highlight']['TitleToDisplay']
                        # Clean highlight markers from TitleToDisplay
                        title_text = str(title_text).replace('_____', '').replace('__.__._', '')
                        doc_info['title'] = title_text.strip()
                        title_extracted = True
                    
                    # Try other common title fields as fallback
                    if not title_extracted:
                        title_fields = [
                            'title', 'Title', 'TITLE',
                            '1_7_verizon_community___forum___subject',
                            'subject', 'Subject', 'name', 'Name'
                        ]
                        
                        # Try direct field access
                        for field in title_fields:
                            if field in hit and hit[field]:
                                doc_info['title'] = str(hit[field]).strip()
                                title_extracted = True
                                break
                        
                        # Try _source nested access
                        if not title_extracted and '_source' in hit:
                            for field in title_fields:
                                if field in hit['_source'] and hit['_source'][field]:
                                    doc_info['title'] = str(hit['_source'][field]).strip()
                                    title_extracted = True
                                    break
                    
                    # Extract URL - try multiple common field patterns across all sources
                    url_extracted = False
                    url_fields = [
                        # Common URL fields I() we have defined to get if we have different key for url 
                        'url', 'URL', 'href', 'Href', 'HREF',
                        'link', 'Link', 'LINK', 'uri', 'URI',
                        'clientHref', 'permalink', 'Permalink'
                    ]
                    
                    # Try direct field access
                    for field in url_fields:
                        if field in hit and hit[field]:
                            doc_info['url'] = str(hit[field]).strip()
                            url_extracted = True
                            break
                    
                    # Try _source nested access
                    if not url_extracted and '_source' in hit:
                        for field in url_fields:
                            if field in hit['_source'] and hit['_source'][field]:
                                doc_info['url'] = str(hit['_source'][field]).strip()
                                url_extracted = True
                                break
                    
                    # Extract description - Alteryx specific logic
                    desc_extracted = False
                    
                    # For Alteryx, first try metadata Summary field
                    if 'metadata' in hit and hit['metadata']:
                        for meta_item in hit['metadata']:
                            if meta_item.get('key') == 'Summary' and meta_item.get('value'):
                                summary_value = meta_item['value']
                                if isinstance(summary_value, list) and summary_value and summary_value[0]:
                                    doc_info['description'] = str(summary_value[0]).strip()
                                    desc_extracted = True
                                    break
                                elif isinstance(summary_value, str) and summary_value.strip():
                                    doc_info['description'] = str(summary_value).strip()
                                    desc_extracted = True
                                    break
                    
                    # Try SummaryToDisplay from highlight if metadata Summary not found
                    if not desc_extracted and 'highlight' in hit and 'SummaryToDisplay' in hit['highlight'] and hit['highlight']['SummaryToDisplay']:
                        summary = hit['highlight']['SummaryToDisplay'][0] if isinstance(hit['highlight']['SummaryToDisplay'], list) else hit['highlight']['SummaryToDisplay']
                        if str(summary).strip():
                            doc_info['description'] = str(summary).strip()
                            desc_extracted = True
                    
                    # Try Summary from highlight as fallback
                    if not desc_extracted and 'highlight' in hit and 'Summary' in hit['highlight'] and hit['highlight']['Summary']:
                        summary = hit['highlight']['Summary'][0] if isinstance(hit['highlight']['Summary'], list) else hit['highlight']['Summary']
                        if str(summary).strip():
                            doc_info['description'] = str(summary).strip()
                            desc_extracted = True
                    
                    # Try other common description fields as final fallback
                    if not desc_extracted:
                        desc_fields = [
                            'description', 'Description', 'DESCRIPTION',
                            'body', 'Body', 'BODY', 'content', 'Content', 'CONTENT',
                            'text', 'Text', 'TEXT', 'excerpt', 'Excerpt', 'snippet', 'Snippet',
                            '1_7_verizon_community___forum___body'
                        ]
                        
                        # Try direct field access
                        for field in desc_fields:
                            if field in hit and hit[field]:
                                doc_info['description'] = str(hit[field]).strip()
                                desc_extracted = True
                                break
                        
                        # Try _source nested access
                        if not desc_extracted and '_source' in hit:
                            for field in desc_fields:
                                if field in hit['_source'] and hit['_source'][field]:
                                    doc_info['description'] = str(hit['_source'][field]).strip()
                                    desc_extracted = True
                                    break
                    
                    # Clean up extracted text - remove HTML tags, highlight markers, and unescape entities
                    if doc_info['title']:
                        title = doc_info['title']
                        title = title.replace('___su-highlight-start___', '').replace('___su-highlight-end___', '')
                        title = title.replace('<span class="highlight">', '').replace('</span>', '')
                        title = title.replace('<em>', '').replace('</em>', '')
                        title = title.replace('<strong>', '').replace('</strong>', '')
                        # Clean Alteryx specific highlight markers
                        title = title.replace('_____', '').replace('__.__._', '')
                        title = html.unescape(title)
                        doc_info['title'] = title.strip()
                    
                    if doc_info['description']:
                        desc = doc_info['description']
                        desc = desc.replace('___su-highlight-start___', '').replace('___su-highlight-end___', '')
                        desc = desc.replace('<span class="highlight">', '').replace('</span>', '')
                        desc = desc.replace('<em>', '').replace('</em>', '')
                        desc = desc.replace('<strong>', '').replace('</strong>', '')
                        desc = desc.replace('<p>', '').replace('</p>', ' ')
                        desc = desc.replace('<br>', ' ').replace('<br/>', ' ')
                        # Clean Alteryx specific highlight markers
                        desc = desc.replace('_____', '').replace('__.__._', '')
                        desc = html.unescape(desc)
                        doc_info['description'] = desc.strip()

                    if doc_info['url']:
                        doc_info['url'] = html.unescape(str(doc_info['url']).strip())
                    
                    documents.append(doc_info)
            else:
                # Fallback: some APIs may return list directly at top
                if isinstance(search_response, list):
                    for i, hit in enumerate(search_response[:top_n]):
                        documents.append({'rank': i+1, 'title': str(hit)[:80], 'url': '', 'description': str(hit), 'score': 0})
                else:
                    print("⚠️  Unexpected response structure - no 'result.hits' list found")
        except Exception as e:
            print(f"Error extracting documents from response: {e}")
        return documents

    def search_and_extract(self, search_string: str, top_n: int = 50) -> List[Dict]:
        """
        Perform search and extract top documents in one call
        
        Args:
            search_string: The search query string
            top_n: Number of top documents to return
            
        Returns:
            List of top documents
        """
        print(f"🔍 Searching for: '{search_string}'")
        
        # Perform search
        search_response = self.search(search_string, results_per_page=top_n)
        
        if not search_response:
            print("❌ No response from search API")
            return []
        
        # Extract documents
        documents = self.extract_top_documents(search_response, top_n)
        
        print(f"✅ Found {len(documents)} documents")
        return documents

    def test_connection(self) -> bool:
        try:
            return bool(self.search("test", results_per_page=1))
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
