#!/bin/bash

# Google Custom Search API Test Script
# This script tests if your Google API key and CSE ID are configured correctly

API_KEY="AIzaSyBhV6ZF0S_pqD9JRkUi7iKDEy6VP7_guY0"
CSE_ID="YOUR_CSE_ID_HERE"  # Replace with your Custom Search Engine ID
QUERY="test"

echo "=========================================="
echo "Google Custom Search API Test"
echo "=========================================="
echo ""
echo "API Key: ${API_KEY:0:20}...***"
echo "CSE ID: $CSE_ID"
echo "Query: $QUERY"
echo ""
echo "Testing API call..."
echo ""

# Make the API request
curl -X GET \
  "https://www.googleapis.com/customsearch/v1?key=${API_KEY}&q=${QUERY}&cx=${CSE_ID}" \
  -H "Content-Type: application/json" \
  -w "\n\nHTTP Status: %{http_code}\n" \
  2>/dev/null | python3 -m json.tool

echo ""
echo "=========================================="
echo "If you see 'kind': 'customsearch#search' above, the API is working!"
echo "If you see 403 error, the Custom Search JSON API is not enabled."
echo "=========================================="

