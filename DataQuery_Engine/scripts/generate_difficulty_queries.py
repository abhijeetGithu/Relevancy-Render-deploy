#!/usr/bin/env python3
"""
Script to generate ONLY Hard queries from an existing CSV (output.csv)
and save them to a single Excel file with columns: Title, Document_URL, Generated_Query.
"""

import pandas as pd
import sys
import os
import random
import time
import json
from typing import List, Dict
from dotenv import load_dotenv

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.ground_truth_agent import GroundTruthAgent
from utils.request_handler import load_config

# Load environment variables
load_dotenv()

class DifficultyBasedQueryGenerator:
    """
    Generator for creating easy, medium, and hard queries from random document samples
    """
    
    def __init__(self, sample_size: int = 500, max_pages: int = 1000):
        self.sample_size = sample_size
        self.max_pages = max_pages
        self.difficulties = ["easy", "medium", "hard"]
        
        # Load configuration
        self.config = load_config()
        # Judge settings (used for LLM-as-judge flow)
        try:
            self.judge_threshold = int(str(self.config.get("JUDGE_THRESHOLD", 3)))
        except Exception:
            self.judge_threshold = 3
        try:
            self.judge_max_attempts = int(str(self.config.get("JUDGE_MAX_ATTEMPTS", 3)))
        except Exception:
            self.judge_max_attempts = 3
        # Toggle to use judge flow (enabled by default)
        self.use_judge = True
        
        # Initialize agent
        self.agent = None
        
        # Combined results storage for single Excel file
        self.combined_results = []

    def _initialize_agent(self):
        """Initialize the Ground Truth Agent"""
        try:
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key or openai_api_key == "your_openai_api_key_here":
                raise ValueError("❌ Please set your OPENAI_API_KEY in the .env file")
            
            self.agent = GroundTruthAgent(openai_api_key=openai_api_key, model="gpt-4o-mini")
            print("✅ Successfully initialized OpenAI agent with gpt-4o-mini")
            return True
            
        except Exception as e:
            print(f"❌ Error initializing OpenAI agent: {e}")
            return False

    def _is_valid_row(self, row: Dict) -> bool:
        """Validate that row has Title, Description, and URL with useful content."""
        title = str(row.get('Title', '') or '').strip()
        description = str(row.get('Description', '') or '').strip()
        # Accept either 'Document URL' or 'Document_URL' as input
        url = str(row.get('Document_URL', row.get('Document URL', '')) or '').strip()

        invalid_values = {'', 'nan', 'null', 'none', 'n/a', 'undefined'}
        title_valid = title.lower() not in invalid_values and len(title) > 2
        description_valid = description.lower() not in invalid_values and len(description) > 10
        url_valid = url.lower() not in invalid_values and len(url) > 5
        return title_valid and description_valid and url_valid

    def load_documents_from_csv(self, input_csv: str) -> List[Dict]:
        """
        Load documents from an input CSV file and return list of dicts.
        Expected input columns include Title, Description, and Document URL (or Document_URL).
        """
        print(f"📥 Loading documents from CSV: {input_csv}")
        if not os.path.exists(input_csv):
            print(f"❌ Input CSV not found: {input_csv}")
            return []

        try:
            df = pd.read_csv(input_csv)
        except Exception as e:
            print(f"❌ Failed to read CSV: {e}")
            return []

        # Normalize column names (strip whitespace)
        df.columns = [c.strip() for c in df.columns]

        valid_docs = []
        skipped = 0
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            if not self._is_valid_row(row_dict):
                skipped += 1
                continue

            title = str(row_dict.get('Title', '')).strip()
            description = str(row_dict.get('Description', '')).strip()
            url = str(row_dict.get('Document_URL', row_dict.get('Document URL', ''))).strip()

            valid_docs.append({
                'Title': title,
                'Description': description,
                'Document_URL': url,
            })

        print(f"📊 Loaded {len(valid_docs)} valid documents from CSV (skipped {skipped})")

        # Sample down if more than requested sample_size
        if self.sample_size and len(valid_docs) > self.sample_size:
            valid_docs = random.sample(valid_docs, self.sample_size)
            print(f"🎯 Sampled down to {len(valid_docs)} documents")

        return valid_docs

    def generate_hard_queries_for_documents(self, documents: List[Dict]) -> List[Dict]:
        """
        Generate Hard queries for each document and return results.
        
        Args:
            documents: List of documents
            
        Returns:
            List of results with only the hard query per document
        """
        print(f"\n🔍 Generating HARD queries for {len(documents)} documents...")
        print("=" * 80)

        hard_results = []

        for idx, doc in enumerate(documents, 1):
            title = doc.get('Title', '')
            description = doc.get('Description', '')
            url = doc.get('Document_URL', doc.get('Document URL', ''))

            if not title or not description:
                print(f"⚠️  Skipping document {idx}: Missing title or description")
                continue

            print(f"📝 Document {idx}/{len(documents)}: {title[:50]}{'...' if len(title) > 50 else ''}")

            result = {
                'Document_ID': idx,
                'Title': title,
                'Document_URL': url,
                'Hard_Generated_Query': None,
                'Hard_Generation_Time_Seconds': None,
                'Hard_Generation_Timestamp': None,
                'Generation_Started': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            }

            try:
                start_time = time.time()
                timestamp = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')

                query = None
                if self.use_judge and hasattr(self.agent, 'generate_with_threshold'):
                    # Use LLM-as-judge flow
                    judge_out = self.agent.generate_with_threshold(
                        title=title,
                        description=description,
                        difficulty="hard",
                        threshold=self.judge_threshold,
                        max_attempts=self.judge_max_attempts,
                    )
                    query = (judge_out or {}).get("query")
                
                # Fallback to plain generation if judge didn't return a query
                if not query:
                    query = self.agent.generate_search_query(title, description, difficulty="hard")

                generation_time = round(time.time() - start_time, 2)

                if query:
                    result['Hard_Generated_Query'] = query
                    result['Hard_Generation_Time_Seconds'] = generation_time
                    result['Hard_Generation_Timestamp'] = timestamp
                    print(f"   ✅ HARD: '{query}' ({len(query.split())} words, {generation_time}s)")
                else:
                    print("   ❌ Failed to generate HARD query")

            except Exception as e:
                print(f"   ❌ Error generating HARD query: {e}")
                continue

            # Small delay between documents
            time.sleep(0.5)

            hard_results.append(result)

            if idx % 10 == 0:
                print(f"   ⏳ Progress: {idx}/{len(documents)} - Brief pause...")
                time.sleep(1)

        print(f"✅ Generated HARD queries for {len(hard_results)} documents")
        return hard_results

    def save_hard_to_excel(self, results: List[Dict], output_dir: str = None):
        """
        Save HARD-only results to a single Excel file with columns:
        Title, Document_URL, Generated_Query
        
        Args:
            results: List of result dictionaries with hard queries
            output_dir: Output directory (optional)
        """
        if not results:
            print("⚠️  No results to save")
            return None

        # Set output directory
        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(__file__))

        # Create filename
        timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        filename = f"queries_hard_only_{timestamp}.xlsx"
        filepath = os.path.join(output_dir, filename)

        try:
            # Create DataFrame with requested columns only
            df_full = pd.DataFrame(results)
            df = pd.DataFrame({
                'Title': df_full.get('Title', []),
                'Document_URL': df_full.get('Document_URL', []),
                'Generated_Query': df_full.get('Hard_Generated_Query', []),
            })

            # Save to Excel with formatting
            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Hard_Queries', index=False)

                # Get the worksheet for formatting
                worksheet = writer.sheets['Hard_Queries']

                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter

                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except Exception:
                            pass

                    adjusted_width = min(max_length + 2, 60)
                    worksheet.column_dimensions[column_letter].width = adjusted_width

            print(f"✅ HARD queries saved to: {filepath}")
            print(f"📊 File contains {len(df)} rows (Title, Document_URL, Generated_Query)")

            return filepath

        except Exception as e:
            print(f"❌ Error saving HARD queries to Excel: {e}")
            return None

    def analyze_combined_results(self, results: List[Dict]):
        """Analyze and display summary statistics for combined results"""
        print(f"\n📊 COMBINED QUERY GENERATION ANALYSIS")
        print("=" * 60)
        
        total_documents = len(results)
        print(f"Total documents processed: {total_documents}")
        
        if not results:
            return
        
        # Analyze each difficulty level
        for difficulty in ['Easy', 'Medium', 'Hard']:
            query_field = f'{difficulty}_Generated_Query'
            time_field = f'{difficulty}_Generation_Time_Seconds'
            
            # Count successful generations
            successful_queries = [r for r in results if r.get(query_field)]
            count = len(successful_queries)
            
            if count > 0:
                queries = [r[query_field] for r in successful_queries]
                avg_length = sum(len(q.split()) for q in queries) / len(queries)
                
                # Calculate time statistics
                times = [r[time_field] for r in successful_queries if r.get(time_field)]
                if times:
                    avg_time = sum(times) / len(times)
                    min_time = min(times)
                    max_time = max(times)
                else:
                    avg_time = min_time = max_time = 0
                
                print(f"\n{difficulty.upper()} Queries:")
                print(f"  Count: {count}/{total_documents} ({count/total_documents*100:.1f}%)")
                print(f"  Average length: {avg_length:.1f} words")
                print(f"  Average generation time: {avg_time:.2f}s")
                print(f"  Time range: {min_time:.2f}s - {max_time:.2f}s")
                
                # Show sample queries
                sample_size = min(3, len(queries))
                if sample_size > 0:
                    print(f"  Sample queries:")
                    for i, query in enumerate(random.sample(queries, sample_size), 1):
                        print(f"    {i}. {query}")
            else:
                print(f"\n{difficulty.upper()} Queries:")
                print(f"  Count: 0/{total_documents} (0.0%)")
        
        # Overall statistics
        total_queries = sum(1 for r in results for difficulty in ['Easy', 'Medium', 'Hard'] 
                          if r.get(f'{difficulty}_Generated_Query'))
        total_time = sum(r.get(f'{difficulty}_Generation_Time_Seconds', 0) 
                        for r in results 
                        for difficulty in ['Easy', 'Medium', 'Hard'] 
                        if r.get(f'{difficulty}_Generation_Time_Seconds'))
        
        print(f"\n📈 OVERALL STATISTICS:")
        print(f"  Total queries generated: {total_queries}")
        print(f"  Total generation time: {total_time:.2f}s ({total_time/60:.1f} minutes)")
        if total_queries > 0:
            print(f"  Average time per query: {total_time/total_queries:.2f}s")

    def run_generation_pipeline(self, output_dir: str = None, input_csv: str | None = None):
        """
        Run the complete query generation pipeline for HARD-only results from CSV
        
        Args:
            output_dir: Output directory for Excel files (optional)
            input_csv: Path to input CSV (default: ../output.csv)
        """
        print("🚀 Starting HARD-Only Query Generation Pipeline (from CSV)")
        print("=" * 80)
        
        # Initialize agent
        if not self._initialize_agent():
            return
        
        # Determine input CSV default
        if not input_csv:
            input_csv = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'output.csv'))
        print(f"📄 Input CSV: {input_csv}")

        # Load documents from CSV
        documents = self.load_documents_from_csv(input_csv)
        
        if not documents:
            print("❌ No documents collected. Exiting.")
            return
        
        # Generate HARD-only queries
        print(f"\n🔄 Generating HARD queries for all {len(documents)} documents...")
        start_time = time.time()
        
        hard_results = self.generate_hard_queries_for_documents(documents)
        self.combined_results = hard_results  # keep attribute for backward compatibility
        
        total_time = time.time() - start_time
        print(f"⏱️  Total pipeline time: {total_time:.2f}s ({total_time/60:.1f} minutes)")
        
        # Save to single Excel file (hard-only)
        filepath = self.save_hard_to_excel(hard_results, output_dir)
        
        # Optional: analysis
        # self.analyze_combined_results(hard_results)  # can be enabled if needed
        
        print(f"\n🎉 Completed hard-query generation from CSV!")
        print(f"Generated HARD queries for {len(documents)} documents")
        if filepath:
            print(f"📄 Results saved to: {filepath}")
        
        return filepath

def main():
    """Main execution function"""
    print("🎯 Hard-Only Query Generator (from output.csv)")
    print("Generates HARD queries from existing CSV and saves Title, Document_URL, Generated_Query to Excel")
    print("=" * 80)

    # Paths
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    input_csv = os.path.join(repo_root, 'output_for_query_testing.csv')
    output_dir = os.path.join(repo_root, 'query_outputs')

    # Ensure output dir
    os.makedirs(output_dir, exist_ok=True)

    # Configurable sample size (optional): use ENV or keep default
    sample_env = os.getenv('HARD_QUERY_SAMPLE_SIZE')
    sample_size = int(sample_env) if sample_env and sample_env.isdigit() else 0  # 0 means use all

    generator = DifficultyBasedQueryGenerator(sample_size=sample_size or 0)
    filepath = generator.run_generation_pipeline(output_dir, input_csv)

    if filepath:
        print(f"\n✅ Success! File saved to: {filepath}")
    else:
        print(f"\n❌ Pipeline failed to complete successfully")

if __name__ == "__main__":
    main()
