FROM python:3.11-slim-bookworm
LABEL io.quad-race.updater.protocol="1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt check_env.py ./
RUN python check_env.py \
    && python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY scripts ./scripts

RUN mkdir -p /app/data /app/backend/static/uploads/signatures

EXPOSE 8000 8010

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ui/monitor', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
