FROM python:3.11-slim

# --- runtime config / smaller image ---
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8765 \
    RUNCORE_ENV=production \
    RUNCORE_DB_PATH=/data/cloud.db

WORKDIR /app

# Install deps first for better layer caching
COPY pyproject.toml README.md ./
COPY runcore/ runcore/
COPY benchmarks/ benchmarks/
COPY serve.py ./

RUN pip install --no-cache-dir ".[server]"

# Persistent data dir — mount a volume (EBS) over /data so the SQLite db survives restarts
RUN mkdir -p /data && useradd -m -u 10001 runcore && chown -R runcore /app /data
USER runcore

EXPOSE 8765

# Healthcheck hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/health',timeout=4).status==200 else 1)"

CMD ["python", "serve.py"]
