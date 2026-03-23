import requests

API_KEY = "AIzaSyBhV6ZF0S_pqD9JRkUi7iKDEy6VP7_guY0"     # regenerate later
CX = "63e1314d190d34d25"
DOMAIN = "learn.microsoft.com"

def google_search(query, num=10):
    url = "https://customsearch.googleapis.com/customsearch/v1"
    params = { 
        "key": API_KEY,
        "cx": CX,
        "q": query,
        "siteSearch": "learn.microsoft.com",
        "siteSearchFilter": "i",
        "num": num
    }

    r = requests.get(url, params=params)

    if r.status_code != 200:
        print("STATUS:", r.status_code)
        print("ERROR BODY:")
        print(r.text)   # ← THIS is what we need
        raise Exception("Google API call failed")

    return r.json().get("items", [])


def run_from_file(file_path):
    with open(file_path) as f:
        queries = [line.strip() for line in f if line.strip()]

    for q in queries[:2]:
        print(f"\n🔍 Query: {q}")
        results = google_search(q)

        for i, item in enumerate(results, 1):
            print(f"{i}. {item['title']}")
            print(f"   {item['link']}")

if __name__ == "__main__":
    run_from_file("queries.txt")