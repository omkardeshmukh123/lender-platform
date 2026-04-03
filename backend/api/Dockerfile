FROM python:3.11-slim

WORKDIR /app

# Non-root user — never run containers as root
RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

USER app

# Single worker: asyncpg pools live in-process.
# Multiple workers create multiple pools → exceeds Supabase connection limits.
# Horizontal scaling = multiple containers via Railway/Render, not multiple workers.

# Built-in health probe — no curl dependency needed (python:3.11-slim has no curl)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
