#!/usr/bin/env python3
"""
CSOD (Cornerstone OnDemand) Search Evaluation Script

This script:
1. Reads search queries from 'CSOD Console Relevancy.xlsx'
2. Performs searches using the Cornerstone OnDemand SearchUnify endpoint
3. Extracts top 10 search results (title and URL only) for each query
4. Saves results to a new Excel file

Uses the curl configuration from curl_input_cornerstone.json
"""

import pandas as pd
import requests
import json
import time
import uuid
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional

class CSODSearchAgent:
    """
    Specialized search agent for CSOD (Cornerstone OnDemand) SearchUnify endpoint
    """
    
    def __init__(self, config_file: str = "curl_input_cornerstone.json"):
        """Initialize CSOD Search Agent with Cornerstone configuration"""
        self.curl_config = self._load_curl_config(config_file)
        
        # Extract configuration
        self.base_url = self.curl_config.get("url", "https://searchunify-s.cornerstoneondemand.com/search/SUSearchResults")
        
        # Headers from curl config
        self.headers = self.curl_config.get("headers", {})
        
        # Body template from curl config
        self.body_template = self.curl_config.get("body", {})
        
        print(f"✅ CSOD Search Agent initialized")
        print(f"🔗 Endpoint: {self.base_url}")
        print(f"🆔 UID: {self.body_template.get('uid', 'N/A')}")

    def _load_curl_config(self, config_file: str) -> Dict:
        """Load curl configuration from specified config file"""
        try:
            # Look for config file in verizon-title-matching directory first
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "verizon-title-matching", config_file)
            if not os.path.exists(config_path):
                # Fallback to main project directory
                config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), config_file)
            
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading {config_file}: {e}")
            return {}

    def _generate_sid(self) -> str:
        """Generate a session ID"""
        return str(int(time.time() * 1000000))

    def search(self, search_string: str, results_per_page: int = 10) -> Dict:
        """
        Perform search query against CSOD SearchUnify API
        
        Args:
            search_string: The search query string
            results_per_page: Number of results to return (default 10)
            
        Returns:
            Dictionary containing search results
        """
        try:
            # Prepare payload based on curl_input_cornerstone.json structure
            payload = self.body_template.copy()
            
            # Update with search-specific parameters
            payload.update({
                "searchString": search_string,
                "from": 0,
                "pageNum": 1,
                "resultsPerPage": results_per_page,
                "sid": self._generate_sid()
            })
            
            print(f"🔍 Searching CSOD for: '{search_string}'")
            
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"⚠️  CSOD Search API returned status {response.status_code}")
                print(f"Response: {response.text[:200]}...")
                return {}
                
        except Exception as e:
            print(f"❌ Error performing CSOD search: {e}")
            return {}

    def extract_top_documents(self, search_response: Dict, top_n: int = 10) -> List[Dict]:
        """
        Extract top N documents from CSOD search response
        Based on the provided response structure
        
        Args:
            search_response: Response from CSOD search API
            top_n: Number of top documents to extract
            
        Returns:
            List of dictionaries containing document info (title and URL only)
        """
        documents = []
        
        try:
            # Navigate through the CSOD response structure
            if 'result' in search_response and 'hits' in search_response['result']:
                hits = search_response['result']['hits']
                
                for i, hit in enumerate(hits[:top_n]):
                    # Extract title - try multiple sources
                    title = ""
                    if 'highlight' in hit and 'TitleToDisplayString' in hit['highlight']:
                        title = hit['highlight']['TitleToDisplayString'][0] if hit['highlight']['TitleToDisplayString'] else ""
                    elif 'highlight' in hit and 'TitleToDisplay' in hit['highlight']:
                        # Remove HTML highlighting tags
                        title_with_html = hit['highlight']['TitleToDisplay'][0] if hit['highlight']['TitleToDisplay'] else ""
                        title = title_with_html.replace("<span class='highlight'>", "").replace("</span>", "")
                    elif 'highlight' in hit and '1_5_northstar_knowledge___knowledge__kav___Title.en' in hit['highlight']:
                        title_with_html = hit['highlight']['1_5_northstar_knowledge___knowledge__kav___Title.en'][0]
                        # Remove SearchUnify highlighting tags
                        title = title_with_html.replace("___su-highlight-start___", "").replace("___su-highlight-end___", "")
                    
                    # Extract URL
                    url = hit.get('href', '')
                    
                    # Extract score for ranking
                    score = hit.get('_score', 0.0)
                    
                    if title or url:  # Only add if we have at least title or URL
                        document = {
                            'rank': i + 1,
                            'title': title.strip(),
                            'url': url.strip(),
                            'score': score
                        }
                        documents.append(document)
                
                print(f"✅ Extracted {len(documents)} documents from CSOD response")
                
            else:
                print("⚠️  No 'result.hits' found in CSOD response")
                print(f"Response keys: {list(search_response.keys()) if search_response else 'Empty response'}")
                    
        except Exception as e:
            print(f"❌ Error extracting documents from CSOD response: {e}")
            
        return documents

    def search_and_extract(self, search_string: str, top_n: int = 10) -> List[Dict]:
        """
        Perform search and extract top documents in one call
        
        Args:
            search_string: The search query string
            top_n: Number of top documents to return
            
        Returns:
            List of top documents with title and URL
        """
        # Perform search
        search_response = self.search(search_string, results_per_page=top_n)
        
        if not search_response:
            print("❌ No response from CSOD search API")
            return []
        
        # Extract documents
        documents = self.extract_top_documents(search_response, top_n)
        
        print(f"📋 Found {len(documents)} documents for query: '{search_string}'")
        return documents

    def test_connection(self) -> bool:
        """
        Test if the CSOD search API is accessible
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            test_response = self.search("test", results_per_page=1)
            return bool(test_response)
        except Exception as e:
            print(f"❌ CSOD connection test failed: {e}")
            return False


def load_csod_queries(excel_file: str) -> pd.DataFrame:
    """
    Load search queries from CSOD Console Relevancy Excel file
    
    Args:
        excel_file: Path to the CSOD Excel file
        
    Returns:
        DataFrame with queries
    """
    try:
        # Try to read the Excel file - check different sheet names
        xl_file = pd.ExcelFile(excel_file)
        print(f"📄 Found sheets in CSOD Excel file: {xl_file.sheet_names}")
        
        # Read the first sheet by default or look for specific sheet names
        sheet_name = xl_file.sheet_names[0]  # Default to first sheet
        
        # Look for common sheet names
        for name in xl_file.sheet_names:
            if any(keyword in name.lower() for keyword in ['query', 'search', 'relevancy', 'console']):
                sheet_name = name
                break
        
        df = pd.read_excel(excel_file, sheet_name=sheet_name)
        print(f"📊 Loaded {len(df)} rows from sheet '{sheet_name}'")
        print(f"📋 Columns found: {list(df.columns)}")
        
        return df
        
    except Exception as e:
        print(f"❌ Error loading CSOD Excel file: {e}")
        return pd.DataFrame()


def perform_csod_search_evaluation(excel_file: str, output_file: str = None, delay_seconds: float = 1.0):
    """
    Perform search evaluation using CSOD queries and save top 10 results
    
    Args:
        excel_file: Path to CSOD Console Relevancy Excel file
        output_file: Path to output Excel file (optional)
        delay_seconds: Delay between API calls to avoid rate limiting
    """
    # Load queries from Excel file
    df = load_csod_queries(excel_file)
    
    if df.empty:
        print("❌ No data loaded from Excel file")
        return
    
    # Try to identify the query column
    query_column = None
    for col in df.columns:
        if any(keyword in col.lower() for keyword in ['query', 'search', 'question', 'term', 'text']):
            query_column = col
            break
    
    if not query_column:
        print("❌ Could not identify query column. Available columns:")
        for i, col in enumerate(df.columns):
            print(f"  {i+1}. {col}")
        
        # Ask user to specify or use first column as default
        query_column = df.columns[0]
        print(f"🔄 Using first column as query column: '{query_column}'")
    
    print(f"📝 Using column '{query_column}' for search queries")
    
    # Set default output path
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"csod_search_results_{timestamp}.xlsx"
        output_file = os.path.join(os.path.dirname(excel_file), output_file)
    
    # Initialize CSOD search agent
    try:
        search_agent = CSODSearchAgent()
        
        # Test connection
        print("🔍 Testing CSOD API connection...")
        if search_agent.test_connection():
            print("✅ CSOD SearchUnify API connection successful")
        else:
            print("⚠️  CSOD API test failed, but continuing...")
        
    except Exception as e:
        print(f"❌ Error initializing CSOD search agent: {e}")
        return
    
    # Prepare results list
    all_results = []
    successful_searches = 0
    failed_searches = 0
    
    print(f"\n🔍 Starting CSOD search evaluation for {len(df)} queries...")
    print("=" * 80)
    
    # Process each query
    for idx, row in df.iterrows():
        query = str(row.get(query_column, '')).strip()
        
        if not query or query.lower() in ['nan', 'none', '']:
            print(f"⚠️  Skipping row {idx + 1}: Empty or invalid query")
            failed_searches += 1
            continue
        
        print(f"\n📝 Query {idx + 1}/{len(df)}: '{query}'")
        
        try:
            # Perform search and get top 10 results
            documents = search_agent.search_and_extract(query, top_n=10)
            
            if documents:
                successful_searches += 1
                
                # Add query info to first result row only
                for i, doc in enumerate(documents):
                    result_row = {
                        'Query_ID': idx + 1 if i == 0 else '',  # Only show Query_ID in first row
                        'Search_Query': query if i == 0 else '',  # Only show query in first row
                        'Result_Rank': doc['rank'],
                        'Result_Title': doc['title'],
                        'Result_URL': doc['url'],
                        'Search_Score': doc['score']
                    }
                    all_results.append(result_row)
                
                # Show preview of top 3 results
                print(f"📋 Top 3 results:")
                for i, doc in enumerate(documents[:3]):
                    print(f"  {doc['rank']}. {doc['title'][:80]}{'...' if len(doc['title']) > 80 else ''}")
                    print(f"     URL: {doc['url']}")
                    print(f"     Score: {doc['score']:.3f}")
                
                if len(documents) > 3:
                    print(f"     ... and {len(documents) - 3} more results")
                    
            else:
                print("❌ No results found")
                failed_searches += 1
                # Add empty result row to maintain record
                result_row = {
                    'Query_ID': idx + 1,
                    'Search_Query': query,
                    'Result_Rank': 0,
                    'Result_Title': 'NO_RESULTS_FOUND',
                    'Result_URL': '',
                    'Search_Score': 0.0
                }
                all_results.append(result_row)
                
        except Exception as e:
            print(f"❌ Error searching for query '{query}': {e}")
            failed_searches += 1
            # Add error result row
            result_row = {
                'Query_ID': idx + 1,
                'Search_Query': query,
                'Result_Rank': -1,
                'Result_Title': f'ERROR: {str(e)}',
                'Result_URL': '',
                'Search_Score': 0.0
            }
            all_results.append(result_row)
        
        # Add delay to avoid rate limiting
        if delay_seconds > 0 and idx < len(df) - 1:
            print(f"⏳ Waiting {delay_seconds}s before next query...")
            time.sleep(delay_seconds)
        
        print("-" * 60)
    
    # Save results to Excel
    if all_results:
        results_df = pd.DataFrame(all_results)
        
        try:
            # Save to Excel with formatting
            with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
                results_df.to_excel(writer, sheet_name='CSOD_Search_Results', index=False)
                
                # Get the workbook and worksheet
                worksheet = writer.sheets['CSOD_Search_Results']
                
                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    
                    # Set column width (max 100 characters)
                    adjusted_width = min(max_length + 2, 100)
                    worksheet.column_dimensions[column_letter].width = adjusted_width
            
            print(f"\n✅ CSOD search results saved to: {output_file}")
            print(f"📊 Total result rows: {len(results_df)}")
            
            # Print summary statistics
            total_queries = len(df)
            
            print(f"\n📈 CSOD SEARCH EVALUATION SUMMARY")
            print(f"   Total queries processed: {total_queries}")
            print(f"   Successful searches: {successful_searches}")
            print(f"   Failed searches: {failed_searches}")
            print(f"   Success rate: {(successful_searches/total_queries)*100:.1f}%")
            
            # Show top queries by average score
            successful_results = results_df[results_df['Result_Rank'] > 0]
            if len(successful_results) > 0:
                avg_scores = successful_results.groupby('Search_Query')['Search_Score'].mean().sort_values(ascending=False)
                print(f"\n🏆 Top 5 queries by average search score:")
                for i, (query, score) in enumerate(avg_scores.head().items()):
                    print(f"  {i+1}. '{query[:50]}{'...' if len(query) > 50 else ''}' (avg score: {score:.3f})")
                    
        except Exception as e:
            print(f"❌ Error saving results to Excel: {e}")
    else:
        print("❌ No results to save")


if __name__ == "__main__":
    # Default paths
    excel_file = '/home/abhijeetsingh1/RelevanceEvaluator-Vespa_verizon/search_relevance_project/verizon-title-matching/CSOD Console Relevancy .xlsx'
    
    print("🚀 CSOD (CORNERSTONE ONDEMAND) SEARCH EVALUATION")
    print("=" * 60)
    print(f"Input Excel: {excel_file}")
    
    # Check if input file exists
    if not os.path.exists(excel_file):
        print(f"❌ Input file not found: {excel_file}")
        print("Please ensure 'CSOD Console Relevancy .xlsx' exists in the verizon-title-matching directory")
        sys.exit(1)
    
    print("\n" + "="*80)
    print("🔍 PERFORMING CSOD SEARCH EVALUATION")
    print("="*80)
    
    # Perform search evaluation
    perform_csod_search_evaluation(excel_file, delay_seconds=0.5)
    
    print(f"\n💾 Results saved to Excel file in the same directory")
    print("🎯 Use this data to evaluate CSOD search relevance!")