from __future__ import annotations

import asyncio
import csv
import io
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analysis import fetch_top_results, is_match, parse_curl_to_template
from app.community_client import fetch_community_results, parse_community_curl_to_template
from app.models import AnalysisMode, PageType, RunCreateRequest, RunCreateResponse, RunResultsResponse, RunStatusResponse
from app.match_mode import MatchMode

app = FastAPI(title="Relevancy System")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

PORTAL_URL = os.environ.get("PORTAL_URL", "/")
LLM_COMPARATOR_URL = os.environ.get("LLM_COMPARATOR_URL", "/")


class _RunState:
    def __init__(self, run_id: str, total: int, is_query_only: bool = False):
        self.run_id = run_id
        self.status: str = "queued"
        self.total_queries: int = total
        self.completed_queries: int = 0
        self.failed_queries: int = 0
        self.message: Optional[str] = None
        self.results: List[Dict[str, Any]] = []
        self.output_csv: Optional[str] = None
        self.output_excel: Optional[bytes] = None
        self.is_query_only: bool = is_query_only
        self.logs: List[Dict[str, Any]] = []  # Log entries with timestamp and message
    
    def add_log(self, message: str, level: str = "info"):
        """Add a log entry with timestamp."""
        import datetime
        self.logs.append({
            "time": datetime.datetime.now().isoformat(),
            "level": level,
            "message": message
        })


_RUNS: Dict[str, _RunState] = {}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "portal_url": PORTAL_URL,
        "llm_comparator_url": LLM_COMPARATOR_URL,
    })


@app.get("/api/config")
def get_config():
    """Return service URLs for frontend cross-service navigation."""
    return {"portal_url": PORTAL_URL, "llm_comparator_url": LLM_COMPARATOR_URL}


@app.get("/health")
def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "service": "relevancy-system",
        "version": "1.0.0"
    }


def _read_csv_rows(csv_text: str) -> List[Dict[str, str]]:
    # Remove BOM if present (common in Excel-exported CSVs)
    if csv_text.startswith('\ufeff'):
        csv_text = csv_text[1:]
    
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)
    if reader.fieldnames is None:
        raise ValueError("CSV is missing a header row")
    
    # Strip whitespace from header names to handle Excel formatting issues
    cleaned_fieldnames = [name.strip() if name else name for name in reader.fieldnames]
    reader.fieldnames = cleaned_fieldnames
    
    rows: List[Dict[str, str]] = []
    for row in reader:
        # Normalize None values and strip keys
        cleaned_row = {}
        for k, v in row.items():
            clean_key = k.strip() if k else k
            cleaned_row[clean_key] = (v or "").strip() if v else ""
        rows.append(cleaned_row)
    return rows


