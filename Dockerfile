# ── Stage 1: dependency builder ───────────────────────────────────────────────
# Use a full image to compile any C-extension wheels (e.g. uvloop, aiohttp),
# then copy only the installed packages into the slim runtime image.
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as a non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copy installed packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=appuser:appgroup . .

# Drop root
USER appuser

# Port exposed by uvicorn (Azure Container Apps forwards traffic to this port)
EXPOSE 8000

# Health-check so Azure Container Apps / Docker knows when the app is ready.
# The /health endpoint is defined in main.py.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Start uvicorn.
# --workers 1        — single worker keeps LangGraph in-process job_store consistent.
#                      Scale horizontally via Azure Container Apps replicas instead.
# --timeout-keep-alive 75 — slightly above Azure Load Balancer's 60 s idle timeout.
CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "75", \
     "--log-level", "info"]
