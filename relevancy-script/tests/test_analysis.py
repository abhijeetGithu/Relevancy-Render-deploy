import json


def test_parse_curl_to_template_extracts_url_and_body():
    from app.analysis import parse_curl_to_template

    curl = """
curl 'https://example.com/search' \
  -H 'content-type: application/json' \
  --data-raw '{"searchString":"","resultsPerPage":10}'
""".strip()

    tpl = parse_curl_to_template(curl)
    assert tpl.url == "https://example.com/search"
    assert tpl.json_body is not None
    assert "searchString" in tpl.json_body


def test_extract_hits_and_match():
    from app.analysis import extract_hits, is_match

    payload = {
        "result": {
            "hits": [
                {
                    "highlight": {
                        "TitleToDisplay": ["My <span class='highlight'>Doc</span>"],
                        "SummaryToDisplay": ["Hello <span class='highlight'>World</span>"],
                    },
                    "href": "https://example.com/doc",
                }
            ]
        }
    }

    hits = extract_hits(payload)
    assert hits[0]["rank"] == 1
    assert hits[0]["title"] == "My Doc"
    assert hits[0]["url"] == "https://example.com/doc"

    assert is_match("My Doc", "https://example.com/doc", hits[0]["title"], hits[0]["url"]) is True
    assert is_match("My Doc", "", hits[0]["title"], hits[0]["url"]) is True
    assert is_match("", "https://example.com/doc", hits[0]["title"], hits[0]["url"]) is True
    assert is_match("Other", "https://example.com/doc", hits[0]["title"], hits[0]["url"]) is False


def test_match_modes():
    from app.analysis import is_match

    title = "My Doc"
    url = "https://example.com/doc"

    # title_and_url mode equivalent
    assert is_match(title, url, title, url) is True
    assert is_match(title, url, title, "https://example.com/other") is False

    # title_only equivalent
    assert is_match(title, "", title, url) is True

    # url_only equivalent
    assert is_match("", url, title, url) is True
