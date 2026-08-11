#!/bin/sh
# Install TrackVault into an empty directory, using the published container image.
#
# What this script is for: turning "I would like to keep my tracks" into a
# running archive, without a Git clone, without Python and without Node on the
# host. Everything it writes is a file you can read, edit and keep -- there is no
# state anywhere else, and removing the directory removes the installation.
#
# What it will never do: touch your data. `--force` overwrites the compose file
# and the environment file, because those are this script's own output. It does
# not go near data/, import/ or backups/, which are yours.
#
#   curl -fsSL <url>/install-docker.sh -o install-docker.sh
#   sh install-docker.sh
#
set -eu

DEFAULT_IMAGE_REPOSITORY="ghcr.io/basecubedev/trackvault"
DEFAULT_TAG="latest"
DEFAULT_PORT="8081"
CONTAINER_PORT="8080"

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# Private from creation. The directories hold a movement profile of a real
# person, and a mode applied afterwards leaves a window in which it was not.
PRIVATE_MODE="700"

image_repository="${TRACKVAULT_IMAGE_REPOSITORY:-$DEFAULT_IMAGE_REPOSITORY}"
tag="$DEFAULT_TAG"
port="$DEFAULT_PORT"
import_path="./import"
target="."
start=1
dry_run=0
force=0

usage() {
    cat <<'USAGE'
Install TrackVault with Docker.

Usage: sh install-docker.sh [options]

Options:
  --dir <path>          Install into this directory (default: the current one)
  --tag <tag>           Image tag: "latest" for the newest release, vX.Y.Z for
                        a specific one, or "edge" for the current development
                        build (default: latest)
  --image <repository>  Image repository (default: ghcr.io/basecubedev/trackvault)
  --port <port>         Host port to serve on (default: 8081). The container
                        always listens on 8080; this is the host side of it.
  --import-dir <path>   Host directory to mount read-only as the import folder
                        (default: ./import inside the installation)
  --no-start            Write the files but do not pull or start anything
  --dry-run             Print what would happen and change nothing
  --force               Overwrite an existing docker-compose.yml and .env.
                        Never touches data/, import/ or backups/.
  --help                Show this text

Version selection:
  "latest" is the newest tagged release. It is never a development build --
  no commit on the main branch can become anybody's "latest", whatever else
  gets published.

  "edge" is that development build: rebuilt from every commit that lands on
  main, and replaced by the next one. It passes the same tests a release does
  and is still the state between releases -- ask for it deliberately, pin a
  vX.Y.Z if you would rather decide when to move.

After installing:
  docker compose up -d                                      start it
  docker compose exec trackvault trackvault scan            import from ./import
  docker compose exec trackvault trackvault doctor          check the deployment
  docker compose exec trackvault trackvault backup create   write a backup
USAGE
}

fail() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

note() {
    printf '%s\n' "$1"
}

require_value() {
    # A flag that swallows the next flag as its value is how `--port --force`
    # silently installs on port "--force".
    [ "$#" -ge 2 ] || fail "$1 needs a value"
    case "$2" in
        -*) fail "$1 needs a value, got '$2'" ;;
    esac
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dir) require_value "$@"; target="$2"; shift 2 ;;
        --tag) require_value "$@"; tag="$2"; shift 2 ;;
        --image) require_value "$@"; image_repository="$2"; shift 2 ;;
        --port) require_value "$@"; port="$2"; shift 2 ;;
        --import-dir) require_value "$@"; import_path="$2"; shift 2 ;;
        --no-start) start=0; shift ;;
        --dry-run) dry_run=1; shift ;;
        --force) force=1; shift ;;
        *) fail "unknown option '$1' (try --help)" ;;
    esac
done

case "$port" in
    ''|*[!0-9]*) fail "--port must be a number, got '$port'" ;;
esac

image="${image_repository}:${tag}"

# The container runs as whoever installed, so a bind-mounted directory is
# writable without `chmod 777` and without running anything as root. This is why
# the ownership question has no third answer here: the files in data/ belong to
# the person who owns the archive, and the process writing them is them.
puid="$(id -u)"
pgid="$(id -g)"

if [ "$dry_run" -eq 1 ]; then
    note "dry run: nothing will be created or started"
fi

if [ "$dry_run" -eq 0 ]; then
    mkdir -p "$target"
    cd "$target"
else
    note "would install into: $target"
fi

compose_exists=0
env_exists=0
[ -f "$COMPOSE_FILE" ] && compose_exists=1
[ -f "$ENV_FILE" ] && env_exists=1

if [ "$compose_exists" -eq 1 ] && [ "$force" -eq 0 ]; then
    note "keeping the existing $COMPOSE_FILE (pass --force to replace it)"
fi
if [ "$env_exists" -eq 1 ] && [ "$force" -eq 0 ]; then
    note "keeping the existing $ENV_FILE (pass --force to replace it)"
fi

