# Production deployment — Search Relevancy Suite

This repo runs **four services**, same as local development:

| Service | Port | Role |
|--------|------|------|
| **Portal** | 8000 | Landing page / links into apps |
| **DataQuery_Engine** | 5050 | Main Flask UI + APIs (`server.py`) |
| **LLM_Comparator** | 8001 | Google search helpers, LLM judge (used by DataQuery server + browser for “Open LLM Comparator”) |
| **relevancy-script** | 8002 | Additional FastAPI app |

Local dev: `python3 launch.py` starts all four (see `launch.py`).

---

## Option A — Docker Compose (recommended for production)

Root [`docker-compose.yml`](docker-compose.yml) mirrors `launch.py`: builds four images, wires **DataQuery → LLM Comparator** on the internal network (`LLM_COMPARATOR_URL=http://llm-comparator:8001`).

### 1. Configure environment (optional)

```bash
cp .env.example .env
# Edit .env: host ports, OPENAI_* for LLM Comparator, GUNICORN_* for DataQuery
```

### 2. Build and run

```bash
docker compose build
docker compose up -d
```

### 3. Open the apps

- Portal: `http://<host>:8000` (or `PORTAL_HOST_PORT` from `.env`)
- Data Query Engine: `http://<host>:5050`
- LLM Comparator (direct): `http://<host>:8001`
- Relevancy script: `http://<host>:8002`

**Important:** Use the **root** `docker-compose.yml` in the repository root — not `DataQuery_Engine/docker-compose.yml` or `relevancy-script/docker-compose.yml` unless you intentionally run a subset.

### Volumes

Compose persists DataQuery working data:

- `dqe_query_outputs` → `/app/query_outputs`
- `dqe_temp_downloads` → `/app/.temp_downloads`

### Healthchecks

Services define healthchecks; DataQuery waits for LLM Comparator to be healthy before starting.

---

## Option B — Bare metal / VM (no Docker)

Same as development:

```bash
cd /path/to/Relevancy\ Framework
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r DataQuery_Engine/requirements.txt
pip install -r LLM_Comparator/LLM_Comparator/requirements.txt
pip install -r relevancy-script/requirements.txt
pip install -r requirements-portal.txt

python3 launch.py
```

Use a **process manager** (systemd, supervisord, PM2, etc.) to run `launch.py` or each `uvicorn`/`gunicorn`/`flask` command separately if you need restarts and logging in production.

Set **`DATAQUERY_NO_RELOADER=1`** for DataQuery when not using `launch.py` (already set in `launch.py`).

---

## Environment variables (reference)

| Variable | Where | Purpose |
|----------|--------|---------|
| `LLM_COMPARATOR_URL` | DataQuery_Engine | Base URL for server-side calls to LLM Comparator. In Docker Compose defaults to `http://llm-comparator:8001`. On bare metal: `http://127.0.0.1:8001`. |
| `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT` | DataQuery Docker | Production WSGI (`server:app`). |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | LLM Comparator Docker | Features that call OpenAI from that app. |
| `PORTAL_HOST_PORT`, `DATAQUERY_HOST_PORT`, etc. | Root `.env` | Publish different host ports. |

DataQuery also reads project-specific settings; see `DataQuery_Engine/.env.example` if present.

---

## Reverse proxy / HTTPS

- Terminate TLS at nginx, Caddy, or your cloud load balancer.
- Proxy **each** port (8000, 5050, 8001, 8002) or map paths to services; if you use path-based routing, the DataQuery `frontend.html` **API base** logic may need the app served from a path (see comments around `API_BASE` in `DataQuery_Engine/frontend.html`).
- The UI uses **`http://localhost:8001`** for “Open LLM Comparator” and some flows; for users **not** on the same machine as the browser, replace with your public LLM Comparator URL (or add a small env-injected script in your proxy build). Server-side Google/LLM calls use `LLM_COMPARATOR_URL` and work inside Docker without that.

---

## Google Web Scraper (Selenium) in production / Docker

The **LLM Comparator** image is built with **Chromium** and libraries needed for **headless** `undetected-chromedriver` (same code path as local `python3 launch.py`).

- **`CHROME_BIN=/usr/bin/chromium`** is set in the image and in Compose so Selenium uses the packaged browser.
- **`shm_size: 1gb`** on the `llm-comparator` service avoids Chrome crashes from the default small `/dev/shm` in Docker.

If the scraper still misbehaves:

- Increase `shm_size` (e.g. `2gb`) on very heavy workloads.
- **Google may block or CAPTCHA** some cloud/datacenter IPs; that is unrelated to Docker. Use **Google Custom Search API** in the UI (no browser) when that happens.
- Bare-metal: install **Google Chrome** or **Chromium** on the host; optional `CHROME_BIN` / `GOOGLE_CHROME_BIN` points Selenium at your binary.

---

## Dockerfiles (summary)

| File | Service |
|------|---------|
| [`Dockerfile.portal`](Dockerfile.portal) | Portal |
| [`DataQuery_Engine/Dockerfile`](DataQuery_Engine/Dockerfile) | Gunicorn + Flask `server:app` |
| [`LLM_Comparator/LLM_Comparator/Dockerfile`](LLM_Comparator/LLM_Comparator/Dockerfile) | Uvicorn on **8001** |
| [`relevancy-script/Dockerfile`](relevancy-script/Dockerfile) | Uvicorn (Compose overrides port to **8002**) |

---

## Quick checklist before go-live

- [ ] Root `docker compose build && docker compose up -d` (or systemd + `launch.py`)
- [ ] Firewall / security groups allow required ports (or only 443 via reverse proxy)
- [ ] Persistent volumes or backups for `query_outputs` / temp downloads if needed
- [ ] Secrets (API keys) via env or secret manager, not committed files
- [ ] Test Search Evaluation, Google API path, and LLM comparison on the deployment URL
