# Streamlit + psycopg3 deployment image for Fly.io.
# Python 3.12 — stable, has wheels for every dep including psycopg[binary].
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python deps first so layer caches survive code edits.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Now the app itself.
COPY . .

EXPOSE 8501

# Streamlit binds 0.0.0.0 so Fly's proxy can reach it. headless=true skips
# the "open in browser" prompt. CORS / XSRF are off because Cloudflare
# Access already authenticates requests before they hit the app.
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]
