# Multi-stage Dockerfile for Relevance Evaluator App
# Stage 1: builder for dependency wheels (optional optimization)
FROM python:3.11-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# Stage 2: runtime
FROM python:3.11-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install runtime dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels and install
COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/.temp_downloads /app/query_outputs/generated_queries /app/query_outputs/uploads

# Create a non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5051

# Default command uses gunicorn for production
# Adjust workers based on CPU cores; fallback to 4. Use gevent for non-blocking streaming.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5051} --workers=${GUNICORN_WORKERS:-4} --worker-class=gevent --timeout=${GUNICORN_TIMEOUT:-1200} --access-logfile - --error-logfile - server:app"]
