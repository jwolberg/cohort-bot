# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching), then the app.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# Cloud Run injects PORT (defaults to 8080). Use exec form so signals reach uvicorn.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