write_compose() {
    cat <<'COMPOSE'
# TrackVault, self-hosted.
#
# Written by install-docker.sh. Everything configurable lives in .env beside
# this file, so an upgrade can replace this file without losing your settings.

name: trackvault

services:
  trackvault:
    image: ${TRACKVAULT_IMAGE}
    container_name: trackvault
    restart: unless-stopped

    # Runs as you, not as root and not as a fixed uid the host knows nothing
    # about. That is what makes the bind mounts below work without loosening
    # any permissions: the process writing your archive is you.
    user: "${PUID}:${PGID}"

    ports:
      - "${TRACKVAULT_HTTP_PORT}:8080"

    environment:
      # Everything persistent. Backing this up backs up the whole archive.
      TRACKVAULT_DATA_DIR: /data
      # Where `trackvault scan` looks. Mounted read-only below, which is the mount
      # enforcing what the application already promises: nothing in the import
      # folder is written, renamed, moved or deleted.
      TRACKVAULT_IMPORT_DIR: /import
      # Where `trackvault backup create` writes. Deliberately not inside /data: a
      # backup kept in the directory it protects is lost with it.
      TRACKVAULT_BACKUP_DIR: /backups
      # Which zone month and year boundaries are drawn in. UTC by default,
      # because a container inherits whatever its image carries and the same
      # archive would otherwise report different monthly totals on two machines.
      TRACKVAULT_TIMEZONE: ${TRACKVAULT_TIMEZONE}
      # Adding files from the browser. On by default; set it to false in .env
      # to refuse the capability. It is the one thing a caller can do that
      # writes, and there is no authentication in front of it. Reading works
      # either way.
      TRACKVAULT_UPLOAD_ENABLED: ${TRACKVAULT_UPLOAD_ENABLED}
      # Set to false to refuse every outbound map download. Installed maps
      # keep working.
      TRACKVAULT_MAPS_ENABLED: ${TRACKVAULT_MAPS_ENABLED}

    volumes:
      - ./data:/data
      - ${TRACKVAULT_IMPORT_PATH}:/import:ro
      - ./backups:/backups

    # The health check comes from the image, so it cannot drift from the
    # endpoint it checks. `docker compose ps` shows its result.
COMPOSE
}

write_env() {
    cat <<ENV
# TrackVault settings. Edit, then \`docker compose up -d\` to apply.

# Which release to run. Pin a version (v1.2.3) to decide upgrades yourself.
TRACKVAULT_IMAGE=$image

# The address you open in a browser: http://localhost:<this>
TRACKVAULT_HTTP_PORT=$port

# The host directory mounted read-only as the import folder. Point this at your
# phone's sync target to import what it uploads.
TRACKVAULT_IMPORT_PATH=$import_path

# The container runs as this user so that ./data and ./backups stay yours.
PUID=$puid
PGID=$pgid

# The zone month and year boundaries are drawn in, for example Europe/Berlin.
TRACKVAULT_TIMEZONE=UTC

# Whether the browser interface may add files. On, because adding a track from
# the browser is what an installation is for. There is no authentication, so
# anyone who can reach the port could add files as well as read them: set this
# to false if your network does not make that acceptable. Importing from the
# command line and the import folder works either way.
TRACKVAULT_UPLOAD_ENABLED=true

# Whether offline map packages may be downloaded at all.
TRACKVAULT_MAPS_ENABLED=true
ENV
}

if [ "$dry_run" -eq 1 ]; then
    note "would create:  data/ import/ backups/  (mode $PRIVATE_MODE)"
    if [ "$compose_exists" -eq 0 ] || [ "$force" -eq 1 ]; then
        note "would write:   $COMPOSE_FILE"
    fi
    if [ "$env_exists" -eq 0 ] || [ "$force" -eq 1 ]; then
        note "would write:   $ENV_FILE"
    fi
    note "would use:     $image"
    note "would serve:   http://localhost:$port"
    note "would mount:   $import_path -> /import (read-only)"
    note "would run as:  $puid:$pgid"
    if [ "$start" -eq 1 ]; then
        note "would run:     docker compose pull && docker compose up -d"
    fi
    exit 0
fi

for directory in data import backups; do
    if [ ! -d "$directory" ]; then
        mkdir -m "$PRIVATE_MODE" "$directory"
    fi
done

if [ "$compose_exists" -eq 0 ] || [ "$force" -eq 1 ]; then
    write_compose > "$COMPOSE_FILE"
    note "wrote $COMPOSE_FILE"
fi
if [ "$env_exists" -eq 0 ] || [ "$force" -eq 1 ]; then
    write_env > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    note "wrote $ENV_FILE"
fi

if [ "$start" -eq 0 ]; then
    note ""
    note "Not started, as asked. To start it:"
    note "  cd $target && docker compose up -d"
    exit 0
fi

command -v docker >/dev/null 2>&1 || fail "docker is not installed; see https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 \
    || fail "this Docker has no 'compose' command; Docker Compose v2 is required"

note "pulling $image"
docker compose pull
docker compose up -d

note ""
note "TrackVault is starting on http://localhost:$port"
note ""
note "  Put GPX files in:  $import_path"
note "  Then import them:  docker compose exec trackvault trackvault scan"
note "  Check the archive: docker compose exec trackvault trackvault doctor"
note "  Back it up:        docker compose exec trackvault trackvault backup create"
