
#!/usr/bin/env python3
"""
Script to perform Vespa searches using queries from optimized_queries.csv and save results
Output is saved as search-result-vespa.csv in the same format as run_search_evaluation-verizon.py
"""

import pandas as pd
import sys
import os
import time
from typing import List, Dict

# Add parent directory to path to import agents module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.search_agent import SearchAgent

def perform_vespa_search_evaluation(input_csv: str, output_csv: str = None, delay_seconds: float = 1.0):
    """
    Read queries from optimized_queries.csv, perform Vespa searches, and save results
    Args:
        input_csv: Path to CSV file containing Generated_Query column
        output_csv: Path to output CSV file (optional)
        delay_seconds: Delay between API calls to avoid rate limiting
    """
    # Read input CSV
    try:
        df = pd.read_csv(input_csv)
        print(f"📄 Loaded {len(df)} queries from {input_csv}")
        print(f"📋 CSV columns: {list(df.columns)}")
    except Exception as e:
        print(f"❌ Error reading input CSV: {e}")
        return

    # Check if required column exists
    if 'Generated_Query' not in df.columns:
        print("❌ 'Generated_Query' column not found in CSV")
        print(f"Available columns: {list(df.columns)}")
        return

    # Set default output path
    if output_csv is None:
        base_name = input_csv.replace('.csv', '')
        output_csv = f"{base_name}-search-result-vespa.csv"

    # Initialize Vespa search agent
    try:
        search_agent = SearchAgent(endpoint_type="vespa")
        print("✅ Vespa search agent initialized")
        print(f"🔗 API URL: {search_agent.base_url}")
        print(f"🆔 UID: {search_agent.uid}")
    except Exception as e:
        print(f"❌ Error initializing Vespa search agent: {e}")
        return

    # Prepare results list
    all_results = []
    successful_searches = 0
    failed_searches = 0

    print(f"\n🔍 Starting Vespa search evaluation for {len(df)} queries...")
    print("=" * 80)

    # Process each query
    for idx, row in df.iterrows():
        query = row.get('Generated_Query', '').strip()
        original_title = row.get('Title', '')
        original_url = row.get('Document_URL', '')

        if not query:
            print(f"⚠️  Skipping row {idx + 1}: Empty query")
            failed_searches += 1
            continue

        print(f"\n📝 Query {idx + 1}/{len(df)}: '{query}'")
        print(f"📰 Original: {original_title[:60]}{'...' if len(original_title) > 60 else ''}")

        try:
            # Perform Vespa search
            documents = search_agent.search_and_extract(query, top_n=10)

            if documents:
                print(f"📋 Found {len(documents)} results:")
                successful_searches += 1

                # Add each result as a separate row
                for i, doc in enumerate(documents):
                    result_row = {
                        'Query_ID': idx + 1 if i == 0 else '',  # Only show Query_ID in first row
                        'Generated_Query': query if i == 0 else '',  # Only show query in first row
                        'Original_Title': original_title if i == 0 else '',  # Only show title in first row
                        'Original_URL': original_url if i == 0 else '',  # Only show URL in first row
                        'Result_Rank': doc.get('rank', i+1),
                        'Result_Title': doc.get('title', ''),
                        'Result_URL': doc.get('url', ''),
                        'Result_Description': doc.get('description', '')[:300] + '...' if len(doc.get('description', '')) > 300 else doc.get('description', ''),
                        'Search_Score': doc.get('score', 0.0),
                        'Is_Original_Match': 'YES' if doc.get('url', '') == original_url else 'NO'
                    }
                    all_results.append(result_row)

                    # Show first 3 results
                    if i < 3:
                        print(f"  {doc.get('rank', i+1)}. {doc.get('title', '')[:50]}{'...' if len(doc.get('title', '')) > 50 else ''}")
                        print(f"     URL: {doc.get('url', '')}")
                        print(f"     Score: {doc.get('score', 0.0):.3f}")
                        if doc.get('url', '') == original_url:
                            print(f"     ⭐ ORIGINAL DOCUMENT FOUND!")
                    elif i == 3:
                        print(f"     ... and {len(documents) - 3} more results")

            else:
                print("❌ No results found")
                failed_searches += 1
                # Add empty result row to maintain record
                result_row = {
                    'Query_ID': idx + 1,
                    'Generated_Query': query,
                    'Original_Title': original_title,
                    'Original_URL': original_url,
                    'Result_Rank': 0,
                    'Result_Title': 'NO_RESULTS_FOUND',
                    'Result_URL': '',
                    'Result_Description': '',
                    'Search_Score': 0.0,
                    'Is_Original_Match': 'NO'
                }
                all_results.append(result_row)

        except Exception as e:
            print(f"❌ Error searching for query '{query}': {e}")
            failed_searches += 1
            # Add error result row
            result_row = {
                'Query_ID': idx + 1,
                'Generated_Query': query,
                'Original_Title': original_title,
                'Original_URL': original_url,
                'Result_Rank': -1,
                'Result_Title': f'ERROR: {str(e)}',
                'Result_URL': '',
                'Result_Description': '',
                'Search_Score': 0.0,
                'Is_Original_Match': 'NO'
            }
            all_results.append(result_row)

        # Add delay to avoid rate limiting
        if delay_seconds > 0 and idx < len(df) - 1:
            print(f"⏳ Waiting {delay_seconds}s before next query...")
            time.sleep(delay_seconds)

        print("-" * 60)

    # Save results to CSV
    if all_results:
        results_df = pd.DataFrame(all_results)
        try:
            results_df.to_csv(output_csv, index=False)
            print(f"\n✅ Vespa search results saved to: {output_csv}")
            print(f"📊 Total result rows: {len(results_df)}")
        except Exception as e:
            print(f"❌ Error saving results: {e}")
    else:
        print("❌ No results to save")

if __name__ == "__main__":
    # Default paths
    input_csv = '/home/abhijeetsingh1/RelevanceEvaluator-Vespa_verizon/search_relevance_project/optimized_queries.csv'
    output_csv = '/home/abhijeetsingh1/RelevanceEvaluator-Vespa_verizon/search_relevance_project/search-result-vespa.csv'

    print("🚀 VESPA SEARCH RELEVANCE EVALUATION")
    print("=" * 50)
    print(f"Input CSV: {input_csv}")
    print(f"Output CSV: {output_csv}")

    # Check if input file exists
    if not os.path.exists(input_csv):
        print(f"❌ Input file not found: {input_csv}")
        print("Please ensure optimized_queries.csv exists with columns: Title, Document_URL, Generated_Query")
        sys.exit(1)

    print("\n" + "="*80)
    print("🔍 PERFORMING VESPA SEARCH EVALUATION")
    print("="*80)

    # Perform Vespa search evaluation
    perform_vespa_search_evaluation(input_csv, output_csv, delay_seconds=0.5)
