import json
import os
import requests

# Load config.json
def load_config(config_path="config.json"):
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"{config_path} not found.")
    
    with open(config_path, "r") as file:
        config = json.load(file)
    
    return config


def main():
    # Load config values
    config = load_config("config.json")

    MAX_PAGE = config.get("MAX_PAGE", 10)
    ITERATIONS = config.get("ITERATIONS", 5)
    TITLE_FIELD = config.get("TITLE_FIELD", "")
    DESCRIPTION_FIELD = config.get("DESCRIPTION_FIELD", "")

    print(f"MAX_PAGE: {MAX_PAGE}")
    print(f"ITERATIONS: {ITERATIONS}")
    print(f"TITLE_FIELD: {TITLE_FIELD}")
    print(f"DESCRIPTION_FIELD: {DESCRIPTION_FIELD}")

    # --- Your existing logic goes here ---
    for i in range(ITERATIONS):
        print(f"Iteration {i+1} of {ITERATIONS}")
        # Example: simulate fetching data
        print(f"Fetching page {i+1} / {MAX_PAGE}")
        # You can now use TITLE_FIELD & DESCRIPTION_FIELD in your API request



def hit_request(
    curl_data,
    from_offset,
    title_field,
    description_field,
    url_field,
    *,
    title_highlight_field: str | None = None,
    description_highlight_field: str | None = None,
):
    try: 
        url = curl_data.get("url")
        method = curl_data.get("method", "GET").upper()
        headers = curl_data.get("headers", {})
        body = curl_data.get("body", {}).copy()
        
        # Use 'from' parameter as the ONLY pagination mechanism
        body["from"] = from_offset
        # Calculate pageNo only for logging/tracking (not sent to server)
        results_per_page = body.get("resultsPerPage", 10)
        page_no = (from_offset // results_per_page) + 1
        # Ensure we do NOT send pageNo, rely solely on 'from'
        if "pageNo" in body:
            body.pop("pageNo", None)
        
        print(f"🔍 DEBUG: Making API request for from={from_offset} (derived page {page_no}, using 'from' only)")
        print(f"📤 Request URL: {url}")
        print(f"📤 Request method: {method}")
        print(f"📤 Pagination: from={body['from']}, resultsPerPage={body.get('resultsPerPage', 10)}")
        print(f"📤 Aggregations: {body.get('aggregations', [])}")
        
        response = None
        if method == "GET":
            response = requests.get(url, headers=headers, params=body)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=body)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        print(f"📥 Response status code: {response.status_code}")
        
        if response.status_code in (400, 404):
            print(f"⚠️ {response.status_code} at this offset - no results available here")
            return None, None, None
        
        if response.status_code != 200:
            print(f"❌ API Error Response: {response.text[:500]}")
            response.raise_for_status()
        
        resp_json = response.json()
        print(f"📥 Response structure: {list(resp_json.keys())}")
        
        # Debug response structure
        if "result" in resp_json:
            result = resp_json["result"]
            print(f"📊 Result keys: {list(result.keys())}")
            if "hits" in result:
                hits = result["hits"]
                print(f"📊 Number of hits: {len(hits)}")
                if hits:
                    first_hit = hits[0]
                    print(f"📊 First hit keys: {list(first_hit.keys())}")
                    
                    # Show a sample of available fields for debugging
                    sample_fields = {k: v for k, v in first_hit.items() if not k.startswith('_') and len(str(v)) < 100}
                    print(f"📊 Sample fields: {sample_fields}")
        
        # Extract from first object in result['hits']
        hits = resp_json.get("result", {}).get("hits", [])
        if hits and isinstance(hits, list):
            first_obj = hits[0]
            
            # Treat empty field names as absent so we rely on highlight/nested fallback
            eff_title_field = title_field if title_field else None
            eff_description_field = description_field if description_field else None

            # Try to extract fields - handle both direct field access and nested structure
            title = first_obj.get(eff_title_field) if eff_title_field else None
            description = first_obj.get(eff_description_field) if eff_description_field else None
            doc_url = first_obj.get(url_field)
            
            print(f"🔍 DEBUG: Looking for fields:")
            print(f"  Title field '{title_field}': {title}")
            print(f"  Description field '{description_field}': {description}")
            print(f"  URL field '{url_field}': {doc_url}")
            
            # If direct field access doesn't work, try looking for the field in highlights or other nested structures
            if not title or not description or not doc_url:
                print("🔍 DEBUG: Direct field access failed, searching in nested structures...")
                
                # Check highlights for title/description
                highlights = first_obj.get("highlight", {})
                if highlights:
                    print(f"📊 Highlight keys: {list(highlights.keys())}")
                    # Prefer correct mappings:
                    # - Title: TitleToDisplay (or TitleToDisplayString)
                    # - Description: SummaryToDisplay
                    if not title:
                        # Use configured highlight field first if provided
                        title_key_order = []
                        if title_highlight_field:
                            title_key_order.append(title_highlight_field)
                        title_key_order += ["TitleToDisplay", "TitleToDisplayString"]
                        title_highlight = None
                        for k in title_key_order:
                            if k in highlights and highlights.get(k):
                                title_highlight = highlights.get(k)
                                break
                        if title_highlight:
                            title = title_highlight[0] if isinstance(title_highlight, list) else title_highlight
                    if not description:
                        # Use configured highlight field first if provided
                        desc_key_order = []
                        if description_highlight_field:
                            desc_key_order.append(description_highlight_field)
                        desc_key_order += ["SummaryToDisplay"]
                        desc_highlight = None
                        for k in desc_key_order:
                            if k in highlights and highlights.get(k):
                                desc_highlight = highlights.get(k)
                                break
                        if desc_highlight:
                            description = desc_highlight[0] if isinstance(desc_highlight, list) else desc_highlight
                
                # Check direct fields with different naming patterns
                if not doc_url:
                    doc_url = first_obj.get("href") or first_obj.get("_id") or first_obj.get("es_id")
                
                if not title:
                    title_candidates = ["objName", "objLabel", "sourceName", "TitleToDisplayString"]
                    for candidate in title_candidates:
                        if first_obj.get(candidate):
                            title = first_obj.get(candidate)
                            break

                # If we still ended up with duplicate title/description, try to correct using highlights when available
                if title and description and title == description and highlights:
                    # Re-apply with priority to configured highlight fields
                    t = None
                    if title_highlight_field and highlights.get(title_highlight_field):
                        t = highlights.get(title_highlight_field)
                    t = t or highlights.get("TitleToDisplay") or highlights.get("TitleToDisplayString")
                    d = None
                    if description_highlight_field and highlights.get(description_highlight_field):
                        d = highlights.get(description_highlight_field)
                    d = d or highlights.get("SummaryToDisplay")
                    if t:
                        title = t[0] if isinstance(t, list) else t
                    if d:
                        description = d[0] if isinstance(d, list) else d
                
                print(f"🔍 DEBUG: After nested search:")
                print(f"  Title: {title}")
                print(f"  Description: {description}")
                print(f"  URL: {doc_url}")
            
            # Final safety normalization
            if isinstance(title, list):
                title = title[0] if title else None
            if isinstance(description, list):
                description = description[0] if description else None

            if not title and title_highlight_field and highlights.get(title_highlight_field):
                # Last chance highlight fallback
                thv = highlights.get(title_highlight_field)
                title = thv[0] if isinstance(thv, list) else thv
            if not description and description_highlight_field and highlights.get(description_highlight_field):
                dhv = highlights.get(description_highlight_field)
                description = dhv[0] if isinstance(dhv, list) else dhv

            return title, description, doc_url
        else:
            print("❌ No hits found in response")
            return None, None, None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error in hit_request: {e}")
        print(f"❌ Response status: {getattr(e.response, 'status_code', 'N/A') if hasattr(e, 'response') else 'N/A'}")
        if hasattr(e, 'response') and e.response:
            print(f"❌ Response text: {e.response.text[:500]}")
        return None, None, None
    except Exception as e:
        print(f"❌ General error in hit_request: {e}")
        import traceback
        print(f"❌ Traceback: {traceback.format_exc()}")
        return None, None, None


if __name__ == "__main__":
    main()