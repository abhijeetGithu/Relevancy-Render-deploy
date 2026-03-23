# Relevancy System (Step 1)

This is the starting point for a **document analysis** web UI.

## What’s implemented

- FastAPI server that serves a simple web page
- UI features:
  - Paste a **cURL** command (textbox)
  - Upload a **CSV**
  - Parse CSV headers client-side
  - Let the user map columns for:
    - Query/Keyword (required)
    - Expected Title (optional)
    - Expected URL (optional)
  - Preview first 5 rows

> The actual **Run Analysis** backend workflow will be implemented in the next steps.

## Run locally

### 1) Install dependencies

Use your preferred environment manager (venv/conda).

If you’re using pip:

```bash
pip install -e .
```

### 2) Start the app

```bash
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000

## Tests

```bash
pytest
```
