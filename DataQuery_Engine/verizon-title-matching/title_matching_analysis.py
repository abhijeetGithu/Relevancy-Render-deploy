#!/usr/bin/env python3
"""
Title Matching Analysis Script

This script compares search results from Excel files with original titles to determine
matching performance, similar to run_search_evaluation-verizon.py but for offline analysis.

Input files:
- result_4826275435117545089.xlsx: Contains Generated_Query and top 10 Results
- verizon_queries_200_20250930_132102.xlsx: Contains Title and Generated_Query mapping

Output:
- Similar format to run_search_evaluation-verizon.py with ranking and matching analysis
"""

import pandas as pd
import re
import os
import sys
from typing import List, Dict, Tuple

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

def clean_result_for_matching(result: str) -> str:
    """
    Clean result for matching by removing R1, R2, R3, etc. prefixes.
    
    Args:
        result: Original result string (e.g., "R3 verizon online & fios tv terms of service announcements")
        
    Returns:
        Cleaned result string (e.g., "verizon online & fios tv terms of service announcements")
    """
    if not result:
        return ""
    
    # Remove R1, R2, R3, etc. prefixes at the beginning
    # Pattern matches: R + digits + optional space
    cleaned = re.sub(r'^R\d+\s*', '', result.strip())
    
    return cleaned.strip()

def titles_match(original_title: str, result_title: str) -> bool:
    """
    Check if two titles match after cleaning and case-insensitive comparison.
    
    Args:
        original_title: The original title from queries file
        result_title: The result title from results file (may have R1, R2, etc. prefix)
        
    Returns:
        True if titles match, False otherwise
    """
    if not original_title or not result_title:
        return False
    
    # Clean both titles
    cleaned_original = clean_title_for_matching(original_title)
    cleaned_result = clean_result_for_matching(result_title)
    
    # Case-insensitive comparison
    return cleaned_original.lower().strip() == cleaned_result.lower().strip()

def parse_results_file(results_file: str) -> Dict[str, List[str]]:
    """
    Parse the results Excel file to extract queries and their top 10 results.
    
    Args:
        results_file: Path to the results Excel file
        
    Returns:
        Dictionary mapping Generated_Query to list of results
    """
    print(f"📄 Loading results from {results_file}...")
    
    try:
        df = pd.read_excel(results_file)
        print(f"✅ Loaded {len(df)} rows from results file")
        
        query_results = {}
        current_query = None
        current_results = []
        
        for idx, row in df.iterrows():
            # Check if this row has a new query
            if pd.notna(row['Generated_Query']) and str(row['Generated_Query']).strip():
                # Save previous query results if we have them
                if current_query and current_results:
                    query_results[current_query] = current_results.copy()
                
                # Start new query
                current_query = str(row['Generated_Query']).strip()
                current_results = []
                
                # Add the first result from this row
                if pd.notna(row['Results']) and str(row['Results']).strip():
                    current_results.append(str(row['Results']).strip())
            
            # Check if this row has results (continuation of current query)
            elif current_query and pd.notna(row['Results']) and str(row['Results']).strip():
                current_results.append(str(row['Results']).strip())
        
        # Don't forget the last query
        if current_query and current_results:
            query_results[current_query] = current_results.copy()
        
        print(f"✅ Parsed {len(query_results)} unique queries")
        
        # Print sample for verification
        if query_results:
            sample_query = list(query_results.keys())[0]
            sample_results = query_results[sample_query]
            print(f"📋 Sample query: '{sample_query[:50]}...'")
            print(f"📋 Sample results ({len(sample_results)}): {sample_results[:3]}...")
        
        return query_results
        
    except Exception as e:
        print(f"❌ Error parsing results file: {e}")
        return {}

