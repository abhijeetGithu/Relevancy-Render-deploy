#!/usr/bin/env python3
"""
Dedicated SearchAgent for Alteryx Community endpoint.
Handles the specific response structure and field extraction for Alteryx search results.
"""
import os
import json
import time
import requests
import html
import urllib.parse
from typing import List, Dict, Optional


class AlteryxSearchAgent:
    """
    Dedicated agent for performing search queries against Alteryx Community SearchUnify API.
    """
    
    def __init__(self):
        """Initialize AlteryxSearchAgent with config from curl_input_alteryx.json"""
        self.curl_config = self._load_curl_config()
        
        # Extract configuration
        self.base_url = self.curl_config.get("url", "https://communitydev.alteryx.com/plugins/custom/alteryx/alteryxdev/searchUnify_Endpoint")
        
        body = self.curl_config.get("body", {})
        self.uid = body.get("uid", "a7f8ff43-b37c-11e9-ad2e-06908fe445c6")
        
        # Setup headers from config
        h = self.curl_config.get("headers", {}) or {}
        # Start with headers as-is from config (preserve original casing)
        self.headers: Dict[str, str] = {str(k): v for k, v in h.items()}

        def has_header(name: str) -> bool:
            ln = name.lower()
            return any(k.lower() == ln for k in self.headers.keys())

        def set_default(name: str, value: str):
            if not has_header(name):
                self.headers[name] = value

        # Provide sensible defaults if missing
        set_default("Accept", "*/*")
        set_default("Accept-Language", "en-GB,en-US;q=0.9,en;q=0.8")
        set_default("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        set_default("Origin", "https://communitydev.alteryx.com")
        set_default("Referer", "https://communitydev.alteryx.com")
        set_default("Sec-Fetch-Dest", "empty")
        set_default("Sec-Fetch-Mode", "cors")
        set_default("Sec-Fetch-Site", "same-origin")
        set_default("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
        set_default("sec-ch-ua", '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"')
        set_default("sec-ch-ua-mobile", "?0")
        set_default("sec-ch-ua-platform", '"Linux"')

        # Cookies object in config (optional)
        self.cookies = self.curl_config.get("cookies", {}) or None

        # Optional: override Authorization bearer from env without editing JSON
        token = os.getenv("SEARCH_BEARER_TOKEN")
        if token:
            # Find header key (case-insensitive) for authorization; else add canonical 'authorization'
            auth_key = next((k for k in self.headers.keys() if k.lower() == "authorization"), "authorization")
            self.headers[auth_key] = f"bearer {token}"

        # Optional behavior flags
        body_cfg = self.curl_config.get("body", {})
        # If true, URL-encode the query once before form encoding (produces %2520 like the UI network call)
        self.double_encode_query = bool(body_cfg.get("doubleEncodeSearchString", False))

    # (moved optional behavior flags into __init__ above)

    def _load_curl_config(self) -> Dict:
        """Load curl configuration from curl_input_alteryx.json"""
        try:
            root = os.path.dirname(os.path.dirname(__file__))
            # Allow switching configs via env var SEARCH_CONFIG ("alteryx" or "braze")
            which = os.getenv("SEARCH_CONFIG", "alteryx").strip().lower()
            fname = "curl_input_braze.json" if which == "braze" else "curl_input_alteryx.json"
            curl_config_path = os.path.join(root, fname)
            with open(curl_config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load curl_input_alteryx.json: {e}")
            return {}

    def _generate_sid(self) -> str:
        """Generate a session ID"""
        return str(int(time.time() * 1000000))

    def search(self, search_string: str, results_per_page: int = 10) -> Dict:
        """
        Perform search query against Alteryx SearchUnify API
        
        Args:
            search_string: The search query string
            results_per_page: Number of results to return (default 10)
            
        Returns:
            Dictionary containing search results
        """
        try:
            curl_body = self.curl_config.get("body", {})

            # Determine payload mode based on content type
            # Content-Type detection (case-insensitive)
            content_type = ""
            for k, v in self.headers.items():
                if k.lower() == "content-type":
                    content_type = (v or "").lower()
                    break
            is_json_payload = "application/json" in content_type or content_type.endswith("+json")

            # Optionally double-encode the query to mimic certain UIs
            q = urllib.parse.quote(search_string) if self.double_encode_query else search_string

            if is_json_payload:
                # Build JSON payload, starting from config body then overriding searchString/page size
                payload = dict(curl_body) if isinstance(curl_body, dict) else {}
                payload["searchString"] = q
                # Respect explicit resultsPerPage if present; otherwise set from arg
                payload["resultsPerPage"] = int(payload.get("resultsPerPage", results_per_page))
                # Some endpoints also use pageSize; keep it in sync if present
                if "pageSize" in payload:
                    payload["pageSize"] = int(payload.get("pageSize", results_per_page))

                response = requests.post(
                    self.base_url,
                    headers=self.headers,
                    json=payload,
                    cookies=self.cookies,
                    timeout=30,
                )
            else:
                # Construct form-encoded data exactly as per config
                form_data = {
                    "langAttr": curl_body.get("langAttr", ""),
                    "react": str(curl_body.get("react", 1)),
                    "isRecommendationsWidget": str(curl_body.get("isRecommendationsWidget", False)).lower(),
                    "searchString": q,
                    "from": str(curl_body.get("from", 0)),
                    # Prefer _score by default unless overridden in config
                    "sortby": curl_body.get("sortby", "_score"),
                    "orderBy": curl_body.get("orderBy", "desc"),
                    "pageNo": str(curl_body.get("pageNo", 1)),
                    "aggregations": json.dumps(curl_body.get("aggregations", [])),
                    "clonedAggregations": curl_body.get("clonedAggregations", ""),
                    # Default to 'external' category if not provided, to align with Knowledge results
                    "category": curl_body.get("category", "external"),
                    "uid": self.uid,
                    "resultsPerPage": str(results_per_page),
                    "exactPhrase": curl_body.get("exactPhrase", ""),
                    "withOneOrMore": curl_body.get("withOneOrMore", ""),
                    "withoutTheWords": curl_body.get("withoutTheWords", ""),
                    "isWildCard": str(curl_body.get("isWildCard", False)).lower(),
                    "pageSize": str(results_per_page),
                    "sid": curl_body.get("sid", self._generate_sid()),
                    "language": curl_body.get("language", "en"),
                    "mergeSources": str(curl_body.get("mergeSources", True)).lower(),
                    "versionResults": str(curl_body.get("versionResults", True)).lower(),
                    "suCaseCreate": str(curl_body.get("suCaseCreate", False)).lower(),
                    "visitedtitle": curl_body.get("visitedtitle", ""),
                    "paginationClicked": str(curl_body.get("paginationClicked", False)).lower(),
                    "email": curl_body.get("email", ""),
                    "getAutoTunedResult": str(curl_body.get("getAutoTunedResult", True)).lower(),
                    "getSimilarSearches": str(curl_body.get("getSimilarSearches", True)).lower(),
                    "smartFacets": str(curl_body.get("smartFacets", False)).lower(),
                    "showMoreSummary": str(curl_body.get("showMoreSummary", False)).lower(),
                    "minSummaryLength": str(curl_body.get("minSummaryLength", 100)),
                    "showContentTag": str(curl_body.get("showContentTag", True)).lower(),
                    "pagingAggregation": json.dumps(curl_body.get("pagingAggregation", [])),
                }

                # Remove any None values
                form_data = {k: v for k, v in form_data.items() if v is not None}

                response = requests.post(
                    self.base_url,
                    headers=self.headers,
                    data=form_data,
                    cookies=self.cookies,
                    timeout=30,
                )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    print("Alteryx search returned non-JSON response")
                    return {}
            else:
                print(f"Alteryx Search API returned status code: {response.status_code}")
                print(f"Response: {response.text[:300]}...")
                return {}
        except Exception as e:
            print(f"Error performing Alteryx search: {e}")
            return {}

    def extract_documents(self, search_response: Dict, top_n: int = 10) -> List[Dict]:
        """
        Extract documents from Alteryx search response
        
        Args:
            search_response: Response from search API
            top_n: Number of top documents to extract
            
        Returns:
            List of dictionaries containing document info
        """
        documents = []
        try:
            # Navigate through the response structure - Alteryx uses 'result.hits'
            if 'result' in search_response and 'hits' in search_response['result']:
                hits = search_response['result']['hits']
                for i, hit in enumerate(hits[:top_n]):
                    # Initialize document info
                    raw_score = hit.get('_score', 0)
                    doc_info = {
                        'rank': i + 1,
                        'title': '',
                        'url': '',
                        'description': '',
                        'score': 0 if raw_score in (None, "null") else float(raw_score) if raw_score else 0,
                    }

                    # Extract title - prefer TitleToDisplayString from highlight (clean version)
                    title_text = None
                    if isinstance(hit.get('highlight'), dict):
                        hl = hit['highlight']
                        if hl.get('TitleToDisplayString'):
                            title_text = hl['TitleToDisplayString'][0] if isinstance(hl['TitleToDisplayString'], list) else hl['TitleToDisplayString']
                        elif hl.get('TitleToDisplay'):
                            title_text = hl['TitleToDisplay'][0] if isinstance(hl['TitleToDisplay'], list) else hl['TitleToDisplay']
                        else:
                            # Fallback: any highlight key containing 'Title'
                            for k, v in hl.items():
                                if 'title' in k.lower() and v:
                                    title_text = v[0] if isinstance(v, list) else v
                                    break
                    if title_text:
                        title_text = str(title_text).replace('_____', '').replace('__.__._', '')
                        doc_info['title'] = title_text.strip()

                    # Extract URL from href field (prefer href, fallback to es_id if URL-like)
                    if hit.get('href'):
                        doc_info['url'] = str(hit['href']).strip()
                    elif isinstance(hit.get('es_id'), str) and hit['es_id'].startswith('http'):
                        doc_info['url'] = str(hit['es_id']).strip()

                    # Extract description: prefer clean summary fields in highlight, then metadata, then body
                    # 1) Try highlight SummaryToDisplay (cleaned summary the UI shows)
                    desc_text = None
                    if isinstance(hit.get('highlight'), dict):
                        hl = hit['highlight']
                        if hl.get('SummaryToDisplay'):
                            s = hl['SummaryToDisplay']
                            desc_text = s[0] if isinstance(s, list) else s
                        if not desc_text and hl.get('Summary'):
                            s = hl['Summary']
                            desc_text = s[0] if isinstance(s, list) else s
                        if not desc_text and hl.get('body.en'):
                            s = hl['body.en']
                            desc_text = s[0] if isinstance(s, list) else s

                    # 2) Fallback: metadata Summary field
                    if not desc_text and hit.get('metadata'):
                        for meta_item in hit['metadata']:
                            if meta_item.get('key') == 'Summary' and meta_item.get('value'):
                                summary_value = meta_item['value']
                                if isinstance(summary_value, list) and summary_value:
                                    desc_text = summary_value[0]
                                elif isinstance(summary_value, str):
                                    desc_text = summary_value
                                if desc_text:
                                    break

                    if desc_text:
                        desc_text = str(desc_text).replace('_____', '').replace('__.__._', '')
                        doc_info['description'] = desc_text.strip()

                    # Clean up extracted text
                    if doc_info['title']:
                        doc_info['title'] = html.unescape(doc_info['title']).strip()
                    if doc_info['description']:
                        doc_info['description'] = html.unescape(doc_info['description']).strip()
                    if doc_info['url']:
                        doc_info['url'] = html.unescape(str(doc_info['url']).strip())

                    documents.append(doc_info)
            else:
                print("⚠️  Unexpected response structure - no 'result.hits' found")
                print(f"Available keys: {list(search_response.keys()) if search_response else 'None'}")
        except Exception as e:
            print(f"Error extracting documents from response: {e}")

        return documents

    def search_and_extract(self, search_string: str, top_n: int = 10) -> List[Dict]:
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
        documents = self.extract_documents(search_response, top_n)
        
        print(f"✅ Found {len(documents)} documents")
        return documents

    def test_connection(self) -> bool:
        """
        Test if the search API is accessible
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            test_response = self.search("test", results_per_page=1)
            return bool(test_response)
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