async def _run_analysis_job(
    state: _RunState,
    req: RunCreateRequest,
):
    state.status = "running"
    state.add_log("Analysis started", "info")
    try:
        # Determine if this is a community page or normal page
        is_community = req.pageType == PageType.community
        page_type_label = "Community" if is_community else "Normal"
        state.add_log(f"Page type: {page_type_label}", "info")
        
        # Parse template based on page type
        if is_community:
            # For community pages, parse curl to community template
            if not req.curl:
                raise ValueError("Community page requires a cURL command")
            state.add_log("Parsing community cURL template...", "info")
            community_template = parse_community_curl_to_template(req.curl)
            template = None  # Not used for community pages
            state.add_log(f"Target URL: {community_template.get('url', 'N/A')[:60]}...", "info")
        else:
            # Normal page: parse curl to template
            state.add_log("Parsing cURL template...", "info")
            template = parse_curl_to_template(req.curl)
            community_template = None
            state.add_log(f"Target URL: {template.url[:60]}...", "info")
        
        rows = _read_csv_rows(req.csvText)

        if not rows:
            raise ValueError("CSV has no data rows")

        state.add_log(f"Loaded {len(rows)} queries from file", "info")

        query_col = req.queryColumn.strip() if req.queryColumn else ""
        is_query_only = req.analysisMode == AnalysisMode.query_only
        mode_label = "Query Only" if is_query_only else "Match Analysis"
        state.add_log(f"Analysis mode: {mode_label}", "info")

        # Column validation
        header = set(rows[0].keys())
        state.add_log(f"CSV columns detected: {list(header)}", "info")
        
        if query_col not in header:
            # Try case-insensitive match as fallback
            query_col_lower = query_col.lower()
            matched_col = None
            for h in header:
                if h.lower() == query_col_lower:
                    matched_col = h
                    break
            if matched_col:
                query_col = matched_col
                state.add_log(f"Using matched column: '{query_col}'", "info")
            else:
                raise ValueError(f"Query column '{query_col}' not found in CSV headers. Available: {list(header)}")

        # For with_expected mode, validate title/url columns
        title_col = req.expectedTitleColumn.strip() if req.expectedTitleColumn else None
        url_col = req.expectedUrlColumn.strip() if req.expectedUrlColumn else None
        if not is_query_only:
            if title_col and title_col not in header:
                # Try case-insensitive match
                title_col_lower = title_col.lower()
                for h in header:
                    if h.lower() == title_col_lower:
                        title_col = h
                        break
                if title_col not in header:
                    raise ValueError(f"Expected Title column '{title_col}' not found in CSV headers. Available: {list(header)}")
            if url_col and url_col not in header:
                # Try case-insensitive match
                url_col_lower = url_col.lower()
                for h in header:
                    if h.lower() == url_col_lower:
                        url_col = h
                        break
                if url_col not in header:
                    raise ValueError(f"Expected URL column '{url_col}' not found in CSV headers. Available: {list(header)}")

        state.add_log("Starting query processing...", "info")

        async with httpx.AsyncClient() as client:
            for idx, row in enumerate(rows):
                query = (row.get(query_col) or "").strip()
                if not query:
                    state.failed_queries += 1
                    state.completed_queries += 1
                    state.add_log(f"[{idx+1}/{len(rows)}] Skipped empty query", "warn")
                    continue

                # Log query being processed (truncate long queries)
                query_preview = query[:50] + "..." if len(query) > 50 else query
                state.add_log(f"[{idx+1}/{len(rows)}] Searching: {query_preview}", "info")

                try:
                    # Use appropriate fetch function based on page type
                    if is_community:
                        hits = await fetch_community_results(client, community_template, query, top_n=req.topN)
                    else:
                        hits = await fetch_top_results(client, template, query, top_n=req.topN)
                    
                    state.add_log(f"[{idx+1}/{len(rows)}] Found {len(hits)} results", "success")
                except Exception as e:  # noqa: BLE001
                    state.failed_queries += 1
                    state.completed_queries += 1
                    state.add_log(f"[{idx+1}/{len(rows)}] Error: {str(e)[:100]}", "error")
                    if is_query_only:
                        state.results.append(
                            {
                                "query": query,
                                "error": str(e),
                                "hits": [],
                            }
                        )
                    else:
                        expected_title = (row.get(title_col) if title_col else "") or ""
                        expected_url = (row.get(url_col) if url_col else "") or ""
                        state.results.append(
                            {
                                "query": query,
                                "error": str(e),
                                "matched": False,
                                "matched_rank": None,
                                "expected_title": expected_title,
                                "expected_url": expected_url,
                                "hits": [],
                            }
                        )
                    continue

                if is_query_only:
                    # Query-only mode: just store all hits, no matching
                    state.results.append(
                        {
                            "query": query,
                            "hits": hits,
                        }
                    )
                else:
                    # With expected mode: perform matching
                    expected_title = (row.get(title_col) if title_col else "") or ""
                    expected_url = (row.get(url_col) if url_col else "") or ""

                    matched_rank = None
                    for h in hits:
                        if req.matchMode == MatchMode.title_and_url:
                            ok = is_match(expected_title, expected_url, h.get("title", ""), h.get("url", ""))
                        elif req.matchMode == MatchMode.title_only:
                            ok = is_match(expected_title, "", h.get("title", ""), h.get("url", ""))
                        elif req.matchMode == MatchMode.url_only:
                            ok = is_match("", expected_url, h.get("title", ""), h.get("url", ""))
                        else:
                            ok = is_match(expected_title, expected_url, h.get("title", ""), h.get("url", ""))

                        if ok:
                            matched_rank = h.get("rank")
                            break

                    # Log match result
                    if matched_rank is not None:
                        state.add_log(f"[{idx+1}/{len(rows)}] Match found at rank {matched_rank}", "success")
                    else:
                        state.add_log(f"[{idx+1}/{len(rows)}] No match found in top {len(hits)} results", "warn")

                    state.results.append(
                        {
                            "query": query,
                            "expected_title": expected_title,
                            "expected_url": expected_url,
                            "matched": matched_rank is not None,
                            "matched_rank": matched_rank,
                            "hits": hits,
                        }
                    )

                state.completed_queries += 1

        # Build output CSV for download.
        out = io.StringIO()
        writer = csv.writer(out)

        if is_query_only:
            # Query-only mode: Generate Excel with two sheets
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()

            # ===== Sheet 1: Search Results =====
            ws1 = wb.active
            ws1.title = "Search Results"

            # Header styling
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            thin_border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin"),
            )

            # Write headers for Sheet 1
            headers1 = ["Query", "Rank", "Title", "Description", "URL"]
            for col, header in enumerate(headers1, 1):
                cell = ws1.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border

            # Write data for Sheet 1
            row_num = 2
            for r in state.results:
                query = r.get("query", "")
                hits = r.get("hits") or []
                if not hits:
                    ws1.cell(row=row_num, column=1, value=query)
                    row_num += 1
                    continue
                first = True
                for h in hits:
                    ws1.cell(row=row_num, column=1, value=query if first else "")
                    ws1.cell(row=row_num, column=2, value=h.get("rank", ""))
                    ws1.cell(row=row_num, column=3, value=h.get("title", ""))
                    ws1.cell(row=row_num, column=4, value=h.get("description", ""))
                    ws1.cell(row=row_num, column=5, value=h.get("url", ""))
                    row_num += 1
                    first = False

            # Auto-adjust column widths for Sheet 1
            ws1.column_dimensions["A"].width = 30
            ws1.column_dimensions["B"].width = 10
            ws1.column_dimensions["C"].width = 40
            ws1.column_dimensions["D"].width = 50
            ws1.column_dimensions["E"].width = 50

            # ===== Sheet 2: Analysis Summary =====
            ws2 = wb.create_sheet(title="Analysis Summary")

            # Calculate summary data
            total_queries = len(state.results)
            total_results = sum(len(r.get("hits", [])) for r in state.results)
            queries_with_results = sum(1 for r in state.results if r.get("hits"))
            queries_without_results = total_queries - queries_with_results
            avg_results = total_results / total_queries if total_queries > 0 else 0

            # Write summary headers
            summary_headers = ["Metric", "Value", "Percentage"]
            for col, header in enumerate(summary_headers, 1):
                cell = ws2.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border

            # Write overall summary
            ws2.cell(row=2, column=1, value="Total Queries")
            ws2.cell(row=2, column=2, value=total_queries)

            ws2.cell(row=3, column=1, value="Total Results Fetched")
            ws2.cell(row=3, column=2, value=total_results)

            ws2.cell(row=4, column=1, value="Queries With Results")
            ws2.cell(row=4, column=2, value=queries_with_results)
            ws2.cell(row=4, column=3, value=f"{(queries_with_results/total_queries*100):.1f}%" if total_queries > 0 else "N/A")

            ws2.cell(row=5, column=1, value="Queries Without Results")
            ws2.cell(row=5, column=2, value=queries_without_results)
            ws2.cell(row=5, column=3, value=f"{(queries_without_results/total_queries*100):.1f}%" if total_queries > 0 else "N/A")

            ws2.cell(row=6, column=1, value="Average Results Per Query")
            ws2.cell(row=6, column=2, value=f"{avg_results:.2f}")

            # Blank row
            row_num = 8

            # Results distribution header
            dist_headers = ["Results Count", "Number of Queries", "Percentage"]
            for col, header in enumerate(dist_headers, 1):
                cell = ws2.cell(row=row_num, column=col, value=header)
                cell.font = header_font
                cell.fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
                cell.alignment = header_alignment
                cell.border = thin_border
            row_num += 1

            # Count queries by number of results
            result_counts = {}
            for r in state.results:
                count = len(r.get("hits", []))
                result_counts[count] = result_counts.get(count, 0) + 1

            for count in sorted(result_counts.keys()):
                num_queries = result_counts[count]
                ws2.cell(row=row_num, column=1, value=f"{count} results")
                ws2.cell(row=row_num, column=2, value=num_queries)
                ws2.cell(row=row_num, column=3, value=f"{(num_queries/total_queries*100):.1f}%" if total_queries > 0 else "N/A")
                row_num += 1

            # Auto-adjust column widths for Sheet 2
            ws2.column_dimensions["A"].width = 25
            ws2.column_dimensions["B"].width = 20
            ws2.column_dimensions["C"].width = 15

            # Save to bytes
            excel_buffer = io.BytesIO()
            wb.save(excel_buffer)
            state.output_excel = excel_buffer.getvalue()

            # Also write CSV for backward compatibility
            writer.writerow(["Query", "Rank", "Title", "Description", "URL"])
            for r in state.results:
                query = r.get("query", "")
                hits = r.get("hits") or []
                if not hits:
                    writer.writerow([query, "", "", "", ""])
                    continue
                first = True
                for h in hits:
                    writer.writerow(
                        [
                            query if first else "",
                            h.get("rank", ""),
                            h.get("title", ""),
                            h.get("description", ""),
                            h.get("url", ""),
                        ]
                    )
                    first = False

            # Print summary for query-only
            print(f"⭐ Query-only analysis complete:")
            print(f"   Total queries: {total_queries}")
            print(f"   Total results fetched: {total_results}")
            print(f"   Average results per query: {avg_results:.2f}")
        else:
            # With expected mode: Generate Excel with two sheets
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()

            # ===== Sheet 1: Search Results =====
            ws1 = wb.active
            ws1.title = "Search Results"

            # Header styling
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            thin_border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin"),
            )

            # Write headers for Sheet 1
            headers1 = ["Keyword", "Expected Title", "Expected URL", "Result Rank", "Result Title", "Result Description", "Result URL", "Matched"]
            for col, header in enumerate(headers1, 1):
                cell = ws1.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border

            # Write data for Sheet 1
            row_num = 2
            for r in state.results:
                query = r.get("query", "")
                expected_title = r.get("expected_title", "")
                expected_url = r.get("expected_url", "")
                matched_rank = r.get("matched_rank")
                hits = r.get("hits") or []
                if not hits:
                    ws1.cell(row=row_num, column=1, value=query)
                    ws1.cell(row=row_num, column=2, value=expected_title)
                    ws1.cell(row=row_num, column=3, value=expected_url)
                    ws1.cell(row=row_num, column=8, value=False)
                    row_num += 1
                    continue
                first = True
                for h in hits:
                    ws1.cell(row=row_num, column=1, value=query if first else "")
                    ws1.cell(row=row_num, column=2, value=expected_title if first else "")
                    ws1.cell(row=row_num, column=3, value=expected_url if first else "")
                    ws1.cell(row=row_num, column=4, value=h.get("rank", ""))
                    ws1.cell(row=row_num, column=5, value=h.get("title", ""))
                    ws1.cell(row=row_num, column=6, value=h.get("description", ""))
                    ws1.cell(row=row_num, column=7, value=h.get("url", ""))
                    ws1.cell(row=row_num, column=8, value=bool(matched_rank == h.get("rank")))
                    row_num += 1
                    first = False

            # Auto-adjust column widths for Sheet 1
            for col in range(1, 9):
                ws1.column_dimensions[get_column_letter(col)].width = 20

            # ===== Sheet 2: Analysis Summary =====
            ws2 = wb.create_sheet(title="Analysis Summary")

            # Calculate analysis data
            ranks = [r.get("matched_rank") for r in state.results if r.get("matched_rank")]
            total_queries = len(state.results)
            matched_count = len(ranks)
            buckets = [1, 3, 5, 10, 20, 30, 40, 50]
            avg_rank = sum(ranks) / len(ranks) if ranks else None

            # Write summary headers
            summary_headers = ["Metric", "Value", "Percentage"]
            for col, header in enumerate(summary_headers, 1):
                cell = ws2.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border

            # Write overall summary
            ws2.cell(row=2, column=1, value="Total Queries")
            ws2.cell(row=2, column=2, value=total_queries)

            ws2.cell(row=3, column=1, value="Matched Queries")
            ws2.cell(row=3, column=2, value=matched_count)
            ws2.cell(row=3, column=3, value=f"{(matched_count/total_queries*100):.1f}%" if total_queries > 0 else "N/A")

            ws2.cell(row=4, column=1, value="Unmatched Queries")
            ws2.cell(row=4, column=2, value=total_queries - matched_count)
            ws2.cell(row=4, column=3, value=f"{((total_queries-matched_count)/total_queries*100):.1f}%" if total_queries > 0 else "N/A")

            ws2.cell(row=5, column=1, value="Average Match Rank")
            ws2.cell(row=5, column=2, value=f"{avg_rank:.2f}" if avg_rank else "N/A")

            # Blank row
            row_num = 7

            # Ranking distribution header
            ranking_headers = ["Rank Bucket", "Count Found", "Percentage"]
            for col, header in enumerate(ranking_headers, 1):
                cell = ws2.cell(row=row_num, column=col, value=header)
                cell.font = header_font
                cell.fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
                cell.alignment = header_alignment
                cell.border = thin_border
            row_num += 1

            # Write bucket data
            for b in buckets:
                count = sum(1 for rk in ranks if rk <= b)
                ws2.cell(row=row_num, column=1, value=f"Rank 1-{b}")
                ws2.cell(row=row_num, column=2, value=count)
                ws2.cell(row=row_num, column=3, value=f"{(count/total_queries*100):.1f}%" if total_queries > 0 else "N/A")
                row_num += 1

            # Auto-adjust column widths for Sheet 2
            ws2.column_dimensions["A"].width = 20
            ws2.column_dimensions["B"].width = 15
            ws2.column_dimensions["C"].width = 15

            # Save to bytes
            excel_buffer = io.BytesIO()
            wb.save(excel_buffer)
            state.output_excel = excel_buffer.getvalue()

            # Print compact summary to terminal
            print("⭐ Original document ranking analysis:")
            for b in buckets:
                print(f"   Found at rank 1-{b}: {sum(1 for rk in ranks if rk <= b)}")
            if ranks:
                print(f"   Average rank: {avg_rank:.2f}")
            else:
                print("   Average rank: N/A")

        state.output_csv = out.getvalue()

        # Log completion summary
        state.add_log("Processing complete!", "success")
        state.add_log(f"Total queries: {state.total_queries}", "info")
        state.add_log(f"Completed: {state.completed_queries}", "info")
        if state.failed_queries > 0:
            state.add_log(f"Failed: {state.failed_queries}", "warn")
        state.add_log("Excel report generated successfully", "success")

        state.status = "done"
    except Exception as e:  # noqa: BLE001
        state.status = "error"
        state.message = str(e)
        state.add_log(f"Error: {str(e)}", "error")


