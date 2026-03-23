import os
import sys
import json
import random
from datetime import datetime
from typing import List, Tuple, Sequence

import pandas as pd
from dotenv import load_dotenv

# Ensure we can import the agents module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.ground_truth_agent import GroundTruthAgent


load_dotenv()


def load_config():
    """Load configuration from config.json (optional)."""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


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


def is_valid_row(row, title_col: str = 'Title', desc_col: str = 'Description') -> bool:
    """Simple validation - only Title is strictly required. Description and URL are optional."""
    title = str(row.get(title_col, '') or '').strip()
    invalid_values = {'', 'nan', 'null', 'none', 'n/a', 'undefined'}
    
    # Only Title is required - must exist and have meaningful content
    return title.lower() not in invalid_values and len(title) > 2


def pick_prompt_type(types: Sequence[str], weights: Sequence[float]) -> str:
    """Pick a prompt type according to provided types & weights."""
    return random.choices(types, weights=weights, k=1)[0]


def ensure_out_dir() -> str:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'query_outputs')
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def prompt_for_weights(defaults: Tuple[float, float, float] = (0.35, 0.45, 0.20)) -> Tuple[float, float, float]:
    """Prompt user for weights; accept decimals or percentages. Returns normalized decimals summing to 1."""
    pretty_defaults = f"{int(defaults[0]*100)},{int(defaults[1]*100)},{int(defaults[2]*100)}"
    print("Enter weights for (hard, keyword_based, special_char).")
    print("Examples: 35,45,20 (percent) or 0.35,0.45,0.20 (decimals).")
    try:
        raw = input(f"Weights [default {pretty_defaults}]: ").strip()
    except EOFError:
        raw = ""
    if not raw:
        return defaults

    try:
        parts = [p.strip() for p in raw.split(',')]
        if len(parts) != 3:
            print("Invalid format; expected 3 comma-separated values. Using defaults.")
            return defaults
        vals = [float(p) for p in parts]
        if any(v < 0 for v in vals):
            print("Weights must be non-negative. Using defaults.")
            return defaults
        total = sum(vals)
        # If they look like percentages, convert to decimals
        if total > 1.5:  # e.g., 35+45+20 = 100
            vals = [v / 100.0 for v in vals]
            total = sum(vals)
        if total == 0:
            print("Sum of weights is zero. Using defaults.")
            return defaults
        # Normalize to sum to 1
        vals = [v / total for v in vals]
        return (vals[0], vals[1], vals[2])
    except Exception:
        print("Could not parse weights. Using defaults.")
        return defaults


