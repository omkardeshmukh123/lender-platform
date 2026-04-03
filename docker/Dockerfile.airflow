FROM apache/airflow:2.9.1-python3.11

# Install system deps for scrapling (Chromium-based scraping)
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       chromium chromium-driver \
       libglib2.0-0 libnss3 libatk-bridge2.0-0 \
       libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
       libxrandr2 libgbm1 libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Pipeline dependencies (scrapling, Gemini, Supabase, etc.)
COPY backend/requirements.txt /tmp/backend_requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend_requirements.txt