def load_queries_file(queries_file: str) -> pd.DataFrame:
    """
    Load the queries Excel file containing Title and Generated_Query mapping.
    
    Args:
        queries_file: Path to the queries Excel file
        
    Returns:
        DataFrame with Title and Generated_Query columns
    """
    print(f"📄 Loading queries from {queries_file}...")
    
    try:
        df = pd.read_excel(queries_file)
        print(f"✅ Loaded {len(df)} queries")
        print(f"📋 Columns: {list(df.columns)}")
        
        # Verify required columns exist
        if 'Title' not in df.columns or 'Generated_Query' not in df.columns:
            print(f"❌ Required columns missing. Expected: ['Title', 'Generated_Query']")
            return pd.DataFrame()
        
        return df
        
    except Exception as e:
        print(f"❌ Error loading queries file: {e}")
        return pd.DataFrame()

def perform_title_matching_analysis(results_file: str, queries_file: str, output_csv: str = None):
    """
    Perform title matching analysis similar to run_search_evaluation-verizon.py
    
    Args:
        results_file: Path to results Excel file
        queries_file: Path to queries Excel file  
        output_csv: Path to output CSV file (optional)
    """
    # Load data
    query_results = parse_results_file(results_file)
    if not query_results:
        print("❌ Failed to load results data")
        return
    
    df_queries = load_queries_file(queries_file)
    if df_queries.empty:
        print("❌ Failed to load queries data")
        return
    
    # Set default output path
    if output_csv is None:
        output_csv = "title_matching_results.csv"
    
    print(f"\n🔍 Starting title matching analysis...")
    print("=" * 80)
    
    # Prepare results list
    all_results = []
    successful_matches = 0
    failed_matches = 0
    total_queries = 0
    
    # Process each query from the queries file
    for idx, row in df_queries.iterrows():
        generated_query = str(row.get('Generated_Query', '')).strip()
        original_title = str(row.get('Title', '')).strip()
        original_url = str(row.get('Document_URL', '')).strip() if 'Document_URL' in row else ''
        
        total_queries += 1
        
        if not generated_query or generated_query == 'nan':
            print(f"⚠️  Skipping row {idx + 1}: Empty generated query")
            failed_matches += 1
            continue
        
        print(f"\n📝 Query {idx + 1}/{len(df_queries)}: '{generated_query[:60]}{'...' if len(generated_query) > 60 else ''}'")
        print(f"📰 Original title: {original_title[:60]}{'...' if len(original_title) > 60 else ''}")
        
        # Find matching results for this query
        matching_results = None
        for result_query, results in query_results.items():
            # Try exact match first
            if generated_query == result_query:
                matching_results = results
                break
            # Try case-insensitive match
            elif generated_query.lower() == result_query.lower():
                matching_results = results
                break
            # Try partial match (in case of minor formatting differences)
            elif generated_query.lower() in result_query.lower() or result_query.lower() in generated_query.lower():
                matching_results = results
                break
        
        if matching_results:
            print(f"📋 Found {len(matching_results)} results")
            successful_matches += 1
            
            # Analyze each result
            original_found_rank = None
            for rank, result_title in enumerate(matching_results, 1):
                # Check for title match with cleaning logic (removes R1, R2, etc. from results)
                is_title_match = 'NO'
                if original_title and result_title:
                    if titles_match(original_title, result_title):
                        is_title_match = 'YES'
                        if original_found_rank is None:
                            original_found_rank = rank
                
                # Overall match is based on title matching only
                result_row = {
                    'Query_ID': idx + 1 if rank == 1 else '',  # Only show Query_ID in first row
                    'Generated_Query': generated_query if rank == 1 else '',  # Only show query in first row
                    'Original_Title': original_title if rank == 1 else '',  # Only show title in first row
                    'Result_Rank': rank,
                    'Result_Title': result_title,
                    'Is_Title_Match': is_title_match,
                }
                all_results.append(result_row)
                
              
                if rank <= 3:
                    cleaned_result = clean_result_for_matching(result_title)
                    print(f"  {rank}. {result_title[:70]}{'...' if len(result_title) > 70 else ''}")
                    print(f"     → Cleaned: {cleaned_result[:60]}{'...' if len(cleaned_result) > 60 else ''}")
                    if is_title_match == 'YES':
                        print(f"     🎯 TITLE MATCH FOUND!")
                elif rank == 4:
                    print(f"     ... and {len(matching_results) - 3} more results")
            
            # Summary for this query
            if original_found_rank:
                print(f"✅ Original title found at rank {original_found_rank}")
            else:
                print(f"❌ Original title not found in top {len(matching_results)} results")
        
        else:
            print("❌ No matching results found for this query")
            failed_matches += 1
            # Add empty result row to maintain record
            result_row = {
                'Query_ID': idx + 1,
                'Generated_Query': generated_query,
                'Original_Title': original_title,
                'Result_Rank': 0,
                'Result_Title': 'NO_RESULTS_FOUND',
                'Is_Title_Match': 'NO',
            }
            all_results.append(result_row)
        
        print("-" * 60)
    
    # Save results to CSV
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        try:
            results_df.to_csv(output_csv, index=False)
            print(f"\n✅ Title matching results saved to: {output_csv}")
            print(f"📊 Total result rows: {len(results_df)}")
            
            # Print summary statistics
            original_matches = len(results_df[results_df['Is_Title_Match'] == 'YES'])
            
            print(f"\n📈 TITLE MATCHING ANALYSIS SUMMARY")
            print(f"   Total queries processed: {total_queries}")
            print(f"   Queries with results found: {successful_matches}")
            print(f"   Queries with no results: {failed_matches}")
            print(f"   Match rate: {(successful_matches/total_queries)*100:.1f}%")
            print(f"   Original titles found: {original_matches}")
            print(f"   Title match rate: {(original_matches/total_queries)*100:.1f}%")
            
            # Ranking analysis for original documents
            original_ranks = results_df[results_df['Is_Title_Match'] == 'YES']['Result_Rank']
            if len(original_ranks) > 0:
                print(f"\n⭐ Original title ranking analysis:")
                print(f"   Found at rank 1: {len(original_ranks[original_ranks == 1])}")
                print(f"   Found at rank 1-3: {len(original_ranks[original_ranks <= 3])}")
                print(f"   Found at rank 1-5: {len(original_ranks[original_ranks <= 5])}")
                print(f"   Found at rank 1-10: {len(original_ranks[original_ranks <= 10])}")
                print(f"   Average rank: {original_ranks.mean():.1f}")
            else:
                print(f"\n❌ No original titles were found in search results")
            
            # Show some successful matches
            successful_matches_df = results_df[results_df['Is_Title_Match'] == 'YES']
            if len(successful_matches_df) > 0:
                print(f"\n🎯 Sample successful matches:")
                unique_matches = successful_matches_df.drop_duplicates(subset=['Generated_Query'])
                for _, row in unique_matches.head(5).iterrows():
                    rank = row['Result_Rank']
                    print(f"   Rank {rank}: '{row['Generated_Query'][:50]}...'")
                    print(f"            → '{row['Result_Title'][:50]}...'")
                    
        except Exception as e:
            print(f"❌ Error saving results: {e}")
    else:
        print("❌ No results to save")

def main():
    """Main function to run the title matching analysis"""
    # File paths
    results_file = "result_4826275435117545089.xlsx"
    queries_file = "verizon_queries_200_20250930_132102.xlsx"
    output_csv = "title_matching_analysis_results.csv"
    
    print("🚀 VERIZON TITLE MATCHING ANALYSIS")
    print("=" * 50)
    print(f"Results file: {results_file}")
    print(f"Queries file: {queries_file}")
    print(f"Output CSV: {output_csv}")
    
    # Check if input files exist
    if not os.path.exists(results_file):
        print(f"❌ Results file not found: {results_file}")
        sys.exit(1)
    
    if not os.path.exists(queries_file):
        print(f"❌ Queries file not found: {queries_file}")
        sys.exit(1)
    
    print("\n" + "="*80)
    print("🔍 PERFORMING TITLE MATCHING ANALYSIS")
    print("="*80)
    
    # Perform analysis
    perform_title_matching_analysis(results_file, queries_file, output_csv)
    
    print(f"\n💾 Analysis complete! Results saved to: {output_csv}")
    print("🎯 Use this data to evaluate title matching performance!")

if __name__ == "__main__":
    main()