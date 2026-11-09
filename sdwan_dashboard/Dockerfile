FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The history database lives on a volume so it survives container replacement.
ENV DB_PATH=/data/sdwan_history.db \
    POLLER_LOCK_PATH=/data/poller.lock

# Run as a non-root user; nothing here needs elevated privileges.
RUN useradd --create-home --shell /bin/false sdwan \
    && mkdir -p /data \
    && chown -R sdwan:sdwan /app /data
USER sdwan

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz').status==200 else 1)"

# Two workers so a slow vManage call cannot block the whole dashboard. Only one
# of them polls — the lock file under /data settles which.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
