import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Search Relevancy Suite")

app.mount("/static", StaticFiles(directory="portal/static"), name="static")

DATAQUERY_URL = os.environ.get("DATAQUERY_URL", "/dataquery/")
LLM_COMPARATOR_URL = os.environ.get("LLM_COMPARATOR_URL", "/llm/")
RELEVANCY_SCRIPT_URL = os.environ.get("RELEVANCY_SCRIPT_URL", "/relevancy/")


@app.get("/")
async def root():
    with open("portal/static/index.html") as f:
        html = f.read()
    html = html.replace("{{DATAQUERY_URL}}", DATAQUERY_URL.rstrip("/") + "/")
    html = html.replace("{{LLM_COMPARATOR_URL}}", LLM_COMPARATOR_URL.rstrip("/") + "/")
    html = html.replace("{{RELEVANCY_SCRIPT_URL}}", RELEVANCY_SCRIPT_URL.rstrip("/") + "/")
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {"status": "ok"}
