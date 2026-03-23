import json
import sys
import os
from utils.request_handler import hit_request

# Force unbuffered stdout so streaming output works in subprocess pipes
if os.environ.get('PYTHONUNBUFFERED') != '1':
    sys.stdout.reconfigure(line_buffering=True)


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ config.json not found at {config_path}. Please make sure the file exists.")
        return
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing config.json: {e}")
        return

    # Extract config with defaults to prevent KeyError
    regenerate_queries = config.get("REGENERATE_QUERIES", "yes").lower()
    append_to_csv = config.get("APPEND_TO_CSV", False)
    multi_source_config = config.get("MULTI_SOURCE_CONFIG", {})

    # Determine output path: allow override via env var OUTPUT_CSV or CLI --output=
    csv_filename = None
    # CLI override has priority
    for a in sys.argv[1:]:
        if a.startswith('--output='):
            csv_filename = a.split('=', 1)[1].strip()
            break
    # Env var fallback
    if not csv_filename:
        csv_filename = os.environ.get('OUTPUT_CSV')
    # Default to project root output.csv
    if not csv_filename:
        csv_filename = os.path.join(os.path.dirname(__file__), "output.csv")

    # Check if output file already exists and regeneration is disabled
    if os.path.exists(csv_filename) and regenerate_queries in ["no", "false", "0"]:
        print(f"✅ Output file '{csv_filename}' already exists and REGENERATE_QUERIES is set to '{config.get('REGENERATE_QUERIES')}'.")
        print("Skipping query generation. Set REGENERATE_QUERIES to 'yes' in config.json to regenerate.")
        return

    # Load curl input
    curl_input_path = os.path.join(os.path.dirname(__file__), "curl_input.json")
    try:
        with open(curl_input_path, "r") as f:
            curl_data = json.load(f)
    except FileNotFoundError:
        print(f"❌ curl_input.json not found at {curl_input_path}. Please make sure the file exists.")
        return
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing curl_input.json: {e}")
        return

    print(f"📝 Generating multi-source documents and writing to '{csv_filename}'...\n")

    import csv
    
    # Determine if we should append or overwrite
    file_exists = os.path.exists(csv_filename)
    if append_to_csv and file_exists:
        print(f"📄 APPEND_TO_CSV=true: Appending to existing '{csv_filename}'")
        write_mode = "a"
        write_header = False
    else:
        if file_exists:
            print(f"📄 APPEND_TO_CSV=false: Overwriting existing '{csv_filename}'")
        else:
            print(f"📄 Creating new file '{csv_filename}'")
        write_mode = "w"
        write_header = True
    
    with open(csv_filename, write_mode, newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            writer.writerow(["Source", "FromOffset", "Title", "Description", "Document URL"])
        
        # Process each source
        for source_key, source_config in multi_source_config.items():
            source_name = source_config.get("display_name", source_key)
            document_count = source_config.get("document_count", 10)
            title_field = source_config.get("title_field", "TitleToDisplay")
            description_field = source_config.get("description_field", "SummaryToDisplay")
            url_field = source_config.get("url_field", "href")
            title_highlight_field = source_config.get("title_highlight_field")
            description_highlight_field = source_config.get("description_highlight_field")
            
            print(f"\n🔍 Processing source: {source_name}")
            print(f"📊 Target documents: {document_count}")
            
            results_per_page = curl_data["body"].get("resultsPerPage", 10)
            
            source_curl_data = curl_data.copy()
            source_curl_data["body"] = curl_data["body"].copy()
            source_curl_data["body"]["searchString"] = ""
            
            print(f"🔧 Using aggregations from curl_input: {source_curl_data['body'].get('aggregations', [])}")
            print(f"🔧 Results per page: {results_per_page}")
            
            # Auto-discover total document count via probe request
            FALLBACK_MAX_OFFSET = 500
            effective_max_offset = FALLBACK_MAX_OFFSET
            try:
                import requests as _req
                probe_body = source_curl_data["body"].copy()
                probe_body["from"] = 0
                probe_body.pop("pageNo", None)
                probe_resp = _req.post(
                    source_curl_data["url"],
                    headers=source_curl_data.get("headers", {}),
                    json=probe_body,
                    timeout=15
                )
                if probe_resp.status_code == 200:
                    probe_data = probe_resp.json()
                    total_available = probe_data.get("result", {}).get("total", None)
                    if total_available is not None and isinstance(total_available, (int, float)):
                        total_available = int(total_available)
                        effective_max_offset = total_available
                        print(f"📊 API reports {total_available} total documents available (auto-discovered)")
                    else:
                        print(f"⚠️ Could not read total from probe response, falling back to {FALLBACK_MAX_OFFSET}")
                else:
                    print(f"⚠️ Probe request returned {probe_resp.status_code}, falling back to {FALLBACK_MAX_OFFSET}")
            except Exception as probe_err:
                print(f"⚠️ Probe request failed ({probe_err}), falling back to {FALLBACK_MAX_OFFSET}")
            
            # Walk offsets in order from 0 upward until quota is met or API is exhausted
            documents_collected = 0
            seen_urls = set()
            failed_requests = 0
            duplicate_count = 0
            max_consecutive_failures = 15
            current_offset = 0

            print(
                f"📥 Sequential document fetch: offsets 0 → {effective_max_offset - 1} "
                f"(stop when {document_count} unique URLs collected, or no more results / limit reached)"
            )

            while documents_collected < document_count and current_offset < effective_max_offset:
                try:
                    print(f"\n--- API Call for {source_name}, offset={current_offset} ({documents_collected + 1}/{document_count}) ---")
                    title, description, doc_url = hit_request(
                        source_curl_data,
                        current_offset,
                        title_field,
                        description_field,
                        url_field,
                        title_highlight_field=title_highlight_field,
                        description_highlight_field=description_highlight_field,
                    )
                    
                    # Accept row if we have at least title & doc_url. Allow empty/None description.
                    if title and doc_url:
                        if doc_url in seen_urls:
                            print(f"⚠️ DUPLICATE: Skipping document with same URL (already collected)")
                            print(f"   URL: {doc_url[:80]}{'...' if len(doc_url) > 80 else ''}")
                            duplicate_count += 1
                        else:
                            seen_urls.add(doc_url)

                            if not description:
                                description = ""  # normalize None to empty string for CSV

                            writer.writerow([source_name, current_offset, title, description, doc_url])
                            csvfile.flush()
                            documents_collected += 1

                            print(f"✅ SUCCESS: Got document {documents_collected}/{document_count}")
                            print(f"📄 Title: {title[:60]}{'...' if len(title) > 60 else ''}")
                            print(f"📄 URL: {doc_url}")
                            failed_requests = 0  # Reset failure counter on success
                    else:
                        print(f"⚠️ No data at offset {current_offset}")
                        failed_requests += 1

                except Exception as e:
                    print(f"❌ Error at offset {current_offset}: {e}")
                    failed_requests += 1

                current_offset += 1

                if failed_requests >= max_consecutive_failures:
                    print(f"⚠️ Stopping after {max_consecutive_failures} consecutive failures. Collected {documents_collected} documents.")
                    break

            print(f"\n📈 Total documents collected for {source_name}: {documents_collected}/{document_count}")
            if duplicate_count > 0:
                print(f"⚠️ Skipped {duplicate_count} duplicate documents (same URL)")
            print("-" * 80)
    
    print(f"\n✅ Multi-source CSV output written to {csv_filename}")
    
    # Print summary - use csv.reader to count actual rows (handles multiline fields correctly)
    try:
        with open(csv_filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            row_count = sum(1 for row in reader) - 1  # Subtract header
        print(f"📊 Total documents across all sources: {row_count}")
    except Exception as e:
        print(f"⚠️ Could not count total documents: {e}")


if __name__ == "__main__":
    main()