#!/usr/bin/env python3
"""
Script to perform searches using queries from optimized_queries.csv and save results
"""

import pandas as pd
import sys
import os
import time
from typing import List, Dict

# Add parent directory to path to import agents module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.search_agent import SearchAgent

def perform_search_evaluation(input_csv: str, output_csv: str = None, delay_seconds: float = 1.0):
    """
    Read queries from optimized_queries.csv, perform searches, and save results
    
    Args:
        input_csv: Path to CSV file containing Generated_Query column
        output_csv: Path to output CSV file (optional)
        delay_seconds: Delay between API calls to avoid rate limiting
    """
    # Read input CSV
    try:
        df = pd.read_csv(input_csv)
        print(f"📄 Loaded {len(df)} queries from {input_csv}")
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
        output_csv = f"{base_name}_search_results.csv"
    
    # Initialize search agent
    try:
        search_agent = SearchAgent()
        print("✅ Search agent initialized")
        
        # Test connection
        if not search_agent.test_connection():
            print("❌ Failed to connect to SearchUnify API")
            return
        print("✅ SearchUnify API connection successful")
        
    except Exception as e:
        print(f"❌ Error initializing search agent: {e}")
        return
    
    # Prepare results list
    all_results = []
    
    print(f"\n🔍 Starting search evaluation for {len(df)} queries...")
    print("=" * 80)
    
    # Process each query
    for idx, row in df.iterrows():
        query = row.get('Generated_Query', '').strip()
        original_title = row.get('Title', '')
        original_url = row.get('Document_URL', '')
        
        if not query:
            print(f"⚠️  Skipping row {idx + 1}: Empty query")
            continue
        
        print(f"\n📝 Query {idx + 1}/{len(df)}: '{query}'")
        print(f"Original Document: {original_title[:60]}{'...' if len(original_title) > 60 else ''}")
        
        try:
            # Perform search
            documents = search_agent.search_and_extract(query, top_n=10)
            
            if documents:
                print(f"📋 Top {len(documents)} results:")
                
                # Add each result as a separate row
                for doc in documents:
                    # Only show query info on the first result row
                    if doc['rank'] == 1:
                        result_row = {
                            'Query_ID': idx + 1,
                            'Generated_Query': query,
                            'Original_Title': original_title,
                            'Original_URL': original_url,
                            'Result_Rank': doc['rank'],
                            'Result_Title': doc['title'],
                            'Result_URL': doc['url'],
                            'Result_Description': doc['description'][:200] + '...' if len(doc.get('description', '')) > 200 else doc.get('description', ''),
                            'Search_Score': doc['score']
                        }
                    else:
                        # For subsequent results, leave query fields empty
                        result_row = {
                            'Query_ID': '',
                            'Generated_Query': '',
                            'Original_Title': '',
                            'Original_URL': '',
                            'Result_Rank': doc['rank'],
                            'Result_Title': doc['title'],
                            'Result_URL': doc['url'],
                            'Result_Description': doc['description'][:200] + '...' if len(doc.get('description', '')) > 200 else doc.get('description', ''),
                            'Search_Score': doc['score']
                        }
                    all_results.append(result_row)
                    
                    print(f"  {doc['rank']}. {doc['title'][:50]}{'...' if len(doc['title']) > 50 else ''}")
                    print(f"     URL: {doc['url']}")
                    print(f"     Score: {doc['score']:.3f}")
                    
            else:
                print("❌ No results found")
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
                    'Search_Score': 0.0
                }
                all_results.append(result_row)
                
        except Exception as e:
            print(f"❌ Error searching for query '{query}': {e}")
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
                'Search_Score': 0.0
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
            print(f"\n✅ Search results saved to: {output_csv}")
            print(f"📊 Total rows: {len(results_df)}")
            
            # Print summary statistics
            successful_queries = len(results_df[results_df['Result_Rank'] > 0]['Query_ID'].unique())
            total_queries = len(df)
            
            print(f"📈 Successful searches: {successful_queries}/{total_queries}")
            print(f"📈 Total search results: {len(results_df[results_df['Result_Rank'] > 0])}")
            
            # Top queries by average score
            if len(results_df[results_df['Result_Rank'] > 0]) > 0:
                avg_scores = results_df[results_df['Result_Rank'] > 0].groupby('Generated_Query')['Search_Score'].mean().sort_values(ascending=False)
                print(f"\n🏆 Top 5 queries by average search score:")
                for i, (query, score) in enumerate(avg_scores.head().items()):
                    print(f"  {i+1}. '{query}' (avg score: {score:.3f})")
                    
        except Exception as e:
            print(f"❌ Error saving results: {e}")
    else:
        print("❌ No results to save")

def analyze_search_results(results_csv: str):
    """
    Analyze the search results and provide insights
    
    Args:
        results_csv: Path to search results CSV
    """
    try:
        df = pd.read_csv(results_csv)
        print(f"\n📊 Search Results Analysis")
        print("=" * 50)
        
        # Basic statistics
        total_rows = len(df)
        successful_results = len(df[df['Result_Rank'] > 0])
        unique_queries = df['Query_ID'].nunique()
        
        print(f"Total result rows: {total_rows}")
        print(f"Successful results: {successful_results}")
        print(f"Unique queries: {unique_queries}")
        
        # Results per query distribution
        results_per_query = df[df['Result_Rank'] > 0].groupby('Query_ID').size()
        print(f"\nResults per query:")
        print(f"  Average: {results_per_query.mean():.1f}")
        print(f"  Min: {results_per_query.min()}")
        print(f"  Max: {results_per_query.max()}")
        
        # Score distribution
        scores = df[df['Result_Rank'] > 0]['Search_Score']
        if len(scores) > 0:
            print(f"\nSearch score distribution:")
            print(f"  Average: {scores.mean():.3f}")
            print(f"  Min: {scores.min():.3f}")
            print(f"  Max: {scores.max():.3f}")
        
    except Exception as e:
        print(f"❌ Error analyzing results: {e}")

if __name__ == "__main__":
    # Default paths
    input_csv = '/home/abhijeetsingh1/RelevanceEvaluator/search_relevance_project/optimized_queries.csv'
    output_csv = '/home/abhijeetsingh1/RelevanceEvaluator/search_relevance_project/search_results.csv'
    
    print("🚀 Starting Search Evaluation Process")
    print(f"Input CSV: {input_csv}")
    print(f"Output CSV: {output_csv}")
    
    # Check if input file exists
    if not os.path.exists(input_csv):
        print(f"❌ Input file not found: {input_csv}")
        print("Please run generate_search_queries.py first to create optimized_queries.csv")
        sys.exit(1)
    
    print("\n" + "="*80)
    print("🔍 PERFORMING SEARCH EVALUATION")
    print("="*80)
    
    # Perform search evaluation
    perform_search_evaluation(input_csv, output_csv, delay_seconds=1.0)
    
    # Analyze results if successful
    if os.path.exists(output_csv):
        analyze_search_results(output_csv)
