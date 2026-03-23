import pandas as pd
import sys
import os
from dotenv import load_dotenv
import re
import json
from datetime import datetime

# Add parent directory to path to import agents module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.ground_truth_agent import GroundTruthAgent

# Load environment variables
load_dotenv()

# Global column name configuration (can be overridden via CLI args)
TITLE_COLUMN = 'Title'
DESCRIPTION_COLUMN = 'Description'
URL_COLUMN = 'Document URL'


def read_input_file(file_path: str) -> pd.DataFrame:
    """Read input file - supports both CSV and Excel formats."""
    file_lower = file_path.lower()
    try:
        if file_lower.endswith('.xlsx') or file_lower.endswith('.xls'):
            df = pd.read_excel(file_path)
            print(f"📊 Loaded {len(df)} rows from Excel file: {file_path}")
        else:
            df = pd.read_csv(file_path)
            print(f"📊 Loaded {len(df)} rows from CSV file: {file_path}")
        return df
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return None

def load_config():
    """Load configuration from config.json"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"❌ config.json not found at {config_path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing config.json: {e}")
        return {}

def should_regenerate_queries(output_file_path):
    """Check if queries should be regenerated based on config flag and file existence"""
    config = load_config()
    regenerate_flag = config.get("REGENERATE_QUERIES", "yes").lower()
    
    # If file doesn't exist, always generate
    if not os.path.exists(output_file_path):
        return True
    
    # If regenerate flag is "no", "false", or "0", don't regenerate
    if regenerate_flag in ["no", "false", "0"]:
        print(f"✅ Output file '{output_file_path}' already exists and REGENERATE_QUERIES is set to '{config.get('REGENERATE_QUERIES')}'.")
        print("Skipping query generation. Set REGENERATE_QUERIES to 'yes' in config.json to regenerate.")
        return False
    
    return True

def generate_queries_with_difficulty_levels(agent, title, description, difficulties=["easy", "medium", "hard"]):
    """
    Generate queries with different difficulty levels and analyze them.
    
    Args:
        agent: GroundTruthAgent instance
        title: Document title
        description: Document description
        difficulties: List of difficulty levels
        
    Returns:
        Dict with queries by difficulty and selected best query
    """
    result = {
        "queries_by_difficulty": {},
        "best_query": "",
        "selected_difficulty": ""
    }
    
    # Generate one query for each difficulty level
    for difficulty in difficulties:
        try:
            query = agent.generate_search_query(title, description, difficulty=difficulty)
            if query:
                result["queries_by_difficulty"][difficulty] = query
                print(f"  {difficulty.upper()}: {query}")
        except Exception as e:
            print(f"  Error generating {difficulty} query: {e}")
    
    # Select the medium query as default best (balanced approach)
    if "medium" in result["queries_by_difficulty"]:
        result["best_query"] = result["queries_by_difficulty"]["medium"]
        result["selected_difficulty"] = "medium"
    elif result["queries_by_difficulty"]:
        # Fallback to any available query
        first_difficulty = list(result["queries_by_difficulty"].keys())[0]
        result["best_query"] = result["queries_by_difficulty"][first_difficulty]
        result["selected_difficulty"] = first_difficulty
    
    return result

def analyze_and_select_best_query(queries, title, description):
    """
    Analyze multiple queries and select the most optimized one based on various criteria.
    
    Args:
        queries: List of generated search queries
        title: Document title for context
        description: Document description for context
        
    Returns:
        Best query string
    """
    if not queries:
        return ""
    
    if len(queries) == 1:
        return queries[0]
    
    # Scoring criteria for query quality
    scored_queries = []
    
    for query in queries:
        score = 0
        query_lower = query.lower()
        title_lower = title.lower()
        desc_lower = description.lower()
        
        # 1. Length optimization (2-6 words is ideal)
        word_count = len(query.split())
        if 2 <= word_count <= 6:
            score += 3
        elif word_count == 1:
            score += 1
        elif word_count > 6:
            score -= 1
        
        # 2. Contains key terms from title (but not too many)
        title_words = set(re.findall(r'\b\w+\b', title_lower))
        query_words = set(re.findall(r'\b\w+\b', query_lower))
        title_overlap = len(title_words.intersection(query_words))
        if 1 <= title_overlap <= 3:
            score += 2
        elif title_overlap > 3:
            score -= 1
        
        # 3. Contains problem-specific terms from description
        problem_keywords = ['error', 'issue', 'problem', 'not working', 'failed', 'unable', 'cannot', 'how to', 'fix']
        for keyword in problem_keywords:
            if keyword in desc_lower and any(word in query_lower for word in keyword.split()):
                score += 1
                break
        
        # 4. Avoid overly technical terms (unless they're central to the problem)
        technical_terms = ['api', 'configuration', 'implementation', 'framework', 'library', 'dependency']
        tech_count = sum(1 for term in technical_terms if term in query_lower)
        if tech_count == 0:
            score += 1
        elif tech_count > 2:
            score -= 1
        
        # 5. Natural language patterns
        if any(pattern in query_lower for pattern in ['how to', 'unable to', 'cannot', 'does not', 'not working']):
            score += 1
        
        # 6. Avoid very generic terms
        generic_terms = ['problem', 'issue', 'question', 'help', 'need']
        if not any(term in query_lower for term in generic_terms):
            score += 1
        
        # 7. Technology/platform specific (good for searchability)
        platforms = ['android', 'java', 'python', 'javascript', 'react', 'spring', 'jenkins', 'docker']
        if any(platform in query_lower for platform in platforms):
            score += 1
        
        scored_queries.append((query, score))
    
    # Sort by score (descending) and return the best one
    scored_queries.sort(key=lambda x: x[1], reverse=True)
    return scored_queries[0][0]

def is_valid_row(row):
    """
    Check if a row has required fields. Only Title is strictly required.
    Description is optional (can be empty).
    Uses global TITLE_COLUMN and DESCRIPTION_COLUMN for column names.
    """
    global TITLE_COLUMN, DESCRIPTION_COLUMN
    
    # Get the values using configured column names, with fallbacks
    title = row.get(TITLE_COLUMN, '') or row.get(TITLE_COLUMN, '') or row.get('Title', '') or ''
    description = row.get(DESCRIPTION_COLUMN, '') or row.get(DESCRIPTION_COLUMN, '') or row.get('Description', '') or ''
    
    # Strip whitespace and check if they have meaningful content
    title = str(title).strip()
    description = str(description).strip()
    
    # Check for empty, NaN, or placeholder values
    invalid_values = ['', 'nan', 'null', 'none', 'n/a', 'undefined']
    
    # Only Title is required - Description is optional
    title_valid = title.lower() not in invalid_values and len(title) > 2
    
    # Description can be empty - just check it's not a placeholder like 'nan'
    if description.lower() in ['nan', 'null', 'none', 'undefined']:
        description = ''  # Treat these as empty
    
    return title_valid

def save_incremental_results(results, output_csv):
    """
    Save results incrementally to avoid losing progress if script is interrupted.
    """
    try:
        results_df = pd.DataFrame(results)
        results_df.to_csv(output_csv, index=False)
        return True
    except Exception as e:
        print(f"⚠️ Warning: Could not save incremental results: {e}")
        return False

def generate_optimized_search_queries(input_csv, output_csv=None, query_type: str | None = None, 
                                     llm_provider='openai', openai_key=None, gemini_key=None, model=None):
    """
    Reads a CSV, generates multiple search queries, analyzes them, and saves the best optimized query
    along with title, URL in a clean format. Handles missing/empty fields gracefully and saves incrementally.
    """
    # Read input file (CSV or Excel)
    df = read_input_file(input_csv)
    if df is None:
        return
        
    # We'll determine default output file after resolving difficulty

    # Check if we should regenerate queries
    if not should_regenerate_queries(output_csv):
        return

    # Filter valid rows
    valid_rows = []
    skipped_count = 0
    
    for idx, row in df.iterrows():
        if is_valid_row(row):
            valid_rows.append((idx, row))
        else:
            skipped_count += 1
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()[:50]
            print(f"⚠️ Skipping row {idx + 1}: Missing or invalid data. Title: '{title}...'")
    
    print(f"📊 Valid rows: {len(valid_rows)}, Skipped rows: {skipped_count}")
    
    if not valid_rows:
        print("❌ No valid rows found. Please check your CSV file format and content.")
        return

    # Initialize LLM agent - ONLY from command-line arguments (NO .env fallback)
    try:
        if llm_provider == 'openai':
            api_key = openai_key  # Only use what was passed via --openai-api-key
            if not api_key:
                print("❌ ERROR: OpenAI API key is required but not provided.")
                print("❌ Please enter your OpenAI API key in the frontend UI.")
                return
            model_name = model or "gpt-4o-mini"
            agent = GroundTruthAgent(openai_api_key=api_key, model=model_name, provider='openai', custom_prompts=custom_prompts)
            print(f"✅ Successfully initialized OpenAI agent (model: {model_name})")
            
        elif llm_provider == 'gemini':
            api_key = gemini_key  # Only use what was passed via --gemini-api-key
            if not api_key:
                print("❌ ERROR: Gemini API key is required but not provided.")
                print("❌ Please enter your Gemini API key in the frontend UI.")
                return
            model_name = model or "gemini-2.5-pro"
            agent = GroundTruthAgent(gemini_api_key=api_key, model=model_name, provider='gemini', custom_prompts=custom_prompts)
            print(f"✅ Successfully initialized Gemini agent (model: {model_name})")
        else:
            print(f"❌ Unsupported LLM provider: {llm_provider}")
            return
        
    except Exception as e:
        print(f"❌ Error initializing agent: {e}")
        return

    # Load optional judge settings from config
    config = load_config()
    threshold = int(str(config.get("JUDGE_THRESHOLD", 3)))
    max_attempts = int(str(config.get("JUDGE_MAX_ATTEMPTS", 3)))
    # Allow overriding via parameter; fallback to config key QUERY_DIFFICULTY or QUERY_TYPE
    configured_type = str(config.get("QUERY_TYPE", config.get("QUERY_DIFFICULTY", "medium"))).lower()
    difficulty = (query_type or configured_type).lower()

    # Decide default output path if not provided, based on difficulty (prompt type)
    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "query_outputs")
        os.makedirs(out_dir, exist_ok=True)
        output_csv = os.path.join(out_dir, f"queries_{difficulty}_{ts}.csv")

    # Prepare results and process incrementally
    results = []
    successful_queries = 0
    save_interval = 5  # Save every 5 queries
    
    print(f"\n🔍 Generating and optimizing search queries for {len(valid_rows)} valid documents...\n")
    
    for i, (original_idx, row) in enumerate(valid_rows):
        try:
            # Extract fields with fallback handling
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()
            description = str(row.get(DESCRIPTION_COLUMN, '') or row.get('Description', '')).strip()
            url = str(row.get(URL_COLUMN, '') or row.get('Document URL', '') or row.get('Document_URL', '') or row.get('URL', '') or row.get('url', '')).strip()
            
            print(f"Processing document {i + 1}/{len(valid_rows)} (Original row {original_idx + 1})")
            print(f"Title: {title[:60]}{'...' if len(title) > 60 else ''}")
            
            # Generate with LLM-as-judge until threshold or attempts exhausted
            judge_out = agent.generate_with_threshold(
                title=title,
                description=description,
                difficulty=difficulty,
                threshold=threshold,
                max_attempts=max_attempts,
            )

            gen_query = judge_out.get("query", "") or ""
            score = int(judge_out.get("score", 0) or 0)
            attempts = int(judge_out.get("attempts", 0) or 0)
            met = bool(judge_out.get("met_threshold", False))

            if gen_query:
                successful_queries += 1

            # Store result - standardized fields
            results.append({
                'Title': title,
                'Document_URL': url,
                'Generated_Query': gen_query,
                'score': score,
                'attempts': attempts,
                'met_threshold': met,
            })
            
            # Save incrementally every N queries
            if (i + 1) % save_interval == 0:
                save_incremental_results(results, output_csv)
                print(f"💾 Saved {len(results)} results so far...")
            
            print("-" * 60)
            
        except Exception as e:
            print(f"❌ Error processing document {i + 1}: {e}")
            # Still append a result with empty query to maintain consistency
            results.append({
                'Title': str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip(),
                'Document_URL': str(row.get(URL_COLUMN, '') or row.get('Document URL', '') or row.get('Document_URL', '') or row.get('URL', '') or row.get('url', '')).strip(),
                'Generated_Query': ""
            })

    # Final save
    try:
        results_df = pd.DataFrame(results)
        results_df.to_csv(output_csv, index=False)
        print(f"\n✅ Optimized queries CSV saved to: {output_csv}")
        print(f"📊 Generated {successful_queries} successful queries out of {len(results)} processed documents")
        print(f"📊 Skipped {skipped_count} invalid rows from original dataset")
    except Exception as e:
        print(f"❌ Error saving final results: {e}")
        return
    
    # Print summary statistics
    try:
        if results:
            # Filter out empty queries for statistics
            valid_rows = [r for r in results if r['Generated_Query']]

            if valid_rows:
                query_lengths = [len(r['Generated_Query'].split()) for r in valid_rows]
                avg_length = sum(query_lengths) / len(query_lengths)
                print(f"📈 Average query length: {avg_length:.1f} words")

                length_distribution = {}
                for length in query_lengths:
                    length_distribution[length] = length_distribution.get(length, 0) + 1

                print(f"📊 Query length distribution: {dict(sorted(length_distribution.items()))}")

                # Judge-related stats
                met_count = sum(1 for r in results if r.get('met_threshold'))
                print(f"📊 Met threshold: {met_count}/{len(results)} ({(met_count/len(results))*100:.1f}%) | threshold={threshold}, max_attempts={max_attempts}, difficulty={difficulty}")
            else:
                print("⚠️ No valid queries generated")
    except Exception as e:
        print(f"⚠️ Error generating statistics: {e}")

def generate_search_queries(input_csv, output_csv=None, query_type: str | None = None):
    """
    Original function - kept for backward compatibility.
    Reads a CSV with Title, Description, URL columns, generates search queries using GroundTruthAgent,
    and writes the result to a new CSV with an added 'Generated Search Query' column.
    Handles missing/empty fields gracefully and saves incrementally.
    """
    # Read input file (CSV or Excel)
    df = read_input_file(input_csv)
    if df is None:
        return
        
    # Resolve query type to pass to agent (back-compat default medium)
    config = load_config()
    configured_type = str(config.get("QUERY_TYPE", config.get("QUERY_DIFFICULTY", "medium"))).lower()
    difficulty = (query_type or configured_type).lower()

    # Default output path if not provided: query_outputs/queries_<type>_<timestamp>.csv
    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "query_outputs")
        os.makedirs(out_dir, exist_ok=True)
        output_csv = os.path.join(out_dir, f"queries_{difficulty}_{ts}.csv")

    # Check if we should regenerate queries
    if not should_regenerate_queries(output_csv):
        return

    # Filter valid rows and track invalid ones
    valid_indices = []
    skipped_count = 0
    
    for idx, row in df.iterrows():
        if is_valid_row(row):
            valid_indices.append(idx)
        else:
            skipped_count += 1
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()[:50]
            print(f"⚠️ Skipping row {idx + 1}: Missing or invalid data. Title: '{title}...'")
    
    print(f"📊 Valid rows: {len(valid_indices)}, Skipped rows: {skipped_count}")
    
    if not valid_indices:
        print("❌ No valid rows found. Please check your CSV file format and content.")
        return

    # Initialize OpenAI-based agent
    try:
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key or openai_api_key == "your_openai_api_key_here":
            print("❌ Please set your OPENAI_API_KEY in the .env file")
            return
            
        agent = GroundTruthAgent(openai_api_key=openai_api_key, model="gpt-4o-mini")
        print("✅ Successfully initialized OpenAI agent")
        
    except Exception as e:
        print(f"❌ Error initializing OpenAI agent: {e}")
        return

    # Generate queries for each valid row
    queries = [''] * len(df)  # Initialize with empty strings for all rows
    successful_queries = 0
    save_interval = 5  # Save every 5 queries
    
    print(f"\n🔍 Generating search queries for {len(valid_indices)} valid documents...\n")
    
    for i, idx in enumerate(valid_indices):
        try:
            row = df.iloc[idx]
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()
            description = str(row.get(DESCRIPTION_COLUMN, '') or row.get('Description', '')).strip()
            
            print(f"Processing document {i + 1}/{len(valid_indices)} (Row {idx + 1})")
            print(f"Title: {title[:60]}{'...' if len(title) > 60 else ''}")
            
            # Call agent to generate search query
            search_query = agent.generate_search_query(title, description, difficulty=difficulty)
            if search_query:
                queries[idx] = search_query
                successful_queries += 1
                print(f"Generated Query: {search_query}")
            else:
                print("⚠️ Failed to generate query")
            
            # Save incrementally every N queries
            if (i + 1) % save_interval == 0:
                temp_df = df.copy()
                temp_df['Generated Search Query'] = queries
                temp_df.to_csv(output_csv, index=False)
                print(f"💾 Saved progress: {i + 1}/{len(valid_indices)} queries processed...")
            
            print("-" * 60)
            
        except Exception as e:
            print(f"❌ Error processing row {idx + 1}: {e}")
            print("-" * 60)

    # Add new column and save final result
    try:
        df['Generated Search Query'] = queries
        df.to_csv(output_csv, index=False)
        print(f"\n✅ Updated CSV saved to: {output_csv}")
        print(f"📊 Generated {successful_queries} successful queries out of {len(valid_indices)} valid documents")
        print(f"📊 Skipped {skipped_count} invalid rows from original dataset")
    except Exception as e:
        print(f"❌ Error saving final results: {e}")
        return


def generate_multiple_queries_per_document(input_csv, output_csv=None, queries_per_doc=3, query_type: str | None = None):
    """
    Generate multiple search queries for each document for more comprehensive testing.
    This creates an expanded dataset with multiple query variations per document.
    Handles missing/empty fields gracefully and saves incrementally.
    """
    # Read input file (CSV or Excel)
    df = read_input_file(input_csv)
    if df is None:
        return
        
    # Resolve query type
    config = load_config()
    configured_type = str(config.get("QUERY_TYPE", config.get("QUERY_DIFFICULTY", "medium"))).lower()
    difficulty = (query_type or configured_type).lower()

    # Default output path if not provided: query_outputs/queries_<type>_multiple_<timestamp>.csv
    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "query_outputs")
        os.makedirs(out_dir, exist_ok=True)
        output_csv = os.path.join(out_dir, f"queries_{difficulty}_multiple_{ts}.csv")

    # Check if we should regenerate queries
    if not should_regenerate_queries(output_csv):
        return

    # Filter valid rows
    valid_rows = []
    skipped_count = 0
    
    for idx, row in df.iterrows():
        if is_valid_row(row):
            valid_rows.append((idx, row))
        else:
            skipped_count += 1
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()[:50]
            print(f"⚠️ Skipping row {idx + 1}: Missing or invalid data. Title: '{title}...'")
    
    print(f"📊 Valid rows: {len(valid_rows)}, Skipped rows: {skipped_count}")
    
    if not valid_rows:
        print("❌ No valid rows found. Please check your CSV file format and content.")
        return

    # Initialize OpenAI-based agent
    try:
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key or openai_api_key == "your_openai_api_key_here":
            print("❌ Please set your OPENAI_API_KEY in the .env file")
            return
            
        agent = GroundTruthAgent(openai_api_key=openai_api_key, model="gpt-4o-mini")
        print("✅ Successfully initialized OpenAI agent")
        
    except Exception as e:
        print(f"❌ Error initializing OpenAI agent: {e}")
        return

    # difficulty already resolved above

    # Prepare expanded dataframe
    expanded_rows = []
    successful_queries = 0
    save_interval = 3  # Save every 3 documents (since each doc generates multiple queries)
    
    print(f"\n🔍 Generating {queries_per_doc} search queries for each of {len(valid_rows)} valid documents...\n")
    
    for i, (original_idx, row) in enumerate(valid_rows):
        try:
            title = str(row.get(TITLE_COLUMN, '') or row.get('Title', '')).strip()
            description = str(row.get(DESCRIPTION_COLUMN, '') or row.get('Description', '')).strip()
            
            print(f"Processing document {i + 1}/{len(valid_rows)} (Original row {original_idx + 1})")
            print(f"Title: {title[:60]}{'...' if len(title) > 60 else ''}")
            
            # Generate multiple queries for this document
            # For multiple queries, vary temperature through the agent itself; here we reuse difficulty
            queries = agent.generate_multiple_queries(title, description, queries_per_doc)
            
            # If we didn't get enough queries, pad with empty strings
            while len(queries) < queries_per_doc:
                queries.append("")
            
            # Create a row for each query
            for j, query in enumerate(queries[:queries_per_doc]):
                new_row = row.copy()
                new_row['Query_ID'] = f"{original_idx + 1}_{j + 1}"
                new_row['Generated Search Query'] = query
                expanded_rows.append(new_row)
                
                if query:
                    successful_queries += 1
                    print(f"  Query {j + 1}: {query}")
                else:
                    print(f"  Query {j + 1}: [Empty - generation failed]")
            
            # Save incrementally every N documents
            if (i + 1) % save_interval == 0:
                temp_df = pd.DataFrame(expanded_rows)
                temp_df.to_csv(output_csv, index=False)
                print(f"💾 Saved progress: {i + 1}/{len(valid_rows)} documents processed...")
            
            print("-" * 60)
            
        except Exception as e:
            print(f"❌ Error processing document {i + 1}: {e}")
            # Still create entries with empty queries to maintain structure
            for j in range(queries_per_doc):
                new_row = row.copy()
                new_row['Query_ID'] = f"{original_idx + 1}_{j + 1}"
                new_row['Generated Search Query'] = ""
                expanded_rows.append(new_row)
            print("-" * 60)

    # Create new dataframe and save final result
    try:
        expanded_df = pd.DataFrame(expanded_rows)
        expanded_df.to_csv(output_csv, index=False)
        print(f"\n✅ Expanded CSV saved to: {output_csv}")
        print(f"📊 Generated {len(expanded_df)} query-document pairs from {len(valid_rows)} valid documents")
        print(f"📊 Successful queries: {successful_queries} out of {len(expanded_df)} total pairs")
        print(f"📊 Skipped {skipped_count} invalid rows from original dataset")
    except Exception as e:
        print(f"❌ Error saving final results: {e}")
        return


if __name__ == "__main__":
    # Default paths
    input_csv = "/home/abhijeetsingh1/RelevanceEvaluator-Vespa_verizon/search_relevance_project/output_for_query_testing.csv"

    # Only parse input CSV from CLI if provided; prompt type will always be asked interactively
    non_interactive = False
    chosen_type_cli = None
    llm_provider = 'openai'
    # TITLE_COLUMN and DESCRIPTION_COLUMN are module-level globals, modified below via CLI args
    openai_key_arg = None
    gemini_key_arg = None
    model_arg = None
    custom_prompts_file = None
    try:
        for a in sys.argv[1:]:
            if a.startswith("--input="):
                input_csv = a.split("=", 1)[1].strip()
            elif a.startswith('--prompt-type='):
                chosen_type_cli = a.split('=',1)[1].strip().lower()
            elif a in ('--no-prompt','--non-interactive','--auto'):
                non_interactive = True
            elif a.startswith('--llm-provider='):
                llm_provider = a.split('=', 1)[1].strip().lower()
            elif a.startswith('--openai-api-key='):
                openai_key_arg = a.split('=', 1)[1].strip()
            elif a.startswith('--gemini-api-key='):
                gemini_key_arg = a.split('=', 1)[1].strip()
            elif a.startswith('--model='):
                model_arg = a.split('=', 1)[1].strip()
            elif a.startswith('--custom-prompts='):
                custom_prompts_file = a.split('=', 1)[1].strip()
            elif a.startswith('--title-column='):
                TITLE_COLUMN = a.split('=', 1)[1].strip()
            elif a.startswith('--description-column='):
                DESCRIPTION_COLUMN = a.split('=', 1)[1].strip()
            elif a.startswith('--url-column='):
                URL_COLUMN = a.split('=', 1)[1].strip()
    except Exception:
        pass
    
    print(f"📋 Using columns - Title: '{TITLE_COLUMN}', Description: '{DESCRIPTION_COLUMN}', URL: '{URL_COLUMN}'")

    # Load custom prompts if provided
    custom_prompts = {}
    if custom_prompts_file and os.path.exists(custom_prompts_file):
        try:
            import json
            with open(custom_prompts_file, 'r') as f:
                custom_prompts = json.load(f)
            print(f"✅ Loaded {len(custom_prompts)} custom prompt(s) from {custom_prompts_file}")
        except Exception as e:
            print(f"⚠️ Warning: Could not load custom prompts: {e}")

    # Central list of supported prompt types (must align with GroundTruthAgent.generate_search_query)
    PROMPT_TYPE_DESCRIPTIONS = {
        "easy": "Longer, specific, expert-style noisy query (6+ tokens)",
        "medium": "Balanced mid-detail query (4–5 tokens)",
        "hard": "Broad, short, high-level semantic query (2–3 tokens)",
        "special_char": "Includes real special-character token (CVE, version, code)",
        "keyword_based": "Generic 2–3 keyword query (very simple)",
        "typo": "Short query containing exactly one realistic misspelling",
        "synonym_abbrev": "Mix of abbreviation + synonym/related concept",
    }

    allowed_types = list(PROMPT_TYPE_DESCRIPTIONS.keys())

    print("Available prompt types (choose one):")
    for i, (ptype, desc) in enumerate(PROMPT_TYPE_DESCRIPTIONS.items(), start=1):
        print(f"  {i}. {ptype:<15} - {desc}")

    # Prompt selection logic
    if chosen_type_cli and chosen_type_cli in allowed_types:
        chosen_type = chosen_type_cli
    elif non_interactive:
        # default in non-interactive mode
        chosen_type = 'medium'
    else:
        chosen_type = None
        try:
            raw = input(f"Enter number or name (default=medium):").strip().lower()
            if not raw:
                chosen_type = "medium"
            else:
                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(allowed_types):
                        chosen_type = allowed_types[idx - 1]
                if chosen_type is None and raw in allowed_types:
                    chosen_type = raw
        except EOFError:
            chosen_type = "medium"

        if chosen_type not in allowed_types:
            print("Unrecognized selection. Falling back to 'medium'.")
            chosen_type = "medium"

    print("🚀 Starting search query generation...")
    print(f"Input CSV: {input_csv}")
    print(f"Prompt type: {chosen_type} -> {PROMPT_TYPE_DESCRIPTIONS.get(chosen_type, '')}")
    print(f"LLM Provider: {llm_provider}")

    # Build output path under query_outputs and include prompt type
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'query_outputs')
    os.makedirs(out_dir, exist_ok=True)
    # Allow override via --output= CLI arg (parsed earlier if present)
    output_override = None
    for a in sys.argv[1:]:
        if a.startswith('--output='):
            output_override = a.split('=',1)[1].strip()
            break
    if output_override:
        optimized_csv = output_override
    else:
        optimized_csv = os.path.join(out_dir, f"queries_{chosen_type}_{ts}.csv")
    os.makedirs(os.path.dirname(optimized_csv), exist_ok=True)
    # Machine-readable line for backend parsing
    print(f"OUTPUT_CSV: {optimized_csv}")
    print(f"Optimized Output CSV: {optimized_csv}")
    print("\n" + "="*80)
    print("🎯 Generating OPTIMIZED search queries...")
    generate_optimized_search_queries(input_csv, optimized_csv, query_type=chosen_type,
                                     llm_provider=llm_provider, openai_key=openai_key_arg, 
                                     gemini_key=gemini_key_arg, model=model_arg)
   