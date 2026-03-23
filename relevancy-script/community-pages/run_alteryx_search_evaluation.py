#!/usr/bin/env python3
"""
Run search evaluation for Alteryx community queries.

Reads a CSV of regenerated queries (columns expected: Title, Document_URL, Generated_Query),
performs a search for each Generated_Query using the local Alteryx client, retrieves the top N
results (default 10), and writes a results CSV in the same folder with the following columns:

Query_ID,Generated_Query,Original_Title,Original_URL,Result_Rank,Result_Title,Result_URL,Result_Description,Search_Score,Is_Original_Match,Is_Title_Match,Is_URL_Match

This mirrors the format used in other evaluation scripts (see `cyberark_community_results.csv`).
"""
import os
import sys
import csv
import argparse
import pandas as pd
import re
from typing import List

# Make sure current script dir is on path so we can import the client module
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

try:
    import alteryx_search_client as client
except Exception as e:
    print(f"❌ Could not import alteryx_search_client from {THIS_DIR}: {e}")
    raise


def clean_title_for_matching(title: str) -> str:
    if not title:
        return ""
    s = title.lower().strip()
    # remove html tags
    s = re.sub(r'<[^>]+>', '', s)
    # remove punctuation
    s = re.sub(r'[\W_]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def clean_url(url: str) -> str:
    if not url:
        return ""
    s = url.strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = s.rstrip('/')
    return s


def is_title_match(original: str, candidate: str) -> bool:
    if not original or not candidate:
        return False
    o = clean_title_for_matching(original)
    c = clean_title_for_matching(candidate)
    # exact or substring match (bidirectional)
    return (o == c) or (o in c) or (c in o)


def is_url_match(original: str, candidate: str) -> bool:
    if not original or not candidate:
        return False
    return clean_url(original) == clean_url(candidate)


def run(input_csv: str, output_csv: str = None, top_n: int = 10, curl_path: str = None):
    df = pd.read_csv(input_csv)
    print(f"📄 Loaded {len(df)} queries from {input_csv}")

    # Validate expected columns
    for col in ('Generated_Query', 'Title', 'Document_URL'):
        if col not in df.columns:
            raise RuntimeError(f"Input CSV missing required column: {col}")

    if not output_csv:
        base, _ = os.path.splitext(input_csv)
        output_csv = base + '_search_results.csv'

    rows = []

    for idx, row in df.iterrows():
        qid = idx + 1
        query = str(row.get('Generated_Query', '')).strip()
        orig_title = row.get('Title', '')
        orig_url = row.get('Document_URL', '')

        print(f"\n🔍 ({qid}) Query: {query[:80]}")
        try:
            docs = client.fetch_documents(curl_path or os.path.join(THIS_DIR, 'curl_alteryx_run.json'), search_string=query, results_per_page=top_n)
        except Exception as e:
            print(f"❌ Error fetching documents for query {qid}: {e}")
            # write a single error row
            rows.append({
                'Query_ID': qid,
                'Generated_Query': query,
                'Original_Title': orig_title,
                'Original_URL': orig_url,
                'Result_Rank': -1,
                'Result_Title': f'ERROR: {e}',
                'Result_URL': '',
                'Result_Description': '',
                'Search_Score': 0.0,
                'Is_Original_Match': 'NO',
                'Is_Title_Match': 'NO',
                'Is_URL_Match': 'NO'
            })
            continue

        if not docs:
            rows.append({
                'Query_ID': qid,
                'Generated_Query': query,
                'Original_Title': orig_title,
                'Original_URL': orig_url,
                'Result_Rank': 0,
                'Result_Title': 'NO_RESULTS_FOUND',
                'Result_URL': '',
                'Result_Description': '',
                'Search_Score': 0.0,
                'Is_Original_Match': 'NO',
                'Is_Title_Match': 'NO',
                'Is_URL_Match': 'NO'
            })
            continue

        for i, d in enumerate(docs[:top_n]):
            is_title = 'YES' if is_title_match(orig_title, d.get('title','')) else 'NO'
            is_url = 'YES' if is_url_match(orig_url, d.get('url','')) else 'NO'
            is_orig = 'YES' if (is_title == 'YES' and is_url == 'YES') else 'NO'

            rows.append({
                'Query_ID': qid if i == 0 else '',
                'Generated_Query': query if i == 0 else '',
                'Original_Title': orig_title if i == 0 else '',
                'Original_URL': orig_url if i == 0 else '',
                'Result_Rank': d.get('rank', i+1),
                'Result_Title': d.get('title',''),
                'Result_URL': d.get('url',''),
                'Result_Description': d.get('description',''),
                'Search_Score': d.get('score',0.0),
                'Is_Original_Match': is_orig,
                'Is_Title_Match': is_title,
                'Is_URL_Match': is_url
            })

    # Save CSV
    out_dir = os.path.dirname(output_csv)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    out_df = pd.DataFrame(rows)
    out_df.to_csv(output_csv, index=False)
    print(f"\n✅ Saved evaluation results to: {output_csv}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default=os.path.join(THIS_DIR, 'tmpbtgu1rqy.csv'), help='Input CSV path (Title,Document_URL,Generated_Query)')
    parser.add_argument('--output', default=None, help='Output CSV path (defaults to <input>_search_results.csv in same folder)')
    parser.add_argument('--top', type=int, default=10, help='Top N results to fetch per query')
    parser.add_argument('--curl', default=os.path.join(THIS_DIR, 'curl_alteryx_run.json'), help='Path to curl template JSON')
    args = parser.parse_args(argv)

    run(args.input, args.output, top_n=args.top, curl_path=args.curl)


if __name__ == '__main__':
    main()