def generate_queries_with_distribution(
    input_csv: str,
    output_csv: str = None,
    weights: Sequence[float] = (0.35, 0.45, 0.20),
    prompt_types: Sequence[str] | None = None,
    deterministic: bool = False,
    seed: int | None = None,
    llm_provider: str = 'openai',
    openai_key: str = None,
    gemini_key: str = None,
    model: str = None,
    custom_prompts: dict = None,
    title_column: str = 'Title',
    description_column: str = 'Description',
    url_column: str = 'Document URL',
):
    """
    Generate one query per document using a distribution of prompt types:
    - semantic queries => 'hard' prompt (default ~35%)
    - keyword hard queries => 'keyword_based' prompt (default ~45%)
    - special character queries => 'special_char' prompt (default ~20%)

    Saves CSV under query_outputs/ with a timestamped filename by default and adds 'Prompt_Type'.
    prompt_types: list of difficulty/prompt labels (e.g. ["hard","keyword_based","special_char"]).
    """
    # Read input file (CSV or Excel)
    df = read_input_file(input_csv)
    if df is None:
        return

    # Print detected columns for debugging
    print(f"📝 Using columns - Title: '{title_column}', Description: '{description_column or 'None'}'")
    if title_column not in df.columns:
        print(f"⚠️ Warning: Title column '{title_column}' not found in CSV. Available columns: {list(df.columns)}")
    if description_column and description_column not in df.columns:
        print(f"⚠️ Warning: Description column '{description_column}' not found in CSV. Available columns: {list(df.columns)}")

    # Validate rows
    valid_indices: List[int] = []
    for idx, row in df.iterrows():
        if is_valid_row(row, title_column, description_column):
            valid_indices.append(idx)
        else:
            title = str(row.get(title_column, '')).strip()[:60]
            print(f"⚠️ Skipping row {idx + 1}: invalid data. Title: '{title}…'")

    if not valid_indices:
        print("❌ No valid rows to process.")
        return

    # Default prompt types if none provided (legacy behavior)
    if not prompt_types:
        prompt_types = ["hard", "keyword_based", "special_char"]

    # Validate alignment
    if len(weights) != len(prompt_types):
        print(f"❌ Mismatch: {len(prompt_types)} prompt types but {len(weights)} weights.")
        return

    # Normalize weights (accept percents or decimals)
    try:
        numeric = [float(w) for w in weights]
        total = sum(numeric)
        if total == 0:
            numeric = [1.0 for _ in numeric]
            total = float(len(numeric))
        # If they look like percents (sum > 1.5), convert
        if total > 1.5:
            numeric = [w / total for w in numeric]
        else:
            # Already decimals but may not sum to 1; normalize
            s = sum(numeric)
            numeric = [w / s for w in numeric]
        weights = numeric
    except Exception as e:
        print(f"❌ Could not parse weights: {e}")
        return

    # Output path
    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_csv = os.path.join(ensure_out_dir(), f"Distributed_queries_{ts}.csv")
    # Ensure parent directory exists (in case a custom path was provided)
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    # Always echo a deterministic machine-readable line early for backend parsing
    print(f"OUTPUT_CSV: {output_csv}")

    # Initialize agent
    try:
        # Get API keys ONLY from command-line arguments (passed from frontend)
        # NO fallback to environment variables
        if llm_provider == 'openai':
            api_key = openai_key  # Only use what was passed via --openai-api-key
            if not api_key:
                print("❌ ERROR: OpenAI API key is required but not provided.")
                print("❌ Please enter your OpenAI API key in the frontend UI.")
                return
            model_name = model or "gpt-4o-mini"
            agent = GroundTruthAgent(openai_api_key=api_key, model=model_name, provider='openai', custom_prompts=custom_prompts)
            print(f"✅ OpenAI agent ready (model: {model_name})")
        elif llm_provider == 'gemini':
            api_key = gemini_key  # Only use what was passed via --gemini-api-key
            if not api_key:
                print("❌ ERROR: Gemini API key is required but not provided.")
                print("❌ Please enter your Gemini API key in the frontend UI.")
                return
            model_name = model or "gemini-2.5-pro"
            agent = GroundTruthAgent(gemini_api_key=api_key, model=model_name, provider='gemini', custom_prompts=custom_prompts)
            print(f"✅ Gemini agent ready (model: {model_name})")
        else:
            print(f"❌ Unsupported LLM provider: {llm_provider}")
            return
    except Exception as e:
        print(f"❌ Error initializing agent: {e}")
        return

    # Stats + deterministic sequence preparation
    total_docs = len(valid_indices)
    type_counts = {t: 0 for t in prompt_types}
    results = []
    save_every = 5

    pretty_dist = ", ".join(f"{t}:{w*100:.1f}%" for t, w in zip(prompt_types, weights))
    print(f"\n🔀 Using distribution weights → {pretty_dist}\n")
    print(f"🔍 Generating queries for {total_docs} valid documents…\n")

    deterministic_sequence: list[str] | None = None
    if deterministic:
        # Compute exact integer counts using largest remainder method + round-robin ordering
        exact_counts_float = [w * total_docs for w in weights]
        base_counts = [int(x) for x in exact_counts_float]
        remainder = total_docs - sum(base_counts)
        # Allocate remaining by largest fractional part
        fractional_parts = [(i, exact_counts_float[i] - base_counts[i]) for i in range(len(base_counts))]
        fractional_parts.sort(key=lambda x: x[1], reverse=True)
        for i in range(remainder):
            base_counts[fractional_parts[i][0]] += 1
        # Interleave in round-robin to avoid long blocks
        working = [[prompt_types[i]] * base_counts[i] for i in range(len(prompt_types))]
        deterministic_sequence = []
        while any(working):
            for bucket in working:
                if bucket:
                    deterministic_sequence.append(bucket.pop())
        if seed is not None:
            random.Random(seed).shuffle(deterministic_sequence)
        # Echo expected vs assigned counts
        print("📐 Deterministic exact distribution counts:")
        for pt, cnt, target in zip(prompt_types, [deterministic_sequence.count(p) for p in prompt_types], exact_counts_float):
            print(f"  - {pt}: {cnt} (target ~{target:.2f})")
    else:
        if seed is not None:
            random.seed(seed)

    for i, idx in enumerate(valid_indices):
        row = df.iloc[idx]
        title = str(row.get(title_column, '')).strip()
        description = str(row.get(description_column, '')).strip() if description_column else ''
        url = str(row.get(url_column, '') or row.get('Document URL', '') or row.get('Document_URL', '') or row.get('URL', '') or row.get('url', '')).strip()

        if deterministic and deterministic_sequence:
            prompt_type = deterministic_sequence[i]
        else:
            prompt_type = pick_prompt_type(prompt_types, weights)
        type_counts[prompt_type] += 1

        print(f"[{i+1}/{total_docs}] {prompt_type} → {title[:64]}{'…' if len(title) > 64 else ''}")

        try:
            query = agent.generate_search_query(title, description, difficulty=prompt_type)
        except Exception as e:
            print(f"  ❌ Generation error: {e}")
            query = ""

        results.append({
            'Title': title,
            'Document_URL': url,
            'Generated_Query': query,
            'Prompt_Type': prompt_type,
        })

        if (i + 1) % save_every == 0:
            pd.DataFrame(results).to_csv(output_csv, index=False)
            print(f"  💾 Saved {len(results)} rows so far → {output_csv}")

    # Final save
    try:
        out_df = pd.DataFrame(results)
        out_df.to_csv(output_csv, index=False)
        print(f"\n✅ Saved results to: {output_csv}")
    except Exception as e:
        print(f"❌ Error saving results: {e}")
        return

    # Summary
    total = len(results)
    if total:
        print("📊 Mix summary →")
        for t in prompt_types:
            c = type_counts.get(t, 0)
            pct_val = (100.0 * c / total) if total else 0
            print(f"  - {t}: {c} ({pct_val:.1f}%)")


