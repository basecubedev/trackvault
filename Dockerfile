# syntax=docker/dockerfile:1

# The browser application is built here and only its output is carried forward.
# Node is a build tool, not a runtime: the final image runs Python and serves
# static files, so shipping a package manager, a bundler and forty thousand
# files of `node_modules` would be shipping an attack surface to save a stage.
FROM node:24-bookworm-slim AS frontend

WORKDIR /web

# `npm ci` installs exactly what the lock file resolved, and fails rather than
# silently updating anything -- which is the property that makes the licence
# audit and the container mean the same thing.
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --ignore-scripts=false

COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src
# `public/` holds the map label glyphs. Vite copies it into the build verbatim,
# and without it the map renders every place name as nothing -- which is what a
# font service would otherwise be for, and the whole point of shipping them.
COPY web/public ./public
RUN npm run build


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

# The version the build installs, and the one authority for it is
# `pyproject.toml`. `tests/contract/test_release_metadata_contract.py` fails
# when this default drifts from it, so the label cannot quietly describe another
# release. Deliberately absent: `revision` and `source`. An ordinary
# `docker compose build` knows neither, and an empty label is a worse statement
# than no label.
ARG GPX_VIEW_VERSION=0.1.0

LABEL org.opencontainers.image.title="GPX-View" \
      org.opencontainers.image.description="Self-hosted archive for recorded and planned geospatial tracks." \
      org.opencontainers.image.version="${GPX_VIEW_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    GPX_VIEW_HOST=0.0.0.0 \
    GPX_VIEW_PORT=8080 \
    GPX_VIEW_DATA_DIR=/data \
    GPX_VIEW_WEB_DIR=/app/web

# A fixed uid/gid keeps ownership predictable across rebuilds, which matters as
# soon as an operator wants to bind-mount a host directory instead of using the
# managed volume.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data \
    && chown -R app:app /app /data

WORKDIR /app

# Only the virtual environment and the built page are shipped: no uv, no dev
# dependencies, no tests, no build caches, no Node.js. The project itself is
# installed into the environment.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=frontend --chown=app:app /web/dist /app/web

# Whoever runs the container is who the attribution and licence statements are
# for. A notice that stays in a repository the operator never sees is not a
# notice, so it ships as a distribution artifact beside what it describes.
COPY --chown=app:app docs/legal/third-party-notices.md /app/THIRD_PARTY_NOTICES.md

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]

CMD ["uvicorn", "gpx_view.main:app", "--host", "0.0.0.0", "--port", "8080"]
