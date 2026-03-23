# Selenium Google Search Implementation

## Overview

Successfully implemented a Selenium-based Google search scraper as an alternative to the paid Google Custom Search API. Users can now choose between three methods in Step 4:

1. **Google API (Fast, Paid)** - Uses Google Custom Search API
2. **Web Scraper (Free, Slow)** - Uses Selenium automation
3. **Use Saved Results** - Loads from CSV file

## Implementation Details

### Files Created

1. **`services/google_selenium.py`** - Core Selenium scraper module
   - Headless Chrome browser with undetected-chromedriver
   - Anti-bot measures (random User-Agents, viewports, delays)
   - CAPTCHA detection and retry logic
   - Async wrapper for FastAPI integration
   - Browser instance reuse across queries in a run

### Files Modified

1. **`app.py`** - Backend integration
   - Added `google_method` field support ("api", "selenium", "bypass")
   - Integrated Selenium scraper into `/api/run-option3` endpoint
   - Browser cleanup after run completion
   - Rate limiting applies to both API and Selenium methods

2. **`static/index.html`** - UI redesign
   - Three selectable method cards with icons and descriptions
   - Method-specific configuration sections
   - Info cards with usage notes for each method

3. **`static/app.js`** - Frontend logic
   - Card selection functionality
   - Method-based validation (API requires credentials, Selenium doesn't)
   - Updated form submission to include `method` field
   - Rate limit checks for both API and Selenium

4. **`static/styles.css`** - Card styling
   - Grid layout for method cards
   - Selected state with checkmark
   - Tag badges for speed/cost indicators
   - Responsive design (stacks on mobile)

5. **`requirements.txt`** - Dependencies
   - Added `selenium`
   - Added `undetected-chromedriver`
   - Added `python-dotenv`

## Anti-Bot Measures

The Selenium scraper implements multiple anti-detection techniques:

1. **undetected-chromedriver** - Patches ChromeDriver to avoid `navigator.webdriver` detection
2. **User-Agent Rotation** - Pool of 7 realistic Chrome/Firefox/Safari user agents
3. **Viewport Randomization** - Random window sizes (1920x1080, 1366x768, etc.)
4. **Human-like Delays** - 3-5 second random delays between queries
5. **Scroll Behavior** - Random scrolling before extracting results
6. **CAPTCHA Detection** - Detects CAPTCHAs, waits 30 seconds, retries once
7. **Session Management** - Single browser instance per run (not per query)
8. **Headless Mode** - Runs without display for server environments

## Usage

### In the UI

1. Navigate to Step 4 (Google Benchmark)
2. Click on the "Web Scraper" card
3. Optionally specify sites to restrict search
4. No API credentials needed
5. Run the analysis

### Rate Limiting

- Selenium queries count toward daily/monthly limits (same as API)
- Per-run limit: 100 queries max
- Cooldown period: 3 hours after 3 consecutive runs

## Known Limitations

1. **CAPTCHA Risk** - Google may show CAPTCHAs with many queries
   - Detection logic logs warnings and returns empty results
   - Retry once after 30-second wait
   
2. **Speed** - Slower than API (3-5 seconds per query vs 1-2 seconds)
   - Due to human-like delays and page rendering

3. **Reliability** - Google's HTML structure changes frequently
   - Multiple selector fallbacks implemented
   - May need updates if Google changes their layout

4. **Chrome Version** - Requires Chrome 145 installed
   - undetected-chromedriver auto-downloads matching ChromeDriver
   - Update `version_main` in code if Chrome version changes

## Testing

A test script is available at `test_selenium.py`:

```bash
python test_selenium.py
```

This will:
- Test a single query ("python programming")
- Display scraped results
- Clean up the browser instance

**Note**: The test may encounter CAPTCHAs in headless mode, which is expected behavior.

## Backward Compatibility

- Old `bypass_api: true/false` configs are automatically converted to `method: "bypass"` or `method: "api"`
- Existing API and CSV workflows remain unchanged
- All three methods produce identical result format for downstream processing

## Future Improvements

1. **Proxy Support** - Rotate IPs to reduce CAPTCHA risk
2. **Residential Proxies** - Use residential proxy services
3. **Session Cookies** - Maintain Google session cookies
4. **Rate Limiting** - Implement per-hour limits for Selenium
5. **Fallback** - Auto-switch to API if CAPTCHA persists

## Troubleshooting

### Chrome Version Mismatch

If you see "This version of ChromeDriver only supports Chrome version X":

1. Check your Chrome version: `google-chrome --version`
2. Update `version_main` in `services/google_selenium.py` line 79
3. Restart the server

### No Results Found

If scraper returns 0 results:

1. Check logs for CAPTCHA warnings
2. Try non-headless mode for debugging (remove `--headless=new`)
3. Update result selectors if Google changed their HTML

### Browser Won't Start

If browser fails to initialize:

1. Ensure Chrome is installed
2. Check permissions for headless Chrome
3. Try running test script directly: `python test_selenium.py`

## Conclusion

The Selenium implementation provides a free alternative to the Google Custom Search API while maintaining the same result format and workflow. It includes robust anti-bot measures and graceful CAPTCHA handling, making it suitable for moderate-volume testing and development use cases.
