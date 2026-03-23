#!/usr/bin/env python3
"""Run a list of Braze search queries against the Braze SearchUnify endpoint
and export the top N results for each query to an Excel file."""
import os
import sys
import pandas as pd
from datetime import datetime

# Ensure parent path for agents package
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.braze_search_agent import BrazeSearchAgent  # noqa: E402


QUERIES = [
    "Guide to Completing Google Play’s Data Safety Form with Braze",
    "Setting Up and Managing Data Syncs with Braze Cloud Data Ingestion (CDI)",
    "Configuring and Updating Scheduled Triggers in Braze Canvas",
    "Deleting Scheduled Messages or Triggers in Braze Canvas",
    "Deleting SDK Authentication Keys via Braze API",
    "Understanding Braze SDK Analytics Events for Apple Privacy Manifest Declarations",
    "Optimizing Custom Data Usage to Reduce Datapoint Consumption in Braze",
    "Handling Anonymous Users Without External IDs in Braze",
    "Seed Users Not Receiving Messages Due to Missing Default in Reply-to Liquid Personalization",
    "Push Notification Requirements: iOS vs. Android SDK in Braze",
]


def run_braze_search(queries: list[str], top_n: int = 10, output_xlsx: str | None = None):
    # Ensure we use the braze config; bearer token can be passed via env SEARCH_BEARER_TOKEN
    os.environ["SEARCH_CONFIG"] = "braze"
    agent = BrazeSearchAgent()
    print(f"✅ Initialized Braze SearchAgent | base_url={agent.base_url}")

    rows = []
    for idx, query in enumerate(queries, start=1):
        print(f"\n🔍 ({idx}/{len(queries)}) Query: {query}")
        try:
            docs = agent.search_and_extract(query, top_n=top_n)
            if not docs:
                rows.append({
                    "Query": query,
                    "Rank": 0,
                    "Result_Title": "NO_RESULTS",
                    "Result_URL": "",
                    "Result_Description": "",
                    "Score": 0.0,
                })
                continue
            for d in docs[:top_n]:
                rows.append({
                    "Query": query,
                    "Rank": d.get("rank", 0),
                    "Result_Title": d.get("title", ""),
                    "Result_URL": d.get("url", ""),
                    "Result_Description": d.get("description", ""),
                    "Score": d.get("score", 0.0),
                })
        except Exception as e:
            print(f"❌ Error for query '{query}': {e}")
            rows.append({
                "Query": query,
                "Rank": -1,
                "Result_Title": f"ERROR: {e}",
                "Result_URL": "",
                "Result_Description": "",
                "Score": 0.0,
            })

    df = pd.DataFrame(rows)

    if output_xlsx is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "query_outputs")
        os.makedirs(out_dir, exist_ok=True)
        output_xlsx = os.path.join(out_dir, f"braze_queries_results_{ts}.xlsx")

    try:
        df.to_excel(output_xlsx, index=False)
        print(f"\n✅ Saved results to {output_xlsx}")
        print(f"📊 Total rows: {len(df)} across {len(queries)} queries (top {top_n} each)")
    except Exception as e:
        print(f"❌ Failed to write Excel file: {e}")

    # Basic summary
    success_counts = df.groupby("Query")["Rank"].apply(lambda s: (s > 0).sum())
    print("\n📈 Summary (documents retrieved per query):")
    for q in queries:
        print(f"  {q[:45]:45} : {success_counts.get(q, 0)}")

    return df


if __name__ == "__main__":
    run_braze_search(QUERIES, top_n=10)
