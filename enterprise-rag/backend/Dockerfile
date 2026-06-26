# =============================================================================
# Enterprise Knowledge Assistant - Backend Dockerfile
# Multi-stage build for production and worker targets
# =============================================================================

FROM python:3.12-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmagic1 \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    pandoc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Production stage ──────────────────────────────────────────────────────────
FROM base AS production

COPY . .

# Create non-root user for security
RUN useradd -m -u 1001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop"]

# ── Worker stage ──────────────────────────────────────────────────────────────
FROM base AS worker

COPY . .

RUN useradd -m -u 1001 appuser \
    && mkdir -p /app/storage \
    && chown -R appuser:appuser /app

USER appuser
