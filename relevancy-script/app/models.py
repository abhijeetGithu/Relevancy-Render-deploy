from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.match_mode import MatchMode


class AnalysisMode(str, Enum):
    """Analysis mode - with expected results or query only."""
    with_expected = "with_expected"
    query_only = "query_only"


class PageType(str, Enum):
    """Page type - normal page or community page."""
    normal = "normal"
    community = "community"


class RunCreateRequest(BaseModel):
    curl: str = Field(..., description="Raw cURL command pasted by the user")
    csvText: str = Field(..., description="CSV content (UTF-8)")
    queryColumn: str
    analysisMode: AnalysisMode = Field(AnalysisMode.with_expected, description="Analysis mode")
    expectedTitleColumn: str | None = None
    expectedUrlColumn: str | None = None
    matchMode: MatchMode = Field(MatchMode.title_and_url, description="How to decide a match")
    topN: int = Field(50, ge=1, le=50)
    # Page type for community page support
    pageType: PageType = Field(PageType.normal, description="Type of page - normal or community")


class RunCreateResponse(BaseModel):
    runId: str


class LogEntry(BaseModel):
    time: str
    level: str
    message: str


class RunStatusResponse(BaseModel):
    runId: str
    status: str  # queued|running|done|error
    totalQueries: int
    completedQueries: int
    failedQueries: int
    message: str | None = None
    logs: list[LogEntry] = []


class RunResultsResponse(BaseModel):
    runId: str
    results: list[dict]