@app.post("/api/runs", response_model=RunCreateResponse)
async def create_run(payload: RunCreateRequest):
    try:
        rows = _read_csv_rows(payload.csvText)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e

    is_query_only = payload.analysisMode == AnalysisMode.query_only
    run_id = str(uuid.uuid4())
    state = _RunState(run_id=run_id, total=len(rows), is_query_only=is_query_only)
    _RUNS[run_id] = state

    # fire-and-forget
    asyncio.create_task(_run_analysis_job(state, payload))
    return RunCreateResponse(runId=run_id)


@app.get("/api/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str):
    state = _RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStatusResponse(
        runId=state.run_id,
        status=state.status,
        totalQueries=state.total_queries,
        completedQueries=state.completed_queries,
        failedQueries=state.failed_queries,
        message=state.message,
        logs=state.logs,
    )


@app.get("/api/runs/{run_id}/results", response_model=RunResultsResponse)
async def get_run_results(run_id: str):
    state = _RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResultsResponse(runId=state.run_id, results=state.results)


@app.get("/api/runs/{run_id}/download")
async def download_run_csv(run_id: str):
    state = _RUNS.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    if state.status != "done":
        raise HTTPException(status_code=409, detail="Run is not finished yet")

    from fastapi.responses import Response

    # Both modes now return Excel with two sheets
    if not state.output_excel:
        raise HTTPException(status_code=409, detail="Output not ready")
    
    if state.is_query_only:
        filename = f"query-results-{run_id}.xlsx"
    else:
        filename = f"analysis-output-{run_id}.xlsx"
    
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=state.output_excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
