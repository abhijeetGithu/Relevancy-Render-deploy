#!/usr/bin/env python3
"""
Debug script to understand available content sources and categories
"""

import json
import requests


def simple_api_request(url, headers, body):
    """Make a simple API request without field extraction"""
    try:
        response = requests.post(url, headers=headers, json=body)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Error response: {response.text[:200]}")
            return None
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return None


def analyze_available_sources():
    """Analyze what content sources are actually available"""
    
    print("🔍 Analyzing available content sources...")
    
    # Load configuration
    with open('curl_input.json', 'r') as f:
        curl_data = json.load(f)
    
    # Base parameters
    base_body = curl_data["body"].copy()
    base_body["from"] = 0
    base_body["pageNo"] = 1
    base_body["resultsPerPage"] = 10
    base_body["aggregations"] = []
    
    # Get a normal response to see what documents look like
    response = simple_api_request(curl_data["url"], curl_data["headers"], base_body)
    
    if not response:
        print("❌ Failed to get response")
        return
    
    hits = response.get("result", {}).get("hits", [])
    print(f"📊 Total documents available: {response.get('result', {}).get('total', 0)}")
    print(f"📊 Sample hits: {len(hits)}")
    
    # Analyze metadata and highlights to understand content structure
    content_sources = set()
    metadata_keys = set()
    highlight_keys = set()
    
    for i, hit in enumerate(hits):
        print(f"\n--- Document {i+1} ---")
        print(f"🆔 ID: {hit.get('_id', 'N/A')}")
        
        # Check metadata
        metadata = hit.get("metadata", [])
        for meta in metadata:
            key = meta.get("key", "")
            value = meta.get("value", "")
            metadata_keys.add(key)
            print(f"📋 {key}: {value}")
            
            # Look for source-related fields
            if any(term in key.lower() for term in ["source", "content", "type", "category"]):
                content_sources.add(f"{key}={value}")
        
        # Check highlights
        highlight = hit.get("highlight", {})
        for key, values in highlight.items():
            highlight_keys.add(key)
            if any(term in key.lower() for term in ["source", "content", "type", "category"]):
                print(f"🎯 {key}: {values}")
    
    print(f"\n📋 All metadata keys found: {sorted(metadata_keys)}")
    print(f"🎯 All highlight keys found: {sorted(highlight_keys)}")
    print(f"🏷️ Content sources identified: {sorted(content_sources)}")
    
    # Now let's check aggregations to see available filters
    print(f"\n🔍 Checking available aggregations...")
    agg_response = response.get("aggregationsArray", [])
    
    for i, agg in enumerate(agg_response[:5]):  # Check first 5 aggregation types
        agg_type = agg.get("type", f"Unknown_{i}")
        agg_values = agg.get("aggregations", [])
        print(f"\n📊 Aggregation Type: {agg_type}")
        print(f"   Values count: {len(agg_values)}")
        
        # Show first few values
        for val in agg_values[:8]:
            key = val.get("key", "N/A")
            count = val.get("doc_count", 0)
            print(f"   - {key}: {count} documents")
    
    # Test some promising content filters
    print(f"\n🔍 Testing promising content filters...")
    
    # Try filtering by Support Home Category since we saw that in metadata
    support_categories = ["Billing & Account", "Device", "Plan", "Network"]
    
    for category in support_categories:
        print(f"\n🔧 Testing Support Home Category: {category}")
        test_body = base_body.copy()
        test_body["aggregations"] = [{"type": "Support Home Category", "filter": [category]}]
        
        response = simple_api_request(curl_data["url"], curl_data["headers"], test_body)
        if response:
            hits = response.get("result", {}).get("hits", [])
            print(f"📊 Results: {len(hits)} hits")
            if hits:
                print(f"✅ Found documents with Support Home Category={category}")
        else:
            print(f"❌ Failed to test category={category}")


if __name__ == "__main__":
    analyze_available_sources()
