"""
Community page document extraction script.

Similar to main.py but uses community page API (form-encoded POST)
and curl_community.json template instead of curl_input.json.
"""
import json
import sys
import os
import csv

from utils.community_request_handler import (
    load_community_template,
    hit_community_request,
)


def main():
    # Load config
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

    # Extract config with defaults
    regenerate_queries = config.get("REGENERATE_QUERIES", "yes").lower()
    append_to_csv = config.get("APPEND_TO_CSV", False)
    multi_source_config = config.get("MULTI_SOURCE_CONFIG", {})

    # Determine output path
    csv_filename = None
    for a in sys.argv[1:]:
        if a.startswith('--output='):
            csv_filename = a.split('=', 1)[1].strip()
            break
    if not csv_filename:
        csv_filename = os.environ.get('OUTPUT_CSV')
    if not csv_filename:
        csv_filename = os.path.join(os.path.dirname(__file__), "output.csv")

    # Check if output file already exists and regeneration is disabled
    if os.path.exists(csv_filename) and regenerate_queries in ["no", "false", "0"]:
        print(f"✅ Output file '{csv_filename}' already exists and REGENERATE_QUERIES is set to '{config.get('REGENERATE_QUERIES')}'.")
        print("Skipping query generation. Set REGENERATE_QUERIES to 'yes' in config.json to regenerate.")
        return

    # Load community curl template
    curl_community_path = os.path.join(os.path.dirname(__file__), "curl_community.json")
    try:
        community_template = load_community_template(curl_community_path)
    except FileNotFoundError:
        print(f"❌ curl_community.json not found at {curl_community_path}. Please make sure the file exists.")
        return
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing curl_community.json: {e}")
        return

    print(f"📝 Generating multi-source documents (COMMUNITY MODE) and writing to '{csv_filename}'...\n")
    print(f"🔗 Using community template URL: {community_template.get('url', 'N/A')[:80]}...")

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

            print(f"\n🔍 Processing source: {source_name}")
            print(f"📊 Target documents: {document_count}")

            # Get results per page from template config
            template_config = community_template.get('config', {})
            results_per_page = template_config.get('documents_per_page', 10)
            
            # Update aggregations if present in source config
            if 'aggregations' in source_config:
                community_template['body']['aggregations'] = source_config['aggregations']
                print(f"🔧 Using aggregations: {source_config['aggregations']}")

            print(f"🔧 Results per page: {results_per_page}")

            # Auto-discover total document count via probe request
            FALLBACK_MAX_OFFSET = 500
            effective_max_offset = FALLBACK_MAX_OFFSET
            try:
                import requests as _req
                probe_url = community_template.get('url', '')
                probe_body = community_template.get('body', {}).copy()
                probe_body['from'] = 0
                probe_body.pop('pageNo', None)
                probe_headers = community_template.get('headers', {})
                probe_resp = _req.post(probe_url, headers=probe_headers, json=probe_body, timeout=15)
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
                    print(f"\n--- Community API Call for {source_name}, offset={current_offset} ({documents_collected + 1}/{document_count}) ---")
                    title, description, doc_url = hit_community_request(
                        community_template,
                        current_offset,
                        results_per_page=results_per_page,
                        search_string=""  # Empty search for extraction
                    )

                    if title and doc_url:
                        if doc_url in seen_urls:
                            print(f"⚠️ DUPLICATE: Skipping document with same URL (already collected)")
                            print(f"   URL: {doc_url[:80]}{'...' if len(doc_url) > 80 else ''}")
                            duplicate_count += 1
                        else:
                            seen_urls.add(doc_url)

                            if not description:
                                description = ""

                            writer.writerow([source_name, current_offset, title, description, doc_url])
                            csvfile.flush()
                            documents_collected += 1

                            print(f"✅ SUCCESS: Got document {documents_collected}/{document_count}")
                            print(f"📄 Title: {title[:60]}{'...' if len(title) > 60 else ''}")
                            print(f"📄 URL: {doc_url}")
                            failed_requests = 0
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

    # Print summary
    try:
        with open(csv_filename, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            row_count = sum(1 for row in reader) - 1
        print(f"📊 Total documents across all sources: {row_count}")
    except Exception as e:
        print(f"⚠️ Could not count total documents: {e}")


if __name__ == "__main__":
    main()
