# Troubleshooting

`trackvault doctor` first. It changes nothing, reaches no network, and answers
most of what follows without anybody having to guess.

```bash
docker compose exec trackvault trackvault doctor
```

**`trackvault scan` says "no import directory configured".**
`TRACKVAULT_IMPORT_DIR` is not set in the container. In an installed deployment it
is set for you; if you edited the compose file, check the `environment:` block
still has `TRACKVAULT_IMPORT_DIR: /import`. `trackvault doctor` says which of the two
is wrong.

**`trackvault scan` finds nothing, and the files are definitely there.**
You are probably looking at two different folders. The path you type on the host
and the path the container reads are not the same thing — inside the container
it is always `/import`, and what that maps to on the host is
`TRACKVAULT_IMPORT_PATH` in `.env`. Check what the container actually sees:

```bash
docker compose exec trackvault ls -la /import
```

If that is empty, the mount is pointing somewhere else. Never pass a host path
to a command running inside the container.

**A file in the import folder does not show up.**
The automatic import reads the folder when TrackVault starts and every 15
minutes after that (`TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES`), and takes a file
only once nothing has changed it for five minutes
(`TRACKVAULT_IMPORT_SETTLE_MINUTES`) — so a ride that just arrived waits at least
that long, and then for the next scan. Until then the page counts it as still
arriving. `trackvault scan` imports it at once. Only
`.gpx` files directly in the folder are read; a file in a subfolder, or with a
different ending, is left alone. If `TRACKVAULT_IMPORT_SCAN_ENABLED` is `false`,
nothing is read until you run `scan`. The top of the **Tracks** page says when
the folder was last read and names every file that could not be imported. If it
says the folder could not be opened, the mount is missing — see "`trackvault scan`
finds nothing" above, and `trackvault doctor`.

**Nothing answers on <http://localhost:8081/>.**
8081 is the host port; 8080 is the container's. Check what is actually
published, and that `TRACKVAULT_HTTP_PORT` in `.env` is what you think it is:

```bash
docker compose port trackvault 8080
```

**"permission denied" writing to `data/` or `backups/`.**
The container is running as a different user than the one that owns the folders.
Check `PUID`/`PGID` in `.env` against `id -u` and `id -g`, then
`docker compose up -d`. Do not `chmod 777` — it does not fix the cause and it
makes your movement history world-readable.

**A backup fails with "the backup directory is not writable by this user".**
Same cause, for `backups/`.

**`doctor` reports originals that do not match their content hash.**
The managed copy of a file is not the bytes it claims to be. That is disk
corruption or tampering, and TrackVault deliberately does not overwrite it — the
artifact is the only evidence of what happened. Restore your newest backup, or
delete the affected artifact and import the original file again.

**`doctor` reports missing originals.**
The database knows a file the disk no longer has. Importing the same file again
restores it and reports `repaired`; the bytes hash to the digest it is filed
under, which is the same proof the first import needed.

**Restore says `restore_target_occupied`.**
The archive you are restoring into already holds tracks. That is the safety
catch. Use `--replace` if you meant it, or `--into <directory>` to restore
somewhere else and look first.

**Restore says `archive_schema_unsupported`.**
The backup was written by a newer TrackVault than the one running. Update the
image and try again.

**The map is a neutral background.**
No offline map covers the track. See [Offline maps](maps.md).

**Headline numbers are blank and the page says the analysis is outdated.**
An upgrade changed an algorithm. The archive never reprocesses itself; run
`trackvault reprocess --outdated` and `trackvault analyze --outdated`. See
[Updating](installation.md#updating).

**Something else.** `trackvault doctor` first, then
`docker compose logs trackvault`. The logs never contain coordinates or track
titles, so they are safe to share.
