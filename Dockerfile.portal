# Portal — entry hub (port 8000), same as launch.py
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-portal.txt .
RUN pip install --no-cache-dir -r requirements-portal.txt

COPY portal ./portal

EXPOSE 8000

CMD ["uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8000"]
