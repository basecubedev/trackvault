# Installation

Everything about getting TrackVault onto a machine and keeping it there. The
[README](../../README.md) has the three commands; this has the rest.

## What the installer writes

```
trackvault/
├── docker-compose.yml    written by the installer; --force replaces it
├── .env                  your settings; edit this, not the compose file
├── data/                 the archive: database, your originals, installed maps
├── import/               drop files here; they are imported automatically
└── backups/              where `trackvault backup create` writes
```

`data/` is **application-owned** and `import/` is **external input**. They are
deliberately not the same directory: a backup written into the import folder
would be re-read as an import, and a restore would overwrite files you were
still syncing.

## Installer flags

`sh install-docker.sh --help` lists them all.

| Flag | For |
| --- | --- |
| `--tag v1.2.3` | pin a release instead of following `latest` |
| `--port 9090` | something else already has 8081 |
| `--import-dir /srv/sync/locus` | your phone syncs somewhere else |
| `--dir /srv/trackvault` | install somewhere other than the current directory |
| `--dry-run` | print what would happen and change nothing |
| `--no-start` | write the files, start it yourself |
| `--force` | replace the compose file and `.env` |

`--force` replaces the two files the installer wrote. It never touches `data/`,
`import/` or `backups/`. Running the installer again without it keeps
everything, including a compose file you have edited.

`latest` is the newest tagged release and is never a development build: images
are published from release tags only, so an untagged commit on the main branch
cannot become anybody's `latest`.

## Ports

```
8081   on the host          what you open in a browser
8080   inside the container what the application binds, and what its health
                            check asks
```

Only the host side is configurable, because only the host side can collide with
something else on the machine. `--port 9090` publishes `9090:8080`; the
container is unchanged, which is why nothing inside it has to be reconfigured.
The setting lives in `.env` as `TRACKVAULT_HTTP_PORT` and applies on the next
`docker compose up -d`.

## Who the container runs as

As you. The installer writes your own uid and gid into `.env` as `PUID`/`PGID`,
so the process writing `data/` and `backups/` is the person who owns them. That
is why nothing here asks you to run `chmod 777` or to run anything as root: the
bind mounts work because the ownership already matches.

If you move the installation to another user, update those two values.

## Windows and macOS

The same installer and the same compose file. On Windows, run it from Git Bash
or WSL — Docker Desktop shares the daemon with both, and the compose file needs
no change.

There is deliberately **no PowerShell installer**. It would be a second copy of
the same logic, and the copy that gets less use is the one that silently rots;
the one thing it could offer over `sh` — knowing a Windows path — is exactly the
thing bind mounts on Docker Desktop want in POSIX form anyway. `PUID`/`PGID` are
ignored on Docker Desktop, which manages ownership itself, so a Windows
installation is the Linux one minus a problem.

## Running from a checkout instead

[`compose.yaml`](../../compose.yaml) in this repository builds from source and is
what development and the test suite use. It makes the opposite choice about
ownership: managed volumes and the image's own user, so nothing depends on whose
uid happens to be 1000. Get a backup out of it with

```bash
docker compose cp trackvault:/backups/<name>.tar.gz .
```

Both are correct; they are answers to different questions. A user deployment
wants its files where its owner can read them, and a development checkout wants
to be independent of the developer.

## Updating

```bash
docker compose exec trackvault trackvault backup create   # 1. before anything
docker compose pull                                       # 2. get the new image
docker compose up -d                                      # 3. start it
```

That is the whole procedure when no migration is needed, and it is safe when one
is: the schema migration runs at start-up, inside one transaction per step.

To move to a specific release rather than the newest, set the tag in `.env`:

```
TRACKVAULT_IMAGE=ghcr.io/basecubedev/trackvault:v1.2.3
```

**Downgrading is not supported.** A database written by a newer build is refused
rather than converted silently — this build cannot know what the newer columns
mean, and guessing would be worse than stopping. Going back means restoring the
backup you took in step 1, which is why it is step 1. `trackvault doctor` reports
the situation in words if you ever end up in it.

After an upgrade that changes an analysis algorithm or the importer, the archive
does **not** reprocess itself: that would turn every deployment into a full
re-parse. It reports what is behind, on the dashboard and in the track list, and
you run:

```bash
docker compose exec trackvault trackvault reprocess --outdated
docker compose exec trackvault trackvault analyze --outdated
```

Until you do, the old numbers are shown as needing re-analysis rather than as
current. That is the point: a stale number presented as a headline is worse than
a blank one.

Which release and which algorithms a deployment is running is answered by
`GET /api/v1/system/info`, by the **About** page, and by `trackvault doctor`.

## One name, everywhere

TrackVault is the product name, and it is also the console script, the Python
distribution, the compose service, the image path and the `TRACKVAULT_*`
configuration prefix. There is nothing to translate between what you read and
what you type.
