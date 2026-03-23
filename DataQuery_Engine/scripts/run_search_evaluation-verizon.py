#!/usr/bin/env python3
"""
Script to perform searches using queries from optimized_queries.csv and save results
Updated to work with the correct optimized_queries.csv format (Title, Document_URL, Generated_Query)
"""

import pandas as pd
import sys
import os
import time
from typing import List, Dict, Union, Optional

# Add parent directory to path to import agents module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.search_agent import SearchAgent
import re

def clean_title_for_matching(title: str) -> str:
    """
    Clean title for matching by removing prefixes like [ABUSE] By:, [Filter: smut], etc.
    
    Args:
        title: Original title string
        
    Returns:
        Cleaned title string
    """
    if not title:
        return ""
    
    # Remove content within square brackets at the beginning of the title
    # This handles patterns like [ABUSE] By:, [Filter: smut], [Filter: Fraud], etc.
    cleaned = re.sub(r'^\[.*?\]\s*(?:By:\s*)?', '', title.strip())
    
    # Additional cleanup - remove "By:" if it appears at the start after bracket removal
    cleaned = re.sub(r'^By:\s*', '', cleaned.strip())
    
    return cleaned.strip()

def clean_url_for_matching(url: str) -> str:
    """
    Clean URL for matching by normalizing and removing common variations
    
    Args:
        url: Original URL string
        
    Returns:
        Cleaned URL string for comparison
    """
    if not url:
        return ""
    
    # Convert to lowercase for case-insensitive comparison
    cleaned = url.lower().strip()
    
    # Remove protocol (http://, https://)
    cleaned = re.sub(r'^https?://', '', cleaned)
    
    # Remove www. prefix
    cleaned = re.sub(r'^www\.', '', cleaned)
    
    # Remove trailing slash
    cleaned = cleaned.rstrip('/')
    
    # Remove query parameters and fragments for basic matching
    cleaned = re.sub(r'[?#].*$', '', cleaned)
    
    return cleaned


def load_queries_dataframe(
    path: str,
    sheet_name: Union[int, str] = 0,
) -> pd.DataFrame:
    """Load query rows from CSV or Excel (.xlsx / .xlsm / .xls)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    if ext == ".xls":
        return pd.read_excel(path, sheet_name=sheet_name)
    return pd.read_csv(path)


def _strip_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace from headers (e.g. 'Query ' → 'Query')."""
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _resolve_column(df: pd.DataFrame, requested: Optional[str]) -> Optional[str]:
    """
    Map a user-selected column name to a column in df.
    Tries exact match (after strip), then case-insensitive match.
    """
    if requested is None:
        return None
    req = str(requested).strip()
    if not req:
        return None
    cols = list(df.columns)
    if req in cols:
        return req
    low = req.lower()
    for c in cols:
        if str(c).lower() == low:
            return c
    return None


