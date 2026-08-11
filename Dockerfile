# syntax=docker/dockerfile:1

# Which release this build is. It cannot be looked up here: the build context
# deliberately carries no `.git` (see `.dockerignore`), so the version is handed
# in instead of guessed. The same value writes the distribution metadata *and*
# the image label, which is what stops the two from describing different
# releases. An ordinary `docker compose build` passes nothing and gets an
# honest `0.0.0+unknown` rather than a number that reads as answered.
ARG TRACKVAULT_VERSION=0.0.0+unknown

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

# Declared here rather than at the top of the stage so that changing the
# version does not invalidate the dependency layer above it. `hatch-vcs` would
# otherwise abort: there is no tag in this context to read.
ARG TRACKVAULT_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${TRACKVAULT_VERSION}

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.13-slim-bookworm AS runtime

# The same argument the builder stage installed the distribution with, so the
# label and `trackvault.__version__` inside the image answer identically.
# Deliberately absent: `revision` and `source`. An ordinary
# `docker compose build` knows neither, and an empty label is a worse statement
# than no label.
ARG TRACKVAULT_VERSION=0.1.0

LABEL org.opencontainers.image.title="TrackVault" \
      org.opencontainers.image.description="Self-hosted archive for recorded and planned activity tracks." \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${TRACKVAULT_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    TRACKVAULT_HOST=0.0.0.0 \
    TRACKVAULT_PORT=8080 \
    TRACKVAULT_DATA_DIR=/data \
    TRACKVAULT_BACKUP_DIR=/backups \
    TRACKVAULT_WEB_DIR=/app/web

# A fixed uid/gid keeps ownership predictable across rebuilds, which matters as
# soon as an operator wants to bind-mount a host directory instead of using the
# managed volume.
#
# `/backups` exists in the image for the same reason `/data` does, and it is not
# cosmetic: Docker initialises a named volume from whatever the image has at
# that path, so a mount point the image does not carry arrives owned by root and
# the non-root runtime user cannot write a backup into it.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data /backups \
    && chown -R app:app /app /data /backups \
    && chmod 700 /data /backups

WORKDIR /app

# Only the virtual environment and the built page are shipped: no uv, no dev
# dependencies, no tests, no build caches, no Node.js. The project itself is
# installed into the environment.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=frontend --chown=app:app /web/dist /app/web

# Whoever runs the container is who the attribution and licence statements are
# for. A notice that stays in a repository the operator never sees is not a
# notice, so it ships as a distribution artifact beside what it describes. The
# AGPL is not documentation either: conveying the program means conveying its
# licence, so the text travels in the image rather than only in a Git tree.
COPY --chown=app:app LICENSE /app/LICENSE
COPY --chown=app:app docs/legal/third-party-notices.md /app/THIRD_PARTY_NOTICES.md

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]

CMD ["uvicorn", "trackvault.main:app", "--host", "0.0.0.0", "--port", "8080"]
