#!/usr/bin/env python3
"""
Clean Title Matching Results and Recalculate Statistics

This script:
1. Loads title_matching_analysis_results.csv
2. Removes duplicate Original_Title entries (keeps first occurrence)
3. Removes entries with "404" errors in titles
4. Recalculates ranking statistics
5. Saves cleaned results to a new CSV file
"""

import pandas as pd
import os
import sys

def is_404_error(title: str) -> bool:
    """
    Check if a title contains 404 error indicators.
    
    Args:
        title: Title string to check
        
    Returns:
        True if title contains 404 error, False otherwise
    """
    if not title or pd.isna(title):
        return False
    
    title_lower = str(title).lower()
    
    # Check for common 404 error patterns
    error_patterns = [
        '404 Error'
    ]
    
    return any(pattern in title_lower for pattern in error_patterns)

def clean_and_analyze_results(input_csv: str, output_csv: str = None):
    """
    Clean the title matching results and recalculate statistics.
    
    Args:
        input_csv: Path to input CSV file
        output_csv: Path to output CSV file (optional)
    """
    # Set default output path
    if output_csv is None:
        base_name = os.path.splitext(input_csv)[0]
        output_csv = f"{base_name}_cleaned.csv"
    
    print("🧹 CLEANING TITLE MATCHING RESULTS")
    print("=" * 80)
    print(f"📄 Input file: {input_csv}")
    print(f"💾 Output file: {output_csv}")
    print()
    
    # Load the CSV
    try:
        df = pd.read_csv(input_csv)
        print(f"✅ Loaded {len(df)} rows from input file")
        print(f"📋 Columns: {list(df.columns)}")
    except Exception as e:
        print(f"❌ Error loading CSV: {e}")
        return
    
    initial_count = len(df)
    
    # Count original unique queries before cleaning
    original_queries = df[df['Original_Title'].notna() & (df['Original_Title'] != '')]['Original_Title'].unique()
    print(f"📊 Original unique titles: {len(original_queries)}")
    
    # Step 1: Remove 404 errors
    print(f"\n🔍 Step 1: Removing 404 error entries...")
    
    # Check Original_Title for 404s
    df['is_404_original'] = df['Original_Title'].apply(is_404_error)
    # Check Result_Title for 404s
    df['is_404_result'] = df['Result_Title'].apply(is_404_error)
    
    count_404_original = df['is_404_original'].sum()
    count_404_result = df['is_404_result'].sum()
    
    print(f"   Found {count_404_original} rows with 404 in Original_Title")
    print(f"   Found {count_404_result} rows with 404 in Result_Title")
    
    # Remove rows where Original_Title contains 404
    df_no_404 = df[~df['is_404_original']].copy()
    df_no_404 = df_no_404.drop(columns=['is_404_original', 'is_404_result'])
    
    removed_404 = initial_count - len(df_no_404)
    print(f"   ✅ Removed {removed_404} rows with 404 errors")
    print(f"   📊 Remaining rows: {len(df_no_404)}")
    
    # Step 2: Remove duplicate Original_Title entries (keep all 10 results for unique queries)
    print(f"\n🔍 Step 2: Removing duplicate Original_Title entries...")
    
    # Strategy: Group by Query_ID (which represents unique queries)
    # Keep only the first occurrence of each Query_ID (which includes all 10 result rows)
    
    # Get unique Query_ID values
    unique_query_ids = df_no_404[df_no_404['Query_ID'].notna() & (df_no_404['Query_ID'] != '')]['Query_ID'].unique()
    print(f"   Found {len(unique_query_ids)} unique Query_ID values")
    
    # For duplicate detection, we need to identify queries with same Original_Title
    # Get the first row of each query (which has the Original_Title filled)
    query_first_rows = df_no_404[df_no_404['Original_Title'].notna() & (df_no_404['Original_Title'] != '')].copy()
    
    # Find duplicates based on Original_Title
    duplicate_titles = query_first_rows[query_first_rows.duplicated(subset=['Original_Title'], keep='first')]['Original_Title'].unique()
    print(f"   Found {len(duplicate_titles)} duplicate Original_Title values")
    
    # Get Query_IDs to remove (duplicates)
    query_ids_to_remove = query_first_rows[query_first_rows['Original_Title'].isin(duplicate_titles) & 
                                            query_first_rows.duplicated(subset=['Original_Title'], keep='first')]['Query_ID'].tolist()
    
    duplicates_removed_count = len(query_ids_to_remove)
    
    # Remove all rows (including the 10 result rows) for duplicate Query_IDs
    df_cleaned = df_no_404[~df_no_404['Query_ID'].isin(query_ids_to_remove)].copy()
    
    print(f"   ✅ Removed {duplicates_removed_count} duplicate queries (with all their result rows)")
    print(f"   📊 Remaining rows: {len(df_cleaned)}")
    
    # Sort by Query_ID and Result_Rank for readability
    df_cleaned = df_cleaned.sort_values(['Query_ID', 'Result_Rank'], ascending=[True, True])
    df_cleaned = df_cleaned.reset_index(drop=True)
    
    # Step 3: Recalculate statistics
    print(f"\n📊 RECALCULATING STATISTICS")
    print("=" * 80)
    
    # Get only rows with title matches
    title_matches = df_cleaned[df_cleaned['Is_Title_Match'] == 'YES']
    
    total_unique_titles = len(df_cleaned[
        df_cleaned['Original_Title'].notna() & 
        (df_cleaned['Original_Title'] != '')
    ])
    
    print(f"   Total unique titles after cleaning: {total_unique_titles}")
    print(f"   Titles with matches found: {len(title_matches)}")
    
    if len(title_matches) > 0:
        match_rate = (len(title_matches) / total_unique_titles) * 100 if total_unique_titles > 0 else 0
        print(f"   Match rate: {match_rate:.1f}%")
        
        # Get Result_Rank for matched titles
        original_ranks = title_matches['Result_Rank']
        
        print(f"\n⭐ Original title ranking analysis:")
        print(f"   Found at rank 1: {len(original_ranks[original_ranks == 1])}")
        print(f"   Found at rank 1-3: {len(original_ranks[original_ranks <= 3])}")
        print(f"   Found at rank 1-5: {len(original_ranks[original_ranks <= 5])}")
        print(f"   Found at rank 1-10: {len(original_ranks[original_ranks <= 10])}")
        print(f"   Average rank: {original_ranks.mean():.2f}")
        
        # Calculate percentages
        print(f"\n📈 Match distribution (percentages):")
        print(f"   Rank 1: {(len(original_ranks[original_ranks == 1]) / len(title_matches)) * 100:.1f}%")
        print(f"   Rank 1-3: {(len(original_ranks[original_ranks <= 3]) / len(title_matches)) * 100:.1f}%")
        print(f"   Rank 1-5: {(len(original_ranks[original_ranks <= 5]) / len(title_matches)) * 100:.1f}%")
        print(f"   Rank 1-10: {(len(original_ranks[original_ranks <= 10]) / len(title_matches)) * 100:.1f}%")
    else:
        print(f"   ❌ No title matches found in cleaned data")
    
    # Step 4: Save cleaned results
    print(f"\n💾 Saving cleaned results...")
    
    try:
        df_cleaned.to_csv(output_csv, index=False)
        print(f"✅ Cleaned results saved to: {output_csv}")
        print(f"📊 Total rows in cleaned file: {len(df_cleaned)}")
        
        # Summary
        print(f"\n📋 CLEANING SUMMARY")
        print(f"   Original rows: {initial_count}")
        print(f"   404 errors removed: {removed_404}")
        print(f"   Duplicate queries removed: {duplicates_removed_count}")
        print(f"   Final rows: {len(df_cleaned)}")
        print(f"   Rows removed: {initial_count - len(df_cleaned)}")
        print(f"   Reduction: {((initial_count - len(df_cleaned)) / initial_count) * 100:.1f}%")
        
    except Exception as e:
        print(f"❌ Error saving results: {e}")

def main():
    """Main function"""
    input_file = "title_matching_analysis_results.csv"
    output_file = "title_matching_analysis_results_cleaned.csv"
    
    # Check if input file exists
    if not os.path.exists(input_file):
        print(f"❌ Input file not found: {input_file}")
        print(f"   Please ensure the file exists in the current directory")
        sys.exit(1)
    
    print("🚀 TITLE MATCHING RESULTS CLEANER")
    print("=" * 80)
    
    # Run the cleaning and analysis
    clean_and_analyze_results(input_file, output_file)
    
    print(f"\n✨ Process complete!")
    print(f"📄 Cleaned results: {output_file}")

if __name__ == "__main__":
    main()
