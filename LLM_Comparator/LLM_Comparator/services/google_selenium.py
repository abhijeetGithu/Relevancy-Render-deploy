"""
Google Search Scraper using Selenium with Anti-Bot Measures

This module provides a Selenium-based alternative to the Google Custom Search API.
It uses undetected-chromedriver to bypass bot detection and implements various
anti-bot measures for reliable scraping on servers.
"""

import asyncio
import logging
import os
import random
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# Browser instance management (reused across queries in a run)
_browser_instance = None
_browser_lock = asyncio.Lock()
_query_count = 0

# User-Agent pool for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

# Viewport sizes for randomization
VIEWPORTS = [
    (1920, 1080),
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (1280, 720),
]


def _emit_progress(
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    stage: str,
    message: str,
    level: str = "INFO",
    **extra: Any,
) -> None:
    """Emit a structured progress event when a callback is available."""
    if not progress_callback:
        return
    payload: Dict[str, Any] = {
        "stage": stage,
        "message": message,
        "level": level,
    }
    payload.update(extra)
    try:
        progress_callback(payload)
    except Exception as e:
        logger.debug(f"Progress callback failed: {e}")


def _is_invalid_session_error(exc: Exception) -> bool:
    """Return True when an exception indicates a dead WebDriver session."""
    message = str(exc).lower()
    markers = [
        "invalid session id",
        "no such window",
        "target window already closed",
        "session deleted",
        "disconnected",
        "chrome not reachable",
        "web view not found",
    ]
    return any(marker in message for marker in markers)


def _is_browser_session_alive(driver) -> bool:
    """Cheap health check to confirm the driver session is still usable."""
    try:
        session_id = getattr(driver, "session_id", None)
        if not session_id:
            return False
        _ = driver.current_url
        return True
    except Exception as e:
        if not _is_invalid_session_error(e):
            logger.warning(f"Browser health check failed unexpectedly: {e}")
        return False


