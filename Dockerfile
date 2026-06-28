# ============================================================================
# Outbound Engine — queue worker image (for Railway / any container host).
#
# This runs the Python engine that drains the `app_jobs` queue your website
# writes to: it picks up "source_run" jobs, runs the scrapers (Meta Ad Library
# via headless Chromium, plus the API/web-search sources), de-dupes, and writes
# leads back to Supabase — which the website then displays.
#
# Base image ships Python 3.12 + Playwright 1.49 + Chromium + all system libs,
# so the headless browser "just works" in the container (the painful part).
# ============================================================================
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

# Install Python deps first so this layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code (web/ and other heavy dirs are excluded via .dockerignore).
COPY . .

# Ensure the Chromium build the scraper expects is present (no-op if already there).
RUN python -m playwright install chromium

# Drain the job queue forever. The website enqueues jobs into Supabase; this
# loop claims and runs them. Set DATABASE_URL (+ any provider keys) as env vars.
CMD ["python", "-m", "orchestration.app_jobs"]