if __name__ == "__main__":
    default_input = os.path.join(os.path.dirname(__file__), '../output.csv')
    input_csv = default_input
    weights: Sequence[float] = (0.35, 0.45, 0.20)
    non_interactive = False
    output_override = None
    chosen_prompt_types: Sequence[str] | None = None
    deterministic_mode = False
    seed_value: int | None = None
    llm_provider = 'openai'
    openai_key_arg = None
    gemini_key_arg = None
    model_arg = None
    custom_prompts_file = None
    title_column_arg = 'Title'
    description_column_arg = 'Description'
    url_column_arg = 'Document URL'
    try:
        for a in sys.argv[1:]:
            if a.startswith('--input='):
                input_csv = a.split('=', 1)[1].strip()
            elif a.startswith('--weights='):
                parts = a.split('=', 1)[1].split(',')
                try:
                    weights = [float(x) for x in parts if x.strip()]
                except Exception:
                    pass
            elif a.startswith('--prompt-types='):
                p_raw = a.split('=', 1)[1].strip()
                chosen_prompt_types = [p.strip() for p in p_raw.split(',') if p.strip()]
            elif a.startswith('--output='):
                output_override = a.split('=', 1)[1].strip()
            elif a in ('--no-prompt', '--non-interactive', '--auto'):
                non_interactive = True
            elif a in ('--deterministic','--exact','--exact-distribution'):
                deterministic_mode = True
            elif a.startswith('--seed='):
                try:
                    seed_value = int(a.split('=',1)[1].strip())
                except ValueError:
                    pass
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
                title_column_arg = a.split('=', 1)[1].strip()
            elif a.startswith('--description-column='):
                description_column_arg = a.split('=', 1)[1].strip()
            elif a.startswith('--url-column='):
                url_column_arg = a.split('=', 1)[1].strip()
    except Exception:
        pass

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

    # Interactive selection only if not non-interactive and no prompt types provided
    if not non_interactive and not chosen_prompt_types:
        try:
            available = ["hard","keyword_based","special_char","easy","medium","typo","synonym_abbrev"]
            print("Available prompt types:")
            print(", ".join(available))
            raw_types = input("Enter prompt types (comma separated) [default=hard,keyword_based,special_char]: ").strip()
            if raw_types:
                chosen_prompt_types = [p.strip() for p in raw_types.split(',') if p.strip()]
            else:
                chosen_prompt_types = ["hard","keyword_based","special_char"]
        except EOFError:
            chosen_prompt_types = ["hard","keyword_based","special_char"]

    if not non_interactive and chosen_prompt_types:
        # Ask weights interactively (optional) only if user wants to customize
        pretty_default = ",".join(str(int(w*100)) for w in (weights if len(weights)==len(chosen_prompt_types) else [1/len(chosen_prompt_types)]*len(chosen_prompt_types)))
        try:
            raw_w = input(f"Weights for {len(chosen_prompt_types)} prompts (comma, % or decimals) [default={pretty_default}]: ").strip()
        except EOFError:
            raw_w = ""
        if raw_w:
            parts = [p.strip() for p in raw_w.split(',') if p.strip()]
            try:
                weights = [float(x) for x in parts]
            except Exception:
                print("Could not parse custom weights; using defaults/equal.")
        if len(weights) != len(chosen_prompt_types):
            weights = [1.0/len(chosen_prompt_types)]*len(chosen_prompt_types)

    prompt_types_arg = chosen_prompt_types if chosen_prompt_types else None

    # If non-interactive we default to deterministic exact distribution unless user opted out
    if non_interactive and not deterministic_mode:
        deterministic_mode = True

    print("🚀 Generating distributed queries…")
    print(f"Input CSV: {input_csv}")
    if prompt_types_arg:
        print(f"Prompt Types: {prompt_types_arg}")
    print(f"Raw Weights Input: {weights}")
    if output_override:
        print(f"Custom output path requested: {output_override}")
    if deterministic_mode:
        print("Mode: Deterministic exact distribution (no sampling variance)")
        if seed_value is not None:
            print(f"Shuffle Seed: {seed_value}")
    else:
        print("Mode: Probabilistic sampling (random.choices)")

    generate_queries_with_distribution(
        input_csv=input_csv,
        output_csv=output_override,
        weights=weights,
        prompt_types=prompt_types_arg,
        deterministic=deterministic_mode,
        seed=seed_value,
        llm_provider=llm_provider,
        openai_key=openai_key_arg,
        gemini_key=gemini_key_arg,
        model=model_arg,
        custom_prompts=custom_prompts,
        title_column=title_column_arg,
        description_column=description_column_arg,
        url_column=url_column_arg,
    )
