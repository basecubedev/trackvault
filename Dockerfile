# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install the locked runtime dependencies first so that source changes do not
# invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    GPX_VIEW_HOST=0.0.0.0 \
    GPX_VIEW_PORT=8080 \
    GPX_VIEW_DATA_DIR=/data

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data \
    && chown -R app:app /app /data

WORKDIR /app

# Only the virtual environment is shipped: no uv, no dev dependencies, no tests,
# no build caches. The project itself is installed into the environment.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]

CMD ["uvicorn", "gpx_view.main:app", "--host", "0.0.0.0", "--port", "8080"]
