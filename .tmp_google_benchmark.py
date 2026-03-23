import time
import json
import urllib.request

queries = [
    'Create a Base Hyperlink (HREF) on Search Result Pages site:docs.searchunify.com',
    'Use Azure DevOps as a Content Source site:docs.searchunify.com',
    'Agent Helper-Slack Integration: Solve Cases Faster with Case Swarming site:docs.searchunify.com',
    'Create an app in Okta IdP site:docs.searchunify.com',
    'Top Rated Graph Tiles site:docs.searchunify.com',
]

url = 'http://localhost:8001/api/google-only'
print('Running Google timing test against http://localhost:8001/api/google-only')
print(f"{'#':<2} | {'Query (with site filter)':<90} | {'Time(ms)':<9} | {'HTTP':<5} | {'Rows':<8}")
print('-' * 130)

total = 0
for i, q in enumerate(queries, start=1):
    payload = json.dumps({'queries': [q], 'max_results': 10}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, method='POST', headers={'Content-Type': 'application/json'})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = resp.read()
        code = resp.getcode()
    t1 = time.perf_counter()
    elapsed_ms = int((t1 - t0) * 1000)
    total += elapsed_ms
    rows = body.decode('utf-8', errors='replace').count('\n')
    short_q = q if len(q) <= 90 else q[:87] + '...'
    print(f"{i:<2} | {short_q:<90} | {elapsed_ms:<9} | {code:<5} | {rows:<8}")

print('-' * 130)
avg = int(total / len(queries))
print(f"TOTAL time: {total} ms ({total/1000:.2f} s)")
print(f"AVERAGE per query: {avg} ms ({avg/1000:.2f} s)")
