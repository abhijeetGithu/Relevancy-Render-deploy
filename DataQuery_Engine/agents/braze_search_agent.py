#!/usr/bin/env python3
"""
Dedicated SearchAgent for Braze (SearchUnify) endpoint.
Loads config from curl_input_braze.json, supports bearer token override,
and extracts top results from the SearchUnify response structure.
"""
import os
import json
import time
import requests
import html
from typing import List, Dict


class BrazeSearchAgent:
    """Agent for performing search queries against Braze SearchUnify API."""

    def __init__(self):
        """Initialize with config from curl_input_braze.json"""
        self.curl_config = self._load_curl_config()

        # Extract configuration
        self.base_url = self.curl_config.get("url", "")

        # Setup headers from config (preserve original casing)
        h = self.curl_config.get("headers", {}) or {}
        self.headers: Dict[str, str] = {str(k): v for k, v in h.items()}

        # Allow overriding Authorization bearer token via env var SEARCH_BEARER_TOKEN
        token = os.getenv("SEARCH_BEARER_TOKEN")
        if token:
            # Respect existing header key casing if present
            auth_key = next((k for k in self.headers.keys() if k.lower() == "authorization"), "authorization")
            self.headers[auth_key] = f"bearer {token}"

        # Cookies object in config (optional)
        self.cookies = self.curl_config.get("cookies", {}) or None

        # Body template
        self.body_template = self.curl_config.get("body", {}) or {}

    def _load_curl_config(self) -> Dict:
        """Load curl configuration from curl_input_braze.json"""
        try:
            root = os.path.dirname(os.path.dirname(__file__))
            curl_config_path = os.path.join(root, "curl_input_braze.json")
            with open(curl_config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load curl_input_braze.json: {e}")
            return {}

    def _generate_sid(self) -> str:
        """Generate a session ID"""
        return str(int(time.time() * 1000000))

    def search(self, search_string: str, results_per_page: int = 10) -> Dict:
        """
        Perform search query against Braze SearchUnify API using JSON payload

        Args:
            search_string: The search query string
            results_per_page: Number of results to return (default 10)

        Returns:
            Dictionary containing search results
        """
        try:
            payload = dict(self.body_template)
            payload["searchString"] = search_string
            # Keep both keys in sync if present
            if "resultsPerPage" in payload:
                payload["resultsPerPage"] = int(results_per_page)
            else:
                payload["resultsPerPage"] = int(results_per_page)
            if "pageSize" in payload:
                payload["pageSize"] = int(results_per_page)
            # Ensure sid exists
            payload["sid"] = payload.get("sid") or self._generate_sid()

            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                cookies=self.cookies,
                timeout=30,
            )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    print("Braze search returned non-JSON response")
                    return {}
            else:
                print(f"Braze Search API returned status code: {response.status_code}")
                print(f"Response: {response.text[:300]}...")
                return {}
        except Exception as e:
            print(f"Error performing Braze search: {e}")
            return {}

    def extract_documents(self, search_response: Dict, top_n: int = 10) -> List[Dict]:
        """
        Extract documents from SearchUnify response (result.hits)

        Args:
            search_response: Response from search API
            top_n: Number of top documents to extract

        Returns:
            List of dictionaries containing document info
        """
        documents: List[Dict] = []
        try:
            if "result" in search_response and "hits" in search_response["result"]:
                hits = search_response["result"]["hits"]
                for i, hit in enumerate(hits[:top_n]):
                    raw_score = hit.get("_score", 0)
                    doc = {
                        "rank": i + 1,
                        "title": "",
                        "url": "",
                        "description": "",
                        "score": 0 if raw_score in (None, "null") else float(raw_score) if raw_score else 0,
                    }

                    # Title: prefer clean highlight fields, then fallbacks
                    title_text = None
                    hl = hit.get("highlight") if isinstance(hit.get("highlight"), dict) else None
                    if hl:
                        if hl.get("TitleToDisplayString"):
                            v = hl["TitleToDisplayString"]
                            title_text = v[0] if isinstance(v, list) else v
                        elif hl.get("TitleToDisplay"):
                            v = hl["TitleToDisplay"]
                            title_text = v[0] if isinstance(v, list) else v
                        else:
                            for k, v in hl.items():
                                if "title" in k.lower() and v:
                                    title_text = v[0] if isinstance(v, list) else v
                                    break
                    if title_text:
                        s = str(title_text).replace("_____", "").replace("__.__._", "")
                        doc["title"] = s.strip()

                    # URL: try href, url, clientHref, es_id fallback
                    if hit.get("href"):
                        doc["url"] = str(hit["href"]).strip()
                    elif hit.get("url"):
                        doc["url"] = str(hit["url"]).strip()
                    elif hit.get("clientHref"):
                        doc["url"] = str(hit["clientHref"]).strip()
                    elif isinstance(hit.get("es_id"), str) and hit["es_id"].startswith("http"):
                        doc["url"] = str(hit["es_id"]).strip()

                    # Description: prefer SummaryToDisplay, Summary, metadata Summary, then body
                    desc_text = None
                    if hl:
                        if not desc_text and hl.get("SummaryToDisplay"):
                            v = hl["SummaryToDisplay"]
                            desc_text = v[0] if isinstance(v, list) else v
                        if not desc_text and hl.get("Summary"):
                            v = hl["Summary"]
                            desc_text = v[0] if isinstance(v, list) else v
                        if not desc_text and hl.get("body.en"):
                            v = hl["body.en"]
                            desc_text = v[0] if isinstance(v, list) else v

                    if not desc_text and hit.get("metadata"):
                        for meta in hit["metadata"]:
                            if meta.get("key") == "Summary" and meta.get("value"):
                                val = meta["value"]
                                if isinstance(val, list) and val:
                                    desc_text = val[0]
                                elif isinstance(val, str):
                                    desc_text = val
                                if desc_text:
                                    break

                    if desc_text:
                        s = str(desc_text).replace("_____", "").replace("__.__._", "")
                        doc["description"] = s.strip()

                    # Unescape HTML entities
                    if doc["title"]:
                        doc["title"] = html.unescape(doc["title"]).strip()
                    if doc["description"]:
                        doc["description"] = html.unescape(doc["description"]).strip()
                    if doc["url"]:
                        doc["url"] = html.unescape(doc["url"]).strip()

                    documents.append(doc)
            else:
                print("⚠️  Unexpected response structure - no 'result.hits' found")
                print(f"Available keys: {list(search_response.keys()) if search_response else 'None'}")
        except Exception as e:
            print(f"Error extracting documents from response: {e}")

        return documents

    def search_and_extract(self, search_string: str, top_n: int = 10) -> List[Dict]:
        """Convenience method to search then extract top documents"""
        print(f"🔍 Searching for: '{search_string}'")
        resp = self.search(search_string, results_per_page=top_n)
        if not resp:
            print("❌ No response from search API")
            return []
        docs = self.extract_documents(resp, top_n)
        print(f"✅ Found {len(docs)} documents")
        return docs

    def test_connection(self) -> bool:
        """Basic endpoint connectivity check"""
        try:
            r = self.search("test", results_per_page=1)
            return bool(r)
        except Exception:
            return False
