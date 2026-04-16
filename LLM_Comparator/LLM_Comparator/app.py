import csv
import io
import json
import os
import zipfile
import base64
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.fetchers import fetch_results
from services.google_fetcher import fetch_google_top10
from services.google_selenium import fetch_google_selenium, cleanup_browser
from services.llm_judge import DEFAULT_LLM_PROMPT, DEFAULT_OPENAI_MODEL, judge_results, generate_overall_analysis
from services.reporting import recall_at, write_csv
from services.rate_limiter import usage_tracker
from utils.parsing import find_rank, parse_ground_truth, parse_google_results_csv, parse_google_results_from_content, parse_dqe_sheet, validate_dqe_columns, extract_file_columns, parse_rs_sheet
from logging_config import setup_logging
import logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Custom log handler to store logs in memory
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=1000):
        super().__init__()
        self.capacity = capacity
        self.logs = []

    def emit(self, record):
        try:
            msg = self.format(record)
            self.logs.append(msg)
            if len(self.logs) > self.capacity:
                self.logs.pop(0)
        except Exception:
            self.handleError(record)

    def clear(self):
        self.logs = []

memory_handler = MemoryLogHandler()
memory_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Create a specific logger for user-facing progress logs
user_logger = logging.getLogger("user_progress")
user_logger.setLevel(logging.INFO)
user_logger.addHandler(memory_handler)
user_logger.propagate = False  # Prevent propagation to root logger to avoid duplication if root has other handlers

# We no longer attach memory_handler to root or uvicorn to avoid "actual logs" flooding
# logging.getLogger().addHandler(memory_handler)
# logging.getLogger("uvicorn").addHandler(memory_handler)
# logging.getLogger("uvicorn.access").addHandler(memory_handler)
# logging.getLogger("uvicorn.error").addHandler(memory_handler)

user_logger.info("Memory log handler attached. Live logs should appear here.")


def _google_csv_rows_for_results(query: str, results: List[dict]) -> List[dict]:
    """Build CSV rows for one query: Query text only on the first row; blank for continuation rows."""
    rows: List[dict] = []
    for j, r in enumerate(results):
        rows.append({
            "Query": query if j == 0 else "",
            "Rank": r.get("rank", ""),
            "Title": (r.get("title") or "").replace('"', '""'),
            "URL": r.get("url") or "",
        })
    return rows


app = FastAPI(title="Search Relevancy Testing Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

PORTAL_URL = os.getenv("PORTAL_URL", "/")
DATAQUERY_URL = os.getenv("DATAQUERY_URL", "/")

progress_state = {"current": 0, "total": 0, "status": "idle", "stage": ""}


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/config")
async def get_config():
    """Return service URLs for frontend cross-service navigation."""
    return {"portal_url": PORTAL_URL, "dataquery_url": DATAQUERY_URL}


@app.get("/api/health")
async def api_health():
    """Health check for DataQuery Engine and other callers. Confirms Web Scraper endpoint is available."""
    return {"status": "ok", "google_only_stream": True}


@app.get("/api/sample/csv")
async def download_sample_csv():
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Query", "Expected Title", "Expected URL"])
    writer.writerow(["reset password", "Password Reset Guide", "https://example.com/docs/password-reset-guide"])
    writer.writerow(["pricing", "Pricing Overview", "https://example.com/pricing/overview"])
    content = output.getvalue().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sample_ground_truth.csv"},
    )


@app.get("/api/sample/xlsx")
async def download_sample_xlsx():
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=400, detail="openpyxl is required for XLSX export.") from exc

    wb = Workbook()
    ws = wb.active
    ws.append(["Query", "Expected Title", "Expected URL"])
    ws.append(["reset password", "Password Reset Guide", "https://example.com/docs/password-reset-guide"])
    ws.append(["pricing", "Pricing Overview", "https://example.com/pricing/overview"])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sample_ground_truth.xlsx"},
    )