def _get_browser(progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
    """Get or create a browser instance with anti-bot measures"""
    global _browser_instance
    
    if _browser_instance is not None:
        if _is_browser_session_alive(_browser_instance):
            return _browser_instance
        logger.warning("Existing Selenium session is invalid; recreating browser instance")
        _emit_progress(
            progress_callback,
            "session_invalid",
            "Existing browser session is invalid; recreating Selenium driver",
            level="WARNING",
        )
        _close_browser()
    
    try:
        import undetected_chromedriver as uc
        
        user_agent = random.choice(USER_AGENTS)
        width, height = random.choice(VIEWPORTS)
        
        options = uc.ChromeOptions()
        
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(f"--user-agent={user_agent}")
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument("--disable-web-security")
        options.add_argument("--disable-features=IsolateOrigins,site-per-process")
        options.add_argument("--disable-gpu")

        # Docker/production: set CHROME_BIN=/usr/bin/chromium (see LLM Comparator Dockerfile)
        chrome_bin = (os.environ.get("CHROME_BIN") or os.environ.get("GOOGLE_CHROME_BIN") or "").strip()
        if chrome_bin:
            options.binary_location = chrome_bin
        
        # Detect installed Chrome/Chromium major version to match undetected-chromedriver
        chrome_version = None
        try:
            for binary in ("google-chrome", "chromium", "chromium-browser"):
                try:
                    result = subprocess.run(
                        [binary, "--version"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode != 0:
                        continue
                    version_str = result.stdout.strip().split()[-1]
                    chrome_version = int(version_str.split(".")[0])
                    logger.info(f"Detected browser version ({binary}): {chrome_version}")
                    break
                except (FileNotFoundError, subprocess.SubprocessError, ValueError, IndexError):
                    continue
        except Exception as ve:
            logger.warning(f"Could not detect Chrome/Chromium version, letting driver auto-detect: {ve}")
        
        kwargs = {"options": options, "use_subprocess": True}
        if chrome_version:
            kwargs["version_main"] = chrome_version
        
        driver = uc.Chrome(**kwargs)
        
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Visit Google homepage first to establish a normal session and handle consent
        _warm_up_browser(driver, progress_callback=progress_callback)
        
        _browser_instance = driver
        logger.info(f"Created browser instance with User-Agent: {user_agent[:50]}...")
        
        return _browser_instance
        
    except Exception as e:
        logger.error(f"Failed to create browser instance: {e}")
        raise


def _warm_up_browser(driver, progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
    """Visit Google homepage to establish cookies/consent before searching."""
    try:
        _emit_progress(progress_callback, "browser_warmup", "Opening Google homepage for browser warm-up")
        driver.get("https://www.google.com/")
        time.sleep(random.uniform(2, 3))
        
        # Handle cookie consent dialog (common in many regions)
        consent_selectors = [
            "button#L2AGLb",           # "I agree" button
            "button[aria-label='Accept all']",
            "button[id='L2AGLb']",
            "form[action='https://consent.google.com/save'] button",
        ]
        for sel in consent_selectors:
            try:
                btns = driver.find_elements("css selector", sel)
                if btns:
                    btns[0].click()
                    logger.info("Accepted Google cookie consent")
                    _emit_progress(progress_callback, "consent", "Accepted Google cookie consent prompt")
                    time.sleep(random.uniform(1, 2))
                    break
            except Exception:
                continue
        
        # Simulate a small human pause on homepage
        driver.execute_script("window.scrollTo(0, 100);")
        time.sleep(random.uniform(0.5, 1))
        driver.execute_script("window.scrollTo(0, 0);")
        
    except Exception as e:
        logger.warning(f"Browser warm-up had an issue (non-fatal): {e}")
        _emit_progress(progress_callback, "browser_warmup", f"Warm-up warning: {e}", level="WARNING")


def _close_browser():
    """Close the browser instance"""
    global _browser_instance, _query_count
    
    if _browser_instance is not None:
        try:
            _browser_instance.quit()
            logger.info("Closed browser instance")
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            _browser_instance = None
            _query_count = 0


def _check_for_captcha(driver) -> bool:
    """Check if Google is showing a CAPTCHA"""
    try:
        # Check for common CAPTCHA indicators
        captcha_selectors = [
            "//div[@id='captcha-form']",
            "//form[@id='captcha-form']",
            "//*[contains(text(), 'unusual traffic')]",
            "//*[contains(text(), 'not a robot')]",
            "//iframe[@title='reCAPTCHA']",
        ]
        
        for selector in captcha_selectors:
            elements = driver.find_elements("xpath", selector)
            if elements:
                return True
        
        return False
    except Exception as e:
        logger.warning(f"Error checking for CAPTCHA: {e}")
        return False


def _build_search_query(query: str, site: Optional[str] = None) -> str:
    """
    Build the final search query with site restriction appended.
    
    Example: "what is content source" + "docs.searchunify.com"
         ->  "what is content source site:docs.searchunify.com"
    """
    search_query = query.strip()
    if site:
        clean_site = site.strip().lstrip("site:").strip()
        if clean_site:
            search_query = f"{search_query} site:{clean_site}"
    return search_query


def _scrape_google_results(
    driver,
    query: str,
    site: Optional[str],
    result_count: int,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    query_index: Optional[int] = None,
    total_queries: Optional[int] = None,
) -> List[Dict]:
    """
    Scrape Google search results for a query.
    
    Args:
        driver: Selenium WebDriver instance
        query: Search query
        site: Single docs site domain to restrict search to (e.g. "docs.searchunify.com")
        result_count: Number of results to return
        
    Returns:
        List of result dicts with rank, title, url
    """
    results = []
    
    try:
        search_query = _build_search_query(query, site)
        
        encoded_query = quote_plus(search_query)
        url = f"https://www.google.com/search?q={encoded_query}&num={result_count}"
        
        logger.info(f"Scraping Google for: {search_query}")
        _emit_progress(
            progress_callback,
            "query_start",
            f"Scraping Google for query: {search_query}",
            query_index=query_index,
            total_queries=total_queries,
            query=query,
        )
        
        # Try up to 3 times with increasing backoff if CAPTCHA is hit
        page_loaded = False
        for attempt in range(3):
            _emit_progress(
                progress_callback,
                "attempt_start",
                f"Loading Google results page (attempt {attempt + 1}/3)",
                query_index=query_index,
                total_queries=total_queries,
                query=query,
                attempt=attempt + 1,
                max_attempts=3,
            )
            driver.get(url)
            time.sleep(random.uniform(2, 4))
            
            if not _check_for_captcha(driver):
                page_loaded = True
                _emit_progress(
                    progress_callback,
                    "serp_loaded",
                    f"Search results page loaded successfully on attempt {attempt + 1}",
                    query_index=query_index,
                    total_queries=total_queries,
                    query=query,
                    attempt=attempt + 1,
                    max_attempts=3,
                )
                break
            
            backoff = [10, 30, 60][attempt]
            logger.warning(f"CAPTCHA detected (attempt {attempt+1}/3) for query: {query}, waiting {backoff}s...")
            _emit_progress(
                progress_callback,
                "captcha_detected",
                f"CAPTCHA detected on attempt {attempt + 1}/3; backing off for {backoff}s",
                level="WARNING",
                query_index=query_index,
                total_queries=total_queries,
                query=query,
                attempt=attempt + 1,
                max_attempts=3,
                backoff_seconds=backoff,
            )
            # Navigate away to Google homepage before retrying
            driver.get("https://www.google.com/")
            time.sleep(backoff)
        
        if not page_loaded:
            logger.error(f"CAPTCHA persisted after 3 retries for query: {query}")
            _emit_progress(
                progress_callback,
                "captcha_failed",
                "CAPTCHA persisted after 3 retries; returning no results",
                level="ERROR",
                query_index=query_index,
                total_queries=total_queries,
                query=query,
            )
            return []
        
        # Human-like scroll behavior
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 3);")
        time.sleep(random.uniform(0.5, 1.5))
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(random.uniform(0.5, 1.0))
        
        # Strategy 1: Use div.g container-based extraction (most reliable)
        results = _extract_via_result_containers(driver, result_count)
        
        # Strategy 2: Fall back to h3-based extraction
        if not results:
            logger.info("Container strategy found no results, trying h3-based extraction")
            results = _extract_via_h3_elements(driver, result_count)
        
        if not results:
            logger.warning(f"No results extracted for query: {query}")
            _emit_progress(
                progress_callback,
                "no_results",
                "No Google results extracted from the current page",
                level="WARNING",
                query_index=query_index,
                total_queries=total_queries,
                query=query,
            )
            logger.debug(f"Page title: {driver.title}")
            try:
                with open("/tmp/google_page_source.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logger.info("Saved page source to /tmp/google_page_source.html for debugging")
            except Exception:
                pass
        
        logger.info(f"Scraped {len(results)} results for query: {query}")
        _emit_progress(
            progress_callback,
            "query_done",
            f"Scraped {len(results)} results",
            query_index=query_index,
            total_queries=total_queries,
            query=query,
            results_count=len(results),
        )
        
    except Exception as e:
        logger.error(f"Error scraping Google results: {e}", exc_info=True)
        _emit_progress(
            progress_callback,
            "query_error",
            f"Error scraping Google results: {e}",
            level="ERROR",
            query_index=query_index,
            total_queries=total_queries,
            query=query,
        )
    
    return results


def _extract_via_result_containers(driver, result_count: int) -> List[Dict]:
    """Extract results using div.g containers which wrap each search result."""
    results = []
    
    container_selectors = [
        "div.g",
        "div[data-sokoban-container]",
        "div.MjjYud div.g",
    ]
    
    containers = []
    for selector in container_selectors:
        try:
            containers = driver.find_elements("css selector", selector)
            if containers and len(containers) >= 2:
                logger.info(f"Found {len(containers)} result containers with: {selector}")
                break
        except Exception:
            continue
    
    for container in containers:
        if len(results) >= result_count:
            break
        try:
            anchor = None
            title = ""
            
            # Find the title link: an <a> containing an <h3>
            try:
                anchor = container.find_element("css selector", "a:has(h3)")
                h3 = anchor.find_element("css selector", "h3")
                title = h3.text.strip()
            except Exception:
                # Fallback: find h3 then walk up to anchor
                try:
                    h3 = container.find_element("css selector", "h3")
                    title = h3.text.strip()
                    parent = h3.find_element("xpath", "./ancestor::a")
                    anchor = parent
                except Exception:
                    continue
            
            if not title or not anchor:
                continue
            
            href = anchor.get_attribute("href") or ""
            if href and href.startswith("http") and "google.com" not in href:
                results.append({
                    "rank": len(results) + 1,
                    "title": title,
                    "url": href
                })
                logger.info(f"Result {len(results)}: {title[:60]} -> {href[:80]}")
        except Exception as e:
            logger.debug(f"Error processing container: {e}")
            continue
    
    return results


def _extract_via_h3_elements(driver, result_count: int) -> List[Dict]:
    """Fallback extraction using h3 elements directly."""
    results = []
    
    h3_selectors = [
        "h3.LC20lb",
        "div#search h3",
        "h3",
    ]
    
    h3_elements = []
    for selector in h3_selectors:
        try:
            h3_elements = driver.find_elements("css selector", selector)
            if h3_elements and len(h3_elements) >= 2:
                logger.info(f"Found {len(h3_elements)} h3 elements with: {selector}")
                break
        except Exception:
            continue
    
    for idx, h3 in enumerate(h3_elements):
        if len(results) >= result_count:
            break
        try:
            title = h3.text.strip()
            if not title:
                continue
            
            href = ""
            # Walk up the DOM to find the nearest ancestor <a>
            try:
                anchor = h3.find_element("xpath", "./ancestor::a")
                href = anchor.get_attribute("href") or ""
            except Exception:
                # Try parent, then grandparent
                try:
                    parent = h3.find_element("xpath", "..")
                    if parent.tag_name == "a":
                        href = parent.get_attribute("href") or ""
                    else:
                        grandparent = parent.find_element("xpath", "..")
                        if grandparent.tag_name == "a":
                            href = grandparent.get_attribute("href") or ""
                except Exception:
                    continue
            
            if href and href.startswith("http") and "google.com" not in href:
                results.append({
                    "rank": len(results) + 1,
                    "title": title,
                    "url": href
                })
                logger.info(f"Result {len(results)}: {title[:60]} -> {href[:80]}")
        except Exception as e:
            logger.debug(f"Error processing h3 element {idx}: {e}")
            continue
    
    return results


async def fetch_google_selenium(
    query: str,
    site: Optional[str] = None,
    result_count: int = 10,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    query_index: Optional[int] = None,
    total_queries: Optional[int] = None,
) -> List[Dict]:
    """
    Fetch Google search results using Selenium (async wrapper).
    
    Args:
        query: Search query string
        site: Single docs site domain to restrict search (e.g. "docs.searchunify.com")
        result_count: Number of results to fetch (default: 10)
        
    Returns:
        List of result dicts: [{"rank": 1, "title": "...", "url": "..."}, ...]
    """
    async with _browser_lock:
        results = await asyncio.to_thread(
            _scrape_google_results_sync,
            query,
            site,
            result_count,
            progress_callback,
            query_index,
            total_queries,
        )
        
        while len(results) < result_count:
            results.append({
                "rank": len(results) + 1,
                "title": "",
                "url": ""
            })
        
        return results


def _scrape_google_results_sync(
    query: str,
    site: Optional[str],
    result_count: int,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    query_index: Optional[int] = None,
    total_queries: Optional[int] = None,
) -> List[Dict]:
    """Synchronous wrapper for scraping (called from thread pool)"""
    global _query_count
    driver = _get_browser(progress_callback=progress_callback)
    
    # First query gets a shorter delay (browser just warmed up)
    # Subsequent queries get longer delays to avoid CAPTCHA
    if _query_count == 0:
        delay = random.uniform(2, 3)
    else:
        delay = random.uniform(5, 8)

    _emit_progress(
        progress_callback,
        "throttle_delay",
        f"Applying anti-bot delay of {delay:.1f}s before query",
        query_index=query_index,
        total_queries=total_queries,
        query=query,
        delay_seconds=round(delay, 1),
    )
    time.sleep(delay)
    
    _query_count += 1
    try:
        return _scrape_google_results(
            driver,
            query,
            site,
            result_count,
            progress_callback=progress_callback,
            query_index=query_index,
            total_queries=total_queries,
        )
    except Exception as e:
        if not _is_invalid_session_error(e):
            raise

        logger.warning(f"Invalid Selenium session detected; recreating driver and retrying once: {e}")
        _emit_progress(
            progress_callback,
            "session_recovery",
            "Selenium session became invalid; recreating browser and retrying query once",
            level="WARNING",
            query_index=query_index,
            total_queries=total_queries,
            query=query,
        )

        _close_browser()
        driver = _get_browser(progress_callback=progress_callback)

        recovery_delay = random.uniform(1.0, 2.0)
        time.sleep(recovery_delay)

        return _scrape_google_results(
            driver,
            query,
            site,
            result_count,
            progress_callback=progress_callback,
            query_index=query_index,
            total_queries=total_queries,
        )


def cleanup_browser():
    """Cleanup function to close browser after run completes"""
    _close_browser()
