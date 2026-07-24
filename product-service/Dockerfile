FROM python:3.14-slim-bookworm AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UPLOAD_FOLDER=/app/uploads
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=app:app . .
RUN mkdir -p /app/uploads && chown app:app /app/uploads
USER app
EXPOSE 5001
CMD ["gunicorn", "--bind=0.0.0.0:5001", "--workers=2", "--threads=4", "--timeout=30", "app:app"]