@app.post("/api/google-only")
async def google_only_run(request: Request):
    """
    Standalone Google search: accept a list of queries, run Google (Selenium) for each,
    return a CSV with columns Query, Rank, Title, URL for download
    (Query filled only on the first row per query; continuation rows leave Query blank).
    Used by DataQuery Engine "Google Search Result" flow.
    """
    try:
        body = await request.json()
        queries = body.get("queries") or []
        max_results = int(body.get("max_results", 10))
        if not queries:
            raise HTTPException(status_code=400, detail="queries list is required and must be non-empty")
        if max_results < 1 or max_results > 20:
            max_results = 10
        queries = [str(q).strip() for q in queries if str(q).strip()]
        if not queries:
            raise HTTPException(status_code=400, detail="No valid queries provided")
        site = (body.get("site") or body.get("selenium_site") or "").strip() or None
        user_logger.info(f"Google-only: running for {len(queries)} queries, max_results={max_results}" + (f" (site:{site})" if site else ""))
        all_rows = []
        for i, query in enumerate(queries):
            try:
                user_logger.info(f"Google-only: query {i+1}/{len(queries)}: {query[:50]}..." + (f" [site:{site}]" if site else ""))
                results = await fetch_google_selenium(query, site=site, result_count=max_results)
                all_rows.extend(_google_csv_rows_for_results(query, results))
            except Exception as e:
                logger.warning(f"Google-only failed for query '{query[:50]}': {e}")
                all_rows.append({"Query": query, "Rank": "", "Title": "[Error]", "URL": str(e)[:200]})
        try:
            cleanup_browser()
        except Exception:
            pass
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["Query", "Rank", "Title", "URL"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_rows)
        content = out.getvalue().encode("utf-8")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=google_search_results.csv"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Google-only run failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/google-only-stream")