def perform_search_evaluation(
    input_csv: str,
    output_csv: str = None,
    delay_seconds: float = 1.0,
    query_column: str = "Generated_Query",
    title_column: str = "Title",
    url_column: str = "Document_URL",
    excel_sheet_name: Union[int, str] = 0,
):
    """
    Read queries from optimized_queries.csv, perform searches, and save results

    Args:
        input_csv: Path to CSV or Excel file containing query column
        output_csv: Path to output CSV file (optional)
        delay_seconds: Delay between API calls to avoid rate limiting
        query_column: Name of the column containing search queries (default: 'Generated_Query')
        title_column: Name of the column containing expected titles (default: 'Title')
        url_column: Name of the column containing expected URLs (default: 'Document_URL')
        excel_sheet_name: Sheet index (0-based) or name when input is Excel
    """
    try:
        df = load_queries_dataframe(input_csv, sheet_name=excel_sheet_name)
        df = _strip_column_names(df)
        print(f"📄 Loaded {len(df)} queries from {input_csv}")
        print(f"📋 Columns (whitespace trimmed from headers): {list(df.columns)}")
    except Exception as e:
        print(f"❌ Error reading input file: {e}")
        return

    # Query-only uploads: no expected title/URL — export Query + result title/URL only.
    query_only = title_column is None and url_column is None

    q_requested = query_column
    query_column = _resolve_column(df, query_column)
    if not query_column:
        print(f"❌ Query column not found (selected: '{q_requested}')")
        print(f"Available columns: {list(df.columns)}")
        return
    if query_column != str(q_requested).strip():
        print(f"📌 Resolved query column: '{q_requested}' → '{query_column}'")

    if not query_only:
        t_req, u_req = title_column, url_column
        title_column = _resolve_column(df, title_column)
        url_column = _resolve_column(df, url_column)
        if not title_column:
            print(f"❌ Expected title column not found (selected: '{t_req}')")
            print(f"Available columns: {list(df.columns)}")
            return
        if not url_column:
            print(f"❌ Expected URL column not found (selected: '{u_req}')")
            print(f"Available columns: {list(df.columns)}")
            return
        if title_column != str(t_req).strip():
            print(f"📌 Resolved title column: '{t_req}' → '{title_column}'")
        if url_column != str(u_req).strip():
            print(f"📌 Resolved URL column: '{u_req}' → '{url_column}'")

    print(
        f"📝 Using columns - Query: '{query_column}', Title: '{title_column or 'None'}', URL: '{url_column or 'None'}'"
    )
    if query_only:
        print("📋 Query-only mode: Excel/CSV will include Query, Search Result Title, Search Result URL only.")

    # Set default output path
    if output_csv is None:
        base_name = input_csv.replace('.csv', '')
        output_csv = f"{base_name}_search_results.csv"
    
    # Initialize search agent
    try:
        search_agent = SearchAgent()
        print("✅ Search agent initialized")
        print(f"🔗 API URL: {search_agent.base_url}")
        print(f"🆔 UID: {search_agent.uid}")
        
        # Test connection with a simple search
        print("🔍 Testing API connection...")
        test_results = search_agent.search_and_extract("test", top_n=1)
        if test_results:
            print("✅ SearchUnify API coqueries.csv exists with columns: Title, Document_URL, nnection successful")
        else:
            print("⚠️  API test returned no results, but connection seems OK")
        
    except Exception as e:
        print(f"❌ Error initializing search agent: {e}")
        return
    
    # Prepare results list
    all_results = []
    successful_searches = 0
    failed_searches = 0
    
    print(f"\n🔍 Starting search evaluation for {len(df)} queries...")
    print("=" * 80)
    
    # Process each query
    for idx, row in df.iterrows():
        query = str(row.get(query_column, '')).strip()
        if title_column and title_column in df.columns:
            _ot = row.get(title_column, '')
            original_title = '' if pd.isna(_ot) else str(_ot).strip()
        else:
            original_title = ''
        if url_column and url_column in df.columns:
            _ou = row.get(url_column, '')
            original_url = '' if pd.isna(_ou) else str(_ou).strip()
        else:
            original_url = ''
        
        if not query:
            print(f"⚠️  Skipping row {idx + 1}: Empty query")
            failed_searches += 1
            continue
        
        print(f"\n📝 Query {idx + 1}/{len(df)}: '{query}'")
        if not query_only:
            print(f"📰 Original: {original_title[:60]}{'...' if len(original_title) > 60 else ''}")

        try:
            # Perform search
            documents = search_agent.search_and_extract(query, top_n=50)

            if documents:
                print(f"📋 Found {len(documents)} results:")
                successful_searches += 1

                for i, doc in enumerate(documents):
                    if query_only:
                        all_results.append({
                            'Query': query if i == 0 else '',
                            'Search Result Title': doc['title'],
                            'Search Result URL': doc['url'],
                            '_Result_Rank': doc['rank'],
                            '_Search_Score': doc['score'],
                        })
                        if i < 3:
                            print(f"  {doc['rank']}. {doc['title'][:50]}{'...' if len(doc['title']) > 50 else ''}")
                            print(f"     URL: {doc['url']}")
                            print(f"     Score: {doc['score']:.3f}")
                        elif i == 3:
                            print(f"     ... and {len(documents) - 3} more results")
                        continue

                    is_title_match = 'NO'
                    if original_title and doc['title']:
                        cleaned_result_title = clean_title_for_matching(doc['title'])
                        cleaned_original_title = clean_title_for_matching(original_title)
                        if cleaned_original_title.lower().strip() == cleaned_result_title.lower().strip():
                            is_title_match = 'YES'

                    is_url_match = 'NO'
                    if original_url and doc['url']:
                        if original_url.strip() == doc['url'].strip():
                            is_url_match = 'YES'

                    is_match = 'YES' if (is_title_match == 'YES' and is_url_match == 'YES') else 'NO'

                    result_row = {
                        'Query_ID': idx + 1 if i == 0 else '',
                        'Generated_Query': query if i == 0 else '',
                        'Original_Title': original_title if i == 0 else '',
                        'Original_URL': original_url if i == 0 else '',
                        'Result_Rank': doc['rank'],
                        'Result_Title': doc['title'],
                        'Result_URL': doc['url'],
                        'Result_Description': doc['description'][:300] + '...' if len(doc.get('description', '')) > 300 else doc.get('description', ''),
                        'Search_Score': doc['score'],
                        'Is_Original_Match': is_match,
                        'Is_Title_Match': is_title_match,
                        'Is_URL_Match': is_url_match
                    }
                    all_results.append(result_row)

                    if i < 3:
                        print(f"  {doc['rank']}. {doc['title'][:50]}{'...' if len(doc['title']) > 50 else ''}")
                        print(f"     URL: {doc['url']}")
                        print(f"     Score: {doc['score']:.3f}")

                        title_match = False
                        url_match = False

                        if original_title and doc['title']:
                            cleaned_result_title = clean_title_for_matching(doc['title'])
                            cleaned_original_title = clean_title_for_matching(original_title)
                            title_match = cleaned_original_title.lower().strip() == cleaned_result_title.lower().strip()

                        if original_url and doc['url']:
                            url_match = original_url.strip() == doc['url'].strip()

                        if title_match and url_match:
                            print(f"     🎯 ORIGINAL DOCUMENT FOUND (Title + URL Match)!")
                        elif title_match:
                            print(f"     ⚠️  Title Match Only (URL differs)")
                        elif url_match:
                            print(f"     ⚠️  URL Match Only (Title differs)")
                    elif i == 3:
                        print(f"     ... and {len(documents) - 3} more results")

            else:
                print("❌ No results found")
                failed_searches += 1
                if query_only:
                    all_results.append({
                        'Query': query,
                        'Search Result Title': 'NO_RESULTS_FOUND',
                        'Search Result URL': '',
                        '_Result_Rank': 0,
                        '_Search_Score': 0.0,
                    })
                else:
                    all_results.append({
                        'Query_ID': idx + 1,
                        'Generated_Query': query,
                        'Original_Title': original_title,
                        'Original_URL': original_url,
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
            print(f"❌ Error searching for query '{query}': {e}")
            failed_searches += 1
            if query_only:
                all_results.append({
                    'Query': query,
                    'Search Result Title': f'ERROR: {str(e)}',
                    'Search Result URL': '',
                    '_Result_Rank': -1,
                    '_Search_Score': 0.0,
                })
            else:
                all_results.append({
                    'Query_ID': idx + 1,
                    'Generated_Query': query,
                    'Original_Title': original_title,
                    'Original_URL': original_url,
                    'Result_Rank': -1,
                    'Result_Title': f'ERROR: {str(e)}',
                    'Result_URL': '',
                    'Result_Description': '',
                    'Search_Score': 0.0,
                    'Is_Original_Match': 'NO',
                    'Is_Title_Match': 'NO',
                    'Is_URL_Match': 'NO'
                })
        
        # Add delay to avoid rate limiting
        if delay_seconds > 0 and idx < len(df) - 1:
            print(f"⏳ Waiting {delay_seconds}s before next query...")
            time.sleep(delay_seconds)
        
        print("-" * 60)
    
    # Save results to Excel with two sheets
    if all_results:
        results_df = pd.DataFrame(all_results)

        try:
            total_queries = len(df)

            if output_csv.endswith('.csv'):
                output_xlsx = output_csv.replace('.csv', '.xlsx')
            else:
                output_xlsx = output_csv + '.xlsx'

            if query_only:
                qo_cols = ['Query', 'Search Result Title', 'Search Result URL']
                export_df = results_df[qo_cols]

                successful_mask = results_df['_Result_Rank'] > 0
                n_hit_rows = int(successful_mask.sum())
                results_hits = results_df[successful_mask].copy()
                results_hits['_QueryFill'] = results_hits['Query'].replace('', pd.NA).ffill()

                analysis_data = []
                analysis_data.append({
                    'Metric': 'QUERY-ONLY MODE',
                    'Value': '',
                    'Details': 'Search Results: Query, Search Result Title, Search Result URL only (no expected title/URL).',
                })
                analysis_data.append({'Metric': 'BASIC STATISTICS', 'Value': '', 'Details': ''})
                analysis_data.append({'Metric': 'Total Queries Processed', 'Value': total_queries, 'Details': ''})
                analysis_data.append({'Metric': 'Successful Searches', 'Value': successful_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Failed Searches', 'Value': failed_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Success Rate', 'Value': f'{(successful_searches/total_queries)*100:.1f}%', 'Details': ''})
                analysis_data.append({'Metric': 'Total Result Rows (with hits)', 'Value': n_hit_rows, 'Details': ''})
                if successful_searches > 0:
                    analysis_data.append({
                        'Metric': 'Avg Result Rows per Successful Query',
                        'Value': f'{(n_hit_rows / successful_searches):.1f}',
                        'Details': '',
                    })
                analysis_data.append({'Metric': '', 'Value': '', 'Details': ''})

                if len(results_hits) > 0:
                    avg_scores = results_hits.groupby('_QueryFill')['_Search_Score'].mean().sort_values(ascending=False)
                    analysis_data.append({'Metric': 'TOP QUERIES BY AVERAGE SCORE', 'Value': '', 'Details': ''})
                    for i, (q, score) in enumerate(avg_scores.head(10).items()):
                        analysis_data.append({'Metric': f'Top {i+1}', 'Value': f'{score:.3f}', 'Details': str(q)[:100]})

                analysis_df = pd.DataFrame(analysis_data)

                with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
                    analysis_df.to_excel(writer, sheet_name='Analysis', index=False)
                    export_df.to_excel(writer, sheet_name='Search Results', index=False)

                export_df.to_csv(output_csv, index=False)

                print(f"\n✅ Results saved to Excel: {output_xlsx}")
                print("   📊 Sheet 1: Analysis (query-only summary)")
                print(f"   📋 Sheet 2: Search Results — columns: {', '.join(qo_cols)} ({len(export_df)} rows)")
                print(f"✅ CSV also saved: {output_csv} (same three columns)")

                print(f"\n📈 SEARCH EVALUATION SUMMARY (query-only)")
                print(f"   Total queries processed: {total_queries}")
                print(f"   Successful searches: {successful_searches}")
                print(f"   Failed searches: {failed_searches}")
                print(f"   Success rate: {(successful_searches/total_queries)*100:.1f}%")
                if len(results_hits) > 0:
                    avg_scores = results_hits.groupby('_QueryFill')['_Search_Score'].mean().sort_values(ascending=False)
                    print(f"\n🏆 Top 5 queries by average search score:")
                    for i, (q, sc) in enumerate(avg_scores.head().items()):
                        print(f"  {i+1}. '{q}' (avg score: {sc:.3f})")

                print(f"\nOUTPUT_XLSX: {output_xlsx}")

            else:
                original_matches = len(results_df[results_df['Is_Original_Match'] == 'YES'])
                successful_results = results_df[results_df['Result_Rank'] > 0]

                analysis_data = []
                analysis_data.append({'Metric': 'BASIC STATISTICS', 'Value': '', 'Details': ''})
                analysis_data.append({'Metric': 'Total Queries Processed', 'Value': total_queries, 'Details': ''})
                analysis_data.append({'Metric': 'Successful Searches', 'Value': successful_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Failed Searches', 'Value': failed_searches, 'Details': ''})
                analysis_data.append({'Metric': 'Success Rate', 'Value': f'{(successful_searches/total_queries)*100:.1f}%', 'Details': ''})
                analysis_data.append({'Metric': 'Original Documents Found', 'Value': original_matches, 'Details': ''})
                analysis_data.append({'Metric': 'Original Match Rate', 'Value': f'{(original_matches/total_queries)*100:.1f}%', 'Details': ''})
                analysis_data.append({'Metric': '', 'Value': '', 'Details': ''})

                original_ranks = results_df[results_df['Is_Original_Match'] == 'YES']['Result_Rank']
                analysis_data.append({'Metric': 'RANKING ANALYSIS (Original Documents)', 'Value': '', 'Details': ''})
                analysis_data.append({'Metric': 'Total Queries', 'Value': total_queries, 'Details': ''})
                if len(original_ranks) > 0:
                    rank_1 = len(original_ranks[original_ranks == 1])
                    rank_3 = len(original_ranks[original_ranks <= 3])
                    rank_5 = len(original_ranks[original_ranks <= 5])
                    rank_10 = len(original_ranks[original_ranks <= 10])
                    rank_20 = len(original_ranks[original_ranks <= 20])
                    rank_30 = len(original_ranks[original_ranks <= 30])
                    rank_40 = len(original_ranks[original_ranks <= 40])
                    rank_50 = len(original_ranks[original_ranks <= 50])

                    analysis_data.append({'Metric': 'Found at Rank 1', 'Value': rank_1, 'Details': f'{(rank_1/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-3', 'Value': rank_3, 'Details': f'{(rank_3/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-5', 'Value': rank_5, 'Details': f'{(rank_5/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-10', 'Value': rank_10, 'Details': f'{(rank_10/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-20', 'Value': rank_20, 'Details': f'{(rank_20/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-30', 'Value': rank_30, 'Details': f'{(rank_30/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-40', 'Value': rank_40, 'Details': f'{(rank_40/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Found at Rank 1-50', 'Value': rank_50, 'Details': f'{(rank_50/total_queries)*100:.1f}%'})
                    analysis_data.append({'Metric': 'Average Rank', 'Value': f'{original_ranks.mean():.1f}', 'Details': ''})
                else:
                    analysis_data.append({'Metric': 'No Original Documents Found', 'Value': 'N/A', 'Details': ''})
                analysis_data.append({'Metric': '', 'Value': '', 'Details': ''})

                if len(successful_results) > 0:
                    avg_scores = successful_results.groupby('Generated_Query')['Search_Score'].mean().sort_values(ascending=False)
                    analysis_data.append({'Metric': 'TOP QUERIES BY AVERAGE SCORE', 'Value': '', 'Details': ''})
                    for i, (query, score) in enumerate(avg_scores.head(10).items()):
                        analysis_data.append({'Metric': f'Top {i+1}', 'Value': f'{score:.3f}', 'Details': query[:100]})

                analysis_df = pd.DataFrame(analysis_data)
                export_df = results_df

                with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
                    analysis_df.to_excel(writer, sheet_name='Analysis', index=False)
                    export_df.to_excel(writer, sheet_name='Search Results', index=False)

                export_df.to_csv(output_csv, index=False)

                print(f"\n✅ Results saved to Excel: {output_xlsx}")
                print(f"   📊 Sheet 1: Analysis (Summary statistics)")
                print(f"   📋 Sheet 2: Search Results ({len(export_df)} rows)")
                print(f"✅ CSV also saved: {output_csv}")

                print(f"\n📈 SEARCH EVALUATION SUMMARY")
                print(f"   Total queries processed: {total_queries}")
                print(f"   Successful searches: {successful_searches}")
                print(f"   Failed searches: {failed_searches}")
                print(f"   Success rate: {(successful_searches/total_queries)*100:.1f}%")
                print(f"   Original documents found: {original_matches}")
                print(f"   Original match rate: {(original_matches/total_queries)*100:.1f}%")

                if len(successful_results) > 0:
                    avg_scores = successful_results.groupby('Generated_Query')['Search_Score'].mean().sort_values(ascending=False)
                    print(f"\n🏆 Top 5 queries by average search score:")
                    for i, (query, score) in enumerate(avg_scores.head().items()):
                        print(f"  {i+1}. '{query}' (avg score: {score:.3f})")

                    original_found = results_df[results_df['Is_Original_Match'] == 'YES']
                    if len(original_found) > 0:
                        print(f"\n⭐ Queries that found their original documents:")
                        unique_original_queries = original_found.drop_duplicates(subset=['Generated_Query'])
                        for _, row in unique_original_queries.head().iterrows():
                            rank = original_found[original_found['Generated_Query'] == row['Generated_Query']]['Result_Rank'].iloc[0]
                            score = original_found[original_found['Generated_Query'] == row['Generated_Query']]['Search_Score'].iloc[0]
                            print(f"   '{row['Generated_Query']}' (rank: {rank}, score: {score:.3f})")

                print(f"\nOUTPUT_XLSX: {output_xlsx}")
                    
        except Exception as e:
            print(f"❌ Error saving results: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("❌ No results to save")

def analyze_search_results(results_csv: str):
    """
    Analyze the search results and provide detailed insights
    
    Args:
        results_csv: Path to search results CSV
    """
    try:
        df = pd.read_csv(results_csv)
        print(f"\n📊 DETAILED SEARCH RESULTS ANALYSIS")
        print("=" * 60)

        # Query-only export: Query, Search Result Title, Search Result URL
        if 'Search Result Title' in df.columns and 'Result_Rank' not in df.columns:
            total_rows = len(df)
            qfill = df['Query'].replace('', pd.NA).ffill()
            print(f"📈 Query-only export analysis:")
            print(f"   Total rows: {total_rows}")
            n_err = df['Search Result Title'].astype(str).str.startswith('ERROR:').sum()
            n_none = (df['Search Result Title'].astype(str).str.strip() == 'NO_RESULTS_FOUND').sum()
            print(f"   Rows with errors: {int(n_err)}")
            print(f"   Rows with no results marker: {int(n_none)}")
            unique_queries = qfill.nunique(dropna=True)
            print(f"   Distinct queries (filled): {int(unique_queries)}")
            return
        
        # Basic statistics (ground-truth / full schema)
        total_rows = len(df)
        successful_results = len(df[df['Result_Rank'] > 0])
        unique_queries = df['Query_ID'].nunique()
        original_matches = len(df[df['Is_Original_Match'] == 'YES'])
        
        print(f"📈 Basic Statistics:")
        print(f"   Total result rows: {total_rows}")
        print(f"   Successful results: {successful_results}")
        print(f"   Unique queries: {unique_queries}")
        print(f"   Original documents found: {original_matches}")
        
        # Results per query distribution
        results_per_query = df[df['Result_Rank'] > 0].groupby('Query_ID').size()
        if len(results_per_query) > 0:
            print(f"\n📊 Results per query distribution:")
            print(f"   Average: {results_per_query.mean():.1f}")
            print(f"   Min: {results_per_query.min()}")
            print(f"   Max: {results_per_query.max()}")
        
        # Score distribution
        scores = df[df['Result_Rank'] > 0]['Search_Score']
        if len(scores) > 0:
            print(f"\n🎯 Search score distribution:")
            print(f"   Average: {scores.mean():.3f}")
            print(f"   Median: {scores.median():.3f}")
            print(f"   Min: {scores.min():.3f}")
            print(f"   Max: {scores.max():.3f}")
        
        # Ranking analysis for original documents
        original_ranks = df[df['Is_Original_Match'] == 'YES']['Result_Rank']
        if len(original_ranks) > 0:
            print(f"\n⭐ Original document ranking analysis:")
            print(f"   Found at rank 1: {len(original_ranks[original_ranks == 1])}")
            print(f"   Found at rank 1-3: {len(original_ranks[original_ranks <= 3])}")
            print(f"   Found at rank 1-5: {len(original_ranks[original_ranks <= 5])}")
            print(f"   Found at rank 1-10: {len(original_ranks[original_ranks <= 10])}")
            print(f"   Found at rank 1-20: {len(original_ranks[original_ranks <= 20])}")
            print(f"   Found at rank 1-30: {len(original_ranks[original_ranks <= 30])}")
            print(f"   Found at rank 1-40: {len(original_ranks[original_ranks <= 40])}")
            print(f"   Found at rank 1-50: {len(original_ranks[original_ranks <= 50])}")
            print(f"   Average rank: {original_ranks.mean():.1f}")
        else:
            print(f"\n❌ No original documents were found in search results")
        
    except Exception as e:
        print(f"❌ Error analyzing results: {e}")

if __name__ == "__main__":
    """CLI entrypoint.
    Supported flags:
      --input=PATH         Path to queries CSV or Excel (.xlsx/.xlsm/.xls).
      --output=PATH        Output CSV path (defaults to <input>_search_results.csv).
      --delay=FLOAT        Delay seconds between queries (default 0.5).
      --sheet=NAME|N       Excel sheet name or 0-based index (default 0). Ignored for CSV.
      --no-analyze         Skip post-run analysis summary.
    If --input not supplied: pick latest queries_*.csv from query_outputs/.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    queries_dir = os.path.join(base_dir, 'query_outputs')
    input_csv = None
    output_csv = None
    delay = 0.5
    analyze = True
    query_column = 'Generated_Query'
    title_column = 'Title'
    url_column = 'Document_URL'
    excel_sheet_name: Union[int, str] = 0

    # Parse args
    for arg in sys.argv[1:]:
        if arg.startswith('--input='):
            input_csv = arg.split('=',1)[1].strip()
        elif arg.startswith('--output='):
            output_csv = arg.split('=',1)[1].strip()
        elif arg.startswith('--delay='):
            try:
                delay = float(arg.split('=',1)[1].strip())
            except ValueError:
                pass
        elif arg in ('--no-analyze','--no_analyze','--skip-analyze'):
            analyze = False
        elif arg in ('--no-ground-truth', '--query-only'):
            # Query-only sheet: do not read expected title/URL columns (avoids wrong defaults).
            title_column = None
            url_column = None
        elif arg.startswith('--query-column='):
            query_column = arg.split('=',1)[1].strip()
        elif arg.startswith('--title-column='):
            title_column = arg.split('=',1)[1].strip() or None
        elif arg.startswith('--url-column='):
            url_column = arg.split('=',1)[1].strip() or None
        elif arg.startswith('--sheet='):
            raw = arg.split('=', 1)[1].strip()
            if raw.isdigit():
                excel_sheet_name = int(raw)
            elif raw:
                excel_sheet_name = raw

    # Auto-pick latest queries file if input not provided
    if not input_csv:
        latest = None
        if os.path.isdir(queries_dir):
            for fname in os.listdir(queries_dir):
                if fname.startswith('queries_') and fname.endswith('.csv'):
                    full = os.path.join(queries_dir, fname)
                    if latest is None or os.path.getmtime(full) > os.path.getmtime(latest):
                        latest = full
        input_csv = latest

    print("🚀 NETSKOPE SEARCH RELEVANCE EVALUATION")
    print("=" * 50)
    if input_csv:
        print(f"Input CSV: {input_csv}")
    else:
        print("❌ No input queries CSV found (queries_*.csv). Provide --input explicitly.")
        sys.exit(1)

    if not os.path.exists(input_csv):
        print(f"❌ Input file not found: {input_csv}")
        print("Provide a valid queries CSV (columns: Title, Document_URL, Generated_Query)")
        sys.exit(1)

    if not output_csv:
        base_no_ext, _ = os.path.splitext(input_csv)
        output_csv = base_no_ext + '_search_results.csv'

    print(f"Output CSV: {output_csv}")
    print(f"Delay: {delay}s | Analyze after run: {analyze}")

    print("\n" + "="*80)
    print("🔍 PERFORMING SEARCH EVALUATION")
    print("="*80)

    perform_search_evaluation(
        input_csv,
        output_csv,
        delay_seconds=delay,
        query_column=query_column,
        title_column=title_column,
        url_column=url_column,
        excel_sheet_name=excel_sheet_name,
    )

    if analyze and os.path.exists(output_csv):
        analyze_search_results(output_csv)
        print(f"\n💾 Results saved to: {output_csv}")
        print("🎯 Use this data to evaluate search relevance and query effectiveness!")