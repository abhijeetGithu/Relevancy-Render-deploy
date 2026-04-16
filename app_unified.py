"""
Unified entry point for the Search Relevancy Suite.

Mounts all 4 services under a single FastAPI application:
    /              -> Portal (landing page)
    /dataquery     -> DataQuery Engine (Flask via WSGIMiddleware)
    /llm           -> LLM Comparator (FastAPI)
    /relevancy     -> Relevancy Script (FastAPI)

Run:
    uvicorn app_unified:app --host 0.0.0.0 --port $PORT
"""

import importlib
import importlib.util
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Determine the port this app runs on (Render injects PORT)
_PORT = os.environ.get("PORT", "8000")

# Set cross-service env vars before importing sub-apps.
# Browser-facing URLs use relative paths (sub-path routing).
os.environ.setdefault("DATAQUERY_URL", "/dataquery/")
os.environ.setdefault("LLM_COMPARATOR_URL", "/llm/")
os.environ.setdefault("RELEVANCY_SCRIPT_URL", "/relevancy/")
os.environ.setdefault("PORTAL_URL", "/")

# DQE calls LLM Comparator via urllib (needs a full HTTP URL), so we point it
# at the loopback address of this same unified server.
os.environ.setdefault("LLM_COMPARATOR_INTERNAL_URL", f"http://127.0.0.1:{_PORT}/llm")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.wsgi import WSGIMiddleware

app = FastAPI(title="Search Relevancy Suite")

# ── Portal (root) ────────────────────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "portal", "static")),
    name="portal-static",
)

DATAQUERY_URL = os.environ.get("DATAQUERY_URL", "/dataquery/")
LLM_COMPARATOR_URL = os.environ.get("LLM_COMPARATOR_URL", "/llm/")
RELEVANCY_SCRIPT_URL = os.environ.get("RELEVANCY_SCRIPT_URL", "/relevancy/")


@app.get("/", response_class=HTMLResponse)
async def portal_root():
    html_path = os.path.join(BASE_DIR, "portal", "static", "index.html")
    with open(html_path) as f:
        html = f.read()
    html = html.replace("{{DATAQUERY_URL}}", DATAQUERY_URL.rstrip("/") + "/")
    html = html.replace("{{LLM_COMPARATOR_URL}}", LLM_COMPARATOR_URL.rstrip("/") + "/")
    html = html.replace("{{RELEVANCY_SCRIPT_URL}}", RELEVANCY_SCRIPT_URL.rstrip("/") + "/")
    return HTMLResponse(html)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "relevancy-suite-unified"}


def _import_from_path(module_name: str, file_path: str):
    """Import a Python module from an explicit file path, avoiding sys.path conflicts."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── DataQuery Engine (Flask via WSGI) ────────────────────────────────────────
_dqe_dir = os.path.join(BASE_DIR, "DataQuery_Engine")
if _dqe_dir not in sys.path:
    sys.path.insert(0, _dqe_dir)

_dqe_server = _import_from_path("dqe_server", os.path.join(_dqe_dir, "server.py"))
dqe_flask_app = _dqe_server.app

app.mount("/dataquery", WSGIMiddleware(dqe_flask_app))

# ── LLM Comparator (FastAPI sub-app) ─────────────────────────────────────────
_llm_dir = os.path.join(BASE_DIR, "LLM_Comparator", "LLM_Comparator")
if _llm_dir not in sys.path:
    sys.path.insert(0, _llm_dir)

# LLM Comparator's app.py imports from services/* and utils/* which are
# inside _llm_dir (now on sys.path for those sub-imports to resolve).
_llm_module = _import_from_path("llm_app", os.path.join(_llm_dir, "app.py"))
llm_fastapi_app = _llm_module.app

app.mount("/llm", llm_fastapi_app)

# ── Relevancy Script (FastAPI sub-app) ────────────────────────────────────────
_rs_dir = os.path.join(BASE_DIR, "relevancy-script")
if _rs_dir not in sys.path:
    sys.path.insert(0, _rs_dir)

# The relevancy-script's app package imports from app.analysis, app.models, etc.
# These resolve via _rs_dir on sys.path.
_rs_module = _import_from_path(
    "rs_app_main",
    os.path.join(_rs_dir, "app", "main.py"),
)
rs_fastapi_app = _rs_module.app

app.mount("/relevancy", rs_fastapi_app)