async def google_only_stream(request: Request):
    """
    Streaming variant: same as google-only but yields NDJSON log lines, then a final line with csv_base64.
    Used by DQE to show live logs in the UI.
    """
    try:
        body = await request.json()
        queries = body.get("queries") or []
        max_results = int(body.get("max_results", 10))
        if not queries:
            raise HTTPException(status_code=400, detail="queries list is required and must be non-empty")
        if max_results < 1 or max_results > 20:
            max_results = 10
        queries = [str(q).strip() for q in queries if str(q).strip()]
        if not queries:
            raise HTTPException(status_code=400, detail="No valid queries provided")
        site = (body.get("site") or body.get("selenium_site") or "").strip() or None
        # Run the generator in a thread so we don't block the event loop; we need to call async fetch_google_selenium
        # from sync generator, so we use asyncio.run in the generator (per-query). Alternatively we could make
        # this endpoint use a full async generator - but fetch_google_selenium is async. So we make an async
        # generator that yields the same lines.
        import asyncio
        async def async_gen():
            try:
                site_msg = f" (site:{site})" if site else ""
                yield json.dumps({"type": "log", "message": f"Starting Google search for {len(queries)} queries (max {max_results} results each){site_msg}."}) + "\n"
                all_rows = []
                for i, query in enumerate(queries):
                    try:
                        yield json.dumps({"type": "log", "message": f"Processing query {i + 1}/{len(queries)}: {query[:60]}{'...' if len(query) > 60 else ''}"}) + "\n"
                        progress_queue: asyncio.Queue = asyncio.Queue()
                        loop = asyncio.get_running_loop()

                        def progress_callback(event):
                            try:
                                loop.call_soon_threadsafe(progress_queue.put_nowait, event)
                            except Exception:
                                pass

                        task = asyncio.create_task(
                            fetch_google_selenium(
                                query,
                                site=site,
                                result_count=max_results,
                                progress_callback=progress_callback,
                                query_index=i + 1,
                                total_queries=len(queries),
                            )
                        )

                        while not task.done():
                            try:
                                event = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                                payload = {"type": "progress"}
                                payload.update(event)
                                yield json.dumps(payload) + "\n"
                            except asyncio.TimeoutError:
                                continue

                        results = await task

                        while not progress_queue.empty():
                            event = progress_queue.get_nowait()
                            payload = {"type": "progress"}
                            payload.update(event)
                            yield json.dumps(payload) + "\n"

                        all_rows.extend(_google_csv_rows_for_results(query, results))
                        yield json.dumps({"type": "log", "message": f"  → Got {len(results)} results"}) + "\n"
                    except Exception as e:
                        logger.warning(f"Google-only failed for query '{query[:50]}': {e}")
                        all_rows.append({"Query": query, "Rank": "", "Title": "[Error]", "URL": str(e)[:200]})
                        yield json.dumps({"type": "log", "message": f"  → Error: {str(e)[:100]}"}) + "\n"
                try:
                    cleanup_browser()
                except Exception:
                    pass
                import base64
                out = io.StringIO()
                writer = csv.DictWriter(out, fieldnames=["Query", "Rank", "Title", "URL"], lineterminator="\n")
                writer.writeheader()
                writer.writerows(all_rows)
                content = out.getvalue().encode("utf-8")
                b64 = base64.b64encode(content).decode("ascii")
                yield json.dumps({"type": "done", "csv_base64": b64}) + "\n"
            except Exception as e:
                logger.exception("Google-only stream failed")
                yield json.dumps({"type": "log", "message": f"Fatal error: {str(e)}"}) + "\n"
                yield json.dumps({"type": "error", "message": str(e)}) + "\n"

        return StreamingResponse(
            async_gen(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Google-only stream failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/google-only-api")
async def google_only_api(request: Request):
    """
    Google Custom Search API only: accept queries + api_key + cse_id,
    run fetch_google_top10 for each query, return CSV.
    Used by DataQuery Engine when user chooses "Google Search API".
    """
    try:
        body = await request.json()
        queries = body.get("queries") or []
        max_results = int(body.get("max_results", 10))
        api_key = (body.get("api_key") or "").strip()
        cse_id = (body.get("cse_id") or "").strip()
        if not queries:
            raise HTTPException(status_code=400, detail="queries list is required and must be non-empty")
        if not api_key or not cse_id:
            raise HTTPException(status_code=400, detail="api_key and cse_id are required for Google Search API")
        if max_results < 1 or max_results > 20:
            max_results = 10
        queries = [str(q).strip() for q in queries if str(q).strip()]
        if not queries:
            raise HTTPException(status_code=400, detail="No valid queries provided")
        user_logger.info(f"Google-only-API: running for {len(queries)} queries")
        all_rows = []
        sites = body.get("sites") or []
        for i, query in enumerate(queries):
            try:
                results = await fetch_google_top10(
                    query, api_key, cse_id, sites, result_count=max_results
                )
                all_rows.extend(_google_csv_rows_for_results(query, results))
            except Exception as e:
                logger.warning(f"Google API failed for query '{query[:50]}': {e}")
                all_rows.append({"Query": query, "Rank": "", "Title": "[Error]", "URL": str(e)[:200]})
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["Query", "Rank", "Title", "URL"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_rows)
        content = out.getvalue().encode("utf-8")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=google_search_results.csv"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Google-only-API run failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/progress")
async def get_progress():
    return progress_state


@app.get("/api/usage-status")
async def get_usage_status():
    """Get current API usage status including rate limits and cooldowns"""
    try:
        status = usage_tracker.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting usage status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get usage status: {str(e)}")

@app.get("/api/logs")
async def get_logs(limit: int = 50):
    return {"logs": memory_handler.logs[-limit:]}


@app.delete("/api/logs")
async def clear_logs():
    memory_handler.clear()
    return {"status": "cleared"}


@app.post("/api/preview-upload")
async def preview_upload(
    file: UploadFile = File(...),
    has_ground_truth: str = Form(...),
    mode: str = Form("normal"),
):
    try:
        content = await file.read()

        if mode == "dqe":
            result = extract_file_columns(file.filename or "", content)
            return {
                "valid": True,
                "columns": result["columns"],
                "total_rows": result["total_rows"],
                "preview": result["preview"],
                "message": f"File parsed with {result['total_rows']} rows."
            }

        if mode == "rs":
            result = extract_file_columns(file.filename or "", content)
            return {
                "valid": True,
                "columns": result["columns"],
                "total_rows": result["total_rows"],
                "preview": result["preview"],
                "message": f"File parsed with {result['total_rows']} rows."
            }

        queries_only = (has_ground_truth == "no")
        parsed = parse_ground_truth(file.filename or "", content, queries_only=queries_only)
        
        return {
            "valid": True,
            "total_rows": len(parsed),
            "preview": parsed[:5],
            "message": "File is valid."
        }
    except ValueError as e:
        return {
            "valid": False,
            "error": str(e)
        }
    except Exception as e:
        logger.error(f"Preview error: {e}")
        return {
            "valid": False,
            "error": f"Failed to parse file: {str(e)}"
        }


@app.post("/api/su-search")
async def searchunify_search(request: Request):
    payload = await request.json()
    query = str(payload.get("query", "")).strip()
    curl_cmd = str(payload.get("curl", "")).strip()
    title_path = str(payload.get("title_path", "")).strip()
    url_path = str(payload.get("url_path", "")).strip()
    max_results = int(payload.get("max_results", 10))

    if not query:
        logger.warning("Query missing in SU search request")
        raise HTTPException(status_code=400, detail="Query is required.")
    if not curl_cmd:
        logger.warning("cURL missing in SU search request")
        raise HTTPException(status_code=400, detail="SearchUnify cURL is required.")
    if not title_path:
        title_path = "$.result.hits[*].highlight.TitleToDisplayString"
    if not url_path:
        url_path = "$.result.hits[*].href"

    try:
        results = await fetch_results(query, curl_cmd, title_path, url_path, max_results)
    except Exception as exc:
        logger.error(f"Error fetching SU results: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"query": query, "results": results}


@app.post("/api/run-option3")
async def run_option3(
    file: UploadFile = File(...),
    config_json: str = Form(...),
):
    try:
        config = json.loads(config_json)
    except Exception as exc:
        logger.error(f"Invalid config JSON: {exc}")
        user_logger.error(f"Invalid config JSON: {exc}")
        raise HTTPException(status_code=400, detail=f"Invalid config JSON: {exc}")

    has_ground_truth = config.get("has_ground_truth", True)
    run_google_benchmark = config.get("run_google_benchmark", True)
    bypass_google_api = config.get("google", {}).get("bypass_api", False)
    run_mode = config.get("mode", "normal")

    try:
        content = await file.read()

        dqe_entries = None
        if run_mode == "dqe":
            column_mapping = config.get("column_mapping", None)
            if column_mapping:
                dqe_entries = parse_rs_sheet(file.filename or "", content, column_mapping, has_ground_truth=True)
            else:
                dqe_entries = parse_dqe_sheet(file.filename or "", content)
            ground_truth = [
                {"query": e["query"], "expected_title": e["expected_title"], "expected_url": e["expected_url"]}
                for e in dqe_entries
            ]
            has_ground_truth = True
            user_logger.info(f"DQE mode: parsed {len(dqe_entries)} queries with SU results from sheet")
        elif run_mode == "rs":
            column_mapping = config.get("column_mapping", {})
            rs_entries = parse_rs_sheet(file.filename or "", content, column_mapping, has_ground_truth)
            ground_truth = [
                {"query": e["query"], "expected_title": e["expected_title"], "expected_url": e["expected_url"]}
                for e in rs_entries
            ]
            dqe_entries = rs_entries
            user_logger.info(f"RS mode: parsed {len(rs_entries)} queries with SU results from sheet")
        else:
            ground_truth = parse_ground_truth(file.filename or "", content, queries_only=not has_ground_truth)
        
        num_queries = len(ground_truth)
        
        if run_google_benchmark and not bypass_google_api:
            can_run, reason = usage_tracker.can_run(num_queries)
            if not can_run:
                logger.warning(f"Rate limit check failed: {reason}")
                user_logger.warning(f"Rate limit check failed: {reason}")
                status_code = 400 if "exceeds maximum allowed per run" in reason else 429
                raise HTTPException(status_code=status_code, detail=reason)
        else:
            if num_queries > 100:
                reason = f"Query count ({num_queries}) exceeds maximum allowed per run (100). Please reduce your file to 100 queries or fewer."
                logger.warning(f"Per-run cap exceeded: {reason}")
                user_logger.warning(f"Per-run cap exceeded: {reason}")
                raise HTTPException(status_code=400, detail=reason)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error parsing file: {exc}")
        user_logger.error(f"Error parsing file: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))

    su_config = config.get("searchunify")
    google_config = config.get("google", {})
    llm_config = config.get("llm", {})

    if run_mode not in ("dqe", "rs"):
        if not su_config or not su_config.get("curl") or not su_config.get("title_path"):
            raise HTTPException(status_code=400, detail="SearchUnify config is required.")

    # Determine Google method: "api", "selenium", or "bypass"
    # Support backward compatibility: bypass_api: true -> method: "bypass"
    google_method = google_config.get("method", "api")
    if "bypass_api" in google_config and google_config["bypass_api"]:
        google_method = "bypass"
    
    # Load Google results from CSV if bypass method is selected
    google_results_map = {}
    if run_google_benchmark and google_method == "bypass":
        try:
            csv_path = "google_results.csv"
            google_results_map = parse_google_results_csv(csv_path)
            user_logger.info(f"Loaded Google results from CSV for {len(google_results_map)} queries.")
        except FileNotFoundError:
            logger.error(f"Google results CSV file not found at {csv_path}")
            user_logger.error(f"Google results CSV file not found at {csv_path}")
            raise HTTPException(status_code=400, detail=f"Google results CSV file not found: {csv_path}")
        except Exception as exc:
            logger.error(f"Error loading Google results CSV: {exc}")
            user_logger.error(f"Error loading Google results CSV: {exc}")
            raise HTTPException(status_code=400, detail=f"Failed to load Google results CSV: {str(exc)}")
    elif run_google_benchmark and google_method == "api":
        if not google_config.get("api_key") or not google_config.get("cse_id"):
            raise HTTPException(status_code=400, detail="Google API key and CSE ID are required for API method.")
    elif run_google_benchmark and google_method == "selenium":
        # No validation needed for Selenium - it doesn't require credentials
        user_logger.info("Using Selenium method for Google search (no credentials required)")

    max_results = int(su_config.get("max_results", 10))
    llm_enabled = bool(llm_config.get("enabled"))
    llm_provider = llm_config.get("provider", "openai")

    detailed_rows: List[List[str]] = []
    recall_totals = {"SearchUnify": {"r5": 0, "r10": 0}}
    if run_google_benchmark:
        recall_totals["Google"] = {"r5": 0, "r10": 0}

    llm_scores = {"su": []}
    if run_google_benchmark:
        llm_scores["google"] = []
        
    summary_judgments: List[str] = []

    progress_state.update({"current": 0, "total": len(ground_truth), "status": "running", "stage": "Fetching data"})

    # Build a lookup for DQE mode SU results
    dqe_su_map: Dict[str, List[Dict]] = {}
    if dqe_entries:
        for e in dqe_entries:
            dqe_su_map[e["query"]] = e["su_results"]

    try:
        for idx, entry in enumerate(ground_truth, start=1):
            progress_state.update({"current": idx, "stage": "Fetching SearchUnify results"})
            user_logger.info(f"Processing query {idx}/{len(ground_truth)}: {entry['query']}")
            query = entry["query"]
            expected_title = entry["expected_title"]
            expected_url = entry["expected_url"]

            if run_mode in ("dqe", "rs"):
                su_results = dqe_su_map.get(query, [])
                user_logger.info(f"{run_mode.upper()} mode: loaded {len(su_results)} SU results from sheet for: {query}")
            else:
                su_results = await fetch_results(
                    query,
                    su_config["curl"],
                    su_config["title_path"],
                    su_config.get("url_path", ""),
                    max_results,
                )
            
            su_rank = -1
            su_r5 = "N/A"
            su_r10 = "N/A"
            
            if has_ground_truth:
                su_rank = find_rank(su_results, expected_title, expected_url)
                su_r5 = recall_at(su_rank, 5)
                su_r10 = recall_at(su_rank, 10)
                recall_totals["SearchUnify"]["r5"] += 1 if su_r5 == "Yes" else 0
                recall_totals["SearchUnify"]["r10"] += 1 if su_r10 == "Yes" else 0

            google_results = []
            google_rank = -1
            google_r5 = "N/A"
            google_r10 = "N/A"

            google_has_results = False

            if run_google_benchmark:
                if google_method == "bypass":
                    progress_state.update({"stage": "Loading Google results from CSV"})
                    user_logger.info(f"Loading Google results from CSV for: {query}")
                    google_results = google_results_map.get(query, [])
                elif google_method == "api":
                    progress_state.update({"stage": "Fetching Google results (API)"})
                    user_logger.info(f"Fetching Google results via API for: {query}")
                    google_results = await fetch_google_top10(
                        query,
                        google_config.get("api_key", ""),
                        google_config.get("cse_id", ""),
                        google_config.get("sites", []),
                        int(google_config.get("max_results", 10)),
                    )
                elif google_method == "selenium":
                    progress_state.update({"stage": "Scraping Google results (Selenium)"})
                    selenium_site = google_config.get("selenium_site", "")
                    user_logger.info(f"Scraping Google results via Selenium for: {query}" + (f" (site:{selenium_site})" if selenium_site else ""))
                    google_results = await fetch_google_selenium(
                        query,
                        site=selenium_site or None,
                        result_count=int(google_config.get("max_results", 10)),
                    )

                google_has_results = bool(google_results)
                if not google_has_results:
                    user_logger.warning(f"No Google results found for query: {query}")
                
                if has_ground_truth and google_has_results:
                    google_rank = find_rank(google_results, expected_title, expected_url)
                    google_r5 = recall_at(google_rank, 5)
                    google_r10 = recall_at(google_rank, 10)
                    recall_totals["Google"]["r5"] += 1 if google_r5 == "Yes" else 0
                    recall_totals["Google"]["r10"] += 1 if google_r10 == "Yes" else 0

            llm_judgment = ""
            llm_reason = ""
            su_llm_score = ""
            google_llm_score = ""
            
            if llm_enabled:
                progress_state.update({"stage": "Running LLM judgment"})
                user_logger.info(f"Running LLM judgment for: {query}")
                
                llm_top_n = 5
                su_results_for_llm = su_results[:llm_top_n]

                su_out = await judge_results(
                    query,
                    expected_title,
                    expected_url,
                    su_results_for_llm,
                    llm_config.get("api_key", ""),
                    llm_config.get("model", DEFAULT_OPENAI_MODEL),
                    llm_config.get("prompt", DEFAULT_LLM_PROMPT),
                    provider=llm_provider,
                )
                
                if su_out:
                    su_score = su_out.get("score")
                    if isinstance(su_score, (int, float)):
                        su_llm_score = str(int(round(su_score)))
                        llm_scores["su"].append(int(round(su_score)))
                    
                    summary_judgments.append(json.dumps({"source": "SearchUnify", "query": query, "judgment": su_out}))

                if run_google_benchmark:
                    if google_has_results:
                        google_list_for_llm = []
                        for i, g in enumerate(google_results[:llm_top_n]):
                            g_copy = g.copy()
                            g_copy["rank"] = i + 1
                            google_list_for_llm.append(g_copy)

                        google_out = await judge_results(
                            query,
                            expected_title,
                            expected_url,
                            google_list_for_llm,
                            llm_config.get("api_key", ""),
                            llm_config.get("model", DEFAULT_OPENAI_MODEL),
                            llm_config.get("prompt", DEFAULT_LLM_PROMPT),
                            provider=llm_provider,
                        )

                        if google_out:
                            google_score = google_out.get("score")
                            if isinstance(google_score, (int, float)):
                                google_llm_score = str(int(round(google_score)))
                                llm_scores["google"].append(int(round(google_score)))
                            
                            summary_judgments.append(json.dumps({"source": "Google", "query": query, "judgment": google_out}))
                    else:
                        google_llm_score = "Not Found"
                        user_logger.info(f"Skipping Google LLM judgment for '{query}' — no Google results")

            progress_state.update({"stage": "Calculating recall"})
            
            # Create 10 rows per query (one per result)
            max_results_to_show = 10
            for result_idx in range(max_results_to_show):
                row = []
                
                # Only fill query/expected columns on the first row
                if result_idx == 0:
                    row.append(query)
                    if has_ground_truth:
                        row.extend([expected_title, expected_url])
                else:
                    row.append("")  # Empty query for subsequent rows
                    if has_ground_truth:
                        row.extend(["", ""])  # Empty expected title/url
                
                # Add SU result for this row
                su_result = su_results[result_idx] if result_idx < len(su_results) else {}
                row.extend([
                    su_result.get("title", ""),
                    su_result.get("url", ""),
                ])
                
                # Add SU rank only on first row
                if result_idx == 0 and has_ground_truth:
                    row.append(str(su_rank))
                elif has_ground_truth:
                    row.append("")
                
                # Add Google results if benchmark is enabled
                if run_google_benchmark:
                    if google_has_results:
                        google_result = google_results[result_idx] if result_idx < len(google_results) else {}
                        row.extend([
                            google_result.get("title", ""),
                            google_result.get("url", ""),
                        ])
                        if result_idx == 0 and has_ground_truth:
                            row.append(str(google_rank))
                        elif has_ground_truth:
                            row.append("")
                    else:
                        row.extend([
                            "Not Found" if result_idx == 0 else "",
                            "Not Found" if result_idx == 0 else "",
                        ])
                        if has_ground_truth:
                            row.append("Not Found" if result_idx == 0 else "")
                
                # Add LLM scores only on first row
                if result_idx == 0:
                    row.append(su_llm_score)
                    if run_google_benchmark:
                        row.append(google_llm_score if google_llm_score else ("Not Found" if not google_has_results else ""))
                else:
                    row.append("")
                    if run_google_benchmark:
                        row.append("")
                
                detailed_rows.append(row)
    except Exception as exc:
        logger.error(f"Error during processing: {exc}", exc_info=True)
        user_logger.error(f"Error during processing: {exc}")
        progress_state.update({"status": "error", "stage": "Error"})
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    progress_state.update({"stage": "Generating overall analysis"})
    
    overall_analysis = ""
    if llm_enabled and summary_judgments:
        try:
            overall_analysis = await generate_overall_analysis(
                summary_judgments,
                llm_config.get("api_key", ""),
                llm_config.get("model", DEFAULT_OPENAI_MODEL),
                provider=llm_provider,
            )
        except Exception as e:
            logger.error(f"Failed to generate overall analysis: {e}")
            overall_analysis = "Analysis generation failed."

    progress_state.update({"stage": "Preparing reports"})
    
    # Build header based on configuration
    detailed_header = ["query"]
    
    if has_ground_truth:
        detailed_header.extend(["expected_title", "expected_url"])
    
    detailed_header.extend(["su_result_title", "su_result_url"])
    
    if has_ground_truth:
        detailed_header.append("su_rank")
    
    if run_google_benchmark:
        detailed_header.extend(["google_result_title", "google_result_url"])
        if has_ground_truth:
            detailed_header.append("google_rank")
    
    detailed_header.append("su_score")
    if run_google_benchmark:
        detailed_header.append("google_score")

    pivot_rows = []
    total = len(ground_truth)
    if has_ground_truth:
        for name, counts in recall_totals.items():
            pivot_rows.append(
                [
                    name,
                    total,
                    counts["r5"],
                    counts["r10"],
                    f"{counts['r5'] / total:.2%}",
                    f"{counts['r10'] / total:.2%}",
                ]
            )

    su_avg_recall = recall_totals["SearchUnify"]["r10"] / total if total and has_ground_truth else 0
    google_avg_recall = recall_totals.get("Google", {}).get("r10", 0) / total if total and has_ground_truth and run_google_benchmark else 0
    
    su_avg_llm = sum(llm_scores["su"]) / len(llm_scores["su"]) if llm_scores["su"] else 0
    google_avg_llm = sum(llm_scores.get("google", [])) / len(llm_scores.get("google", [])) if llm_scores.get("google") else 0

    def _better_label(su_val: float, google_val: float, label: str) -> str:
        if su_val > google_val:
            return f"SearchUnify performs better than Google on {label}."
        if google_val > su_val:
            return f"Google performs better than SearchUnify on {label}."
        return f"SearchUnify and Google are tied on {label}."

    performance_notes = []
    if has_ground_truth and run_google_benchmark:
        performance_notes.append(_better_label(su_avg_recall, google_avg_recall, "average recall@10"))
    
    if llm_scores["su"] and (not run_google_benchmark or llm_scores.get("google")):
        if run_google_benchmark:
            performance_notes.append(_better_label(su_avg_llm, google_avg_llm, "average LLM score"))
        else:
            performance_notes.append(f"Average SearchUnify LLM Score: {su_avg_llm:.2f}")

    if has_ground_truth and run_google_benchmark:
        if su_avg_recall < google_avg_recall or (su_avg_llm and su_avg_llm < google_avg_llm):
            performance_notes.append(
                "SearchUnify is underperforming. Likely gaps include ranking signals, query understanding, "
                "content coverage/index freshness, synonym/typo handling, and metadata quality."
            )
            performance_notes.append(
                "Recommended improvements for SearchUnify: tune boosting rules, add click feedback signals, "
                "expand content sources, improve title/URL extraction, and optimize analyzers for intent matching."
            )
            performance_notes.append(
                "Google is likely benefiting from broader coverage and stronger general relevance signals."
            )
        else:
            performance_notes.append(
                "SearchUnify shows strong results, likely due to tighter domain focus and relevance tuning."
            )
            performance_notes.append(
                "Google may be less precise for domain-specific intent; ensure site scoping and query framing are optimized."
            )

    summary_lines = [
        f"Total queries: {total}",
    ]
    
    if has_ground_truth:
        summary_lines.extend([
            "Average recall@10:",
            f"- SearchUnify: {su_avg_recall:.2%}",
        ])
        if run_google_benchmark:
             summary_lines.append(f"- Google: {google_avg_recall:.2%}")
        summary_lines.append("")

    summary_lines.append("Average LLM score:")
    summary_lines.append(f"- SearchUnify: {su_avg_llm:.2f}" if llm_scores["su"] else "- SearchUnify: N/A")
    if run_google_benchmark:
        summary_lines.append(f"- Google: {google_avg_llm:.2f}" if llm_scores.get("google") else "- Google: N/A")
    summary_lines.append("")
    
    summary_lines.append("Performance insights:")
    summary_lines.extend([f"- {note}" for note in performance_notes])
    
    if has_ground_truth:
        summary_lines.extend(["", "Recall@5 / Recall@10:"])
        for row in pivot_rows:
            summary_lines.append(f"- {row[0]}: {row[4]} / {row[5]}")
            
    if summary_judgments:
        summary_lines.append(f"LLM judgments captured: {len(summary_judgments)}")
        
    # Create a full report list for the text file, keeping summary_lines clean for JSON
    report_lines = list(summary_lines)
    
    if overall_analysis:
        report_lines.append("")
        report_lines.append("=" * 40)
        report_lines.append("OVERALL LLM ANALYSIS")
        report_lines.append("=" * 40)
        report_lines.append(overall_analysis)

    memory_zip = io.BytesIO()
    with zipfile.ZipFile(memory_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        path, detailed_bytes = write_csv("detailed_results.csv", detailed_header, detailed_rows)
        zf.writestr(path, detailed_bytes)
        
        if has_ground_truth:
            path, pivot_bytes = write_csv(
                "pivot_report.csv",
                ["source", "total_queries", "recall@5_count", "recall@10_count", "recall@5_rate", "recall@10_rate"],
                pivot_rows,
            )
            zf.writestr(path, pivot_bytes)
            
        zf.writestr("summary_report.txt", "\n".join(report_lines))

    progress_state.update({"status": "completed", "stage": "Done"})
    user_logger.info("Analysis completed successfully.")
    
    # Cleanup Selenium browser if it was used
    if run_google_benchmark and google_method == "selenium":
        try:
            cleanup_browser()
            logger.info("Cleaned up Selenium browser")
        except Exception as e:
            logger.warning(f"Error cleaning up browser: {e}")
    
    # Record usage for rate limiting (only if Google benchmark was used with API or Selenium)
    if run_google_benchmark and google_method in ["api", "selenium"]:
        try:
            usage_tracker.record_queries(len(ground_truth))
            usage_tracker.record_run()
            logger.info(f"Recorded usage: {len(ground_truth)} queries, 1 run")
        except Exception as e:
            logger.error(f"Failed to record usage: {e}")
            # Don't fail the request if usage recording fails
    
    memory_zip.seek(0)
    zip_content = base64.b64encode(memory_zip.getvalue()).decode("utf-8")
    
    filename = f"relevancy_reports_{datetime.now().strftime('%Y-%m-%d')}.zip"
    
    return JSONResponse({
        "status": "success",
        "filename": filename,
        "zip_content": zip_content,
        "summary": summary_lines,
        "overall_analysis": overall_analysis,
        "preview": {
            "header": detailed_header,
            "rows": detailed_rows[:50] 
        }
    })
