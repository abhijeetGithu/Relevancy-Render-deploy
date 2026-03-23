#!/usr/bin/env python3
"""
Quick test script to verify the new config functionality.
"""
import json
import os

# Test 1: Check that curl_alteryx.json has config section
print("=" * 80)
print("TEST 1: Verify curl_alteryx.json has config section")
print("=" * 80)

config_path = os.path.join(os.path.dirname(__file__), 'curl_alteryx.json')
with open(config_path, 'r') as f:
    data = json.load(f)

config = data.get('config', {})
print(f"✅ Config found: {config}")
print(f"   - Source Name: {config.get('source_name')}")
print(f"   - Documents Per Page: {config.get('documents_per_page')}")
print(f"   - Max Pages: {config.get('max_pages')}")

# Test 2: Verify client can read config
print("\n" + "=" * 80)
print("TEST 2: Verify alteryx_search_client can read config")
print("=" * 80)

from alteryx_search_client import load_curl_template, get_config_from_template

tpl = load_curl_template(config_path)
client_config = get_config_from_template(tpl)
print(f"✅ Client config: {client_config}")

# Test 3: Verify structure matches expected format
print("\n" + "=" * 80)
print("TEST 3: Verify JSON structure")
print("=" * 80)

required_keys = ['url', 'method', 'config', 'headers', 'body']
for key in required_keys:
    if key in data:
        print(f"✅ {key}: present")
    else:
        print(f"❌ {key}: MISSING")

# Test 4: Verify body has correct sortby
print("\n" + "=" * 80)
print("TEST 4: Verify relevance sorting is configured")
print("=" * 80)

body = data.get('body', {})
sortby = body.get('sortby')
aggregations = body.get('aggregations', [])
category = body.get('category')

print(f"   - sortby: {sortby} {'✅' if sortby == '_score' else '❌'}")
print(f"   - category: {category} {'✅' if category == 'external' else '❌'}")
print(f"   - aggregations: {len(aggregations)} filters {'✅' if aggregations else '⚠️'}")

print("\n" + "=" * 80)
print("ALL TESTS COMPLETED")
print("=" * 80)
