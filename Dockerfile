FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install dependencies first, so code changes don't reinstall them. The lock file
# pins exact versions so every build gets the same packages.
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY zepto_ordering ./zepto_ordering
COPY scripts ./scripts
COPY db/migrations ./db/migrations

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"

CMD ["python", "-m", "zepto_ordering.main"]
