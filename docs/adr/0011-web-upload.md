# ADR 0011: Importing a file from the browser

## Status

Accepted (2026-08-10)

Reverses the "no upload endpoint" position taken in ADR 0003 and restated in
ADR 0008 and ADR 0009. Nothing else those decisions made changes: the import
pipeline, the raw-import authority and the content-hash identity are exactly as
they were, and this adds a caller rather than a path.

## Context

Every earlier ADR here says the same thing, and says it as a consequence of one
missing feature:

> There is no authentication, so there is no upload endpoint.

The reasoning was sound and is unchanged: an unauthenticated endpoint that
accepts files and writes them to disk is a bigger thing than an unauthenticated
endpoint that reads. Anybody who can reach the port could fill the archive, and
the archive is on somebody's home network with no login in front of it.

What changed is not the reasoning. It is that the archive's owner asked for the
feature, for their own deployment, having read what it costs — and that
"importing is an operator action on the machine that holds the data" turned out
to describe a workflow nobody wanted: the tracks are on a phone, the archive is
on a server, and the person is holding a browser.

Deferring it until authentication exists made the archive harder to use for
years in exchange for a property the deployment already does not have. The
honest position is not "no", it is "yes, bounded, and off by default for anybody
who cannot make the trusted-network assumption".

## Decision

> **A file may be offered to the archive over HTTP. It goes through the one
> canonical import use case, its read is bounded before its body is consumed,
> and a deployment can refuse the capability outright.**

```
POST /api/v1/tracks/imports?filename=…
Content-Type: application/octet-stream
<the file>
```

One file per request. The body is the file — no multipart, no envelope, no new
dependency in the tree to parse one.

### What it does not change

**The pipeline.** The bytes reach `ImportTracks`, the same use case the command
line and the scanned directory reach. Content identity, the duplicate rule, the
managed raw storage, the classification and the analysis are all decided there
and nowhere else. The channel is recorded as `web_upload` — metadata, never
authority, exactly like the other two.

**The filename.** Display metadata. It never becomes a path: the raw store files
bytes under their content hash, and a name carrying a directory is discarded.

**The outcome vocabulary.** Four outcomes, and the response says which. A file
the archive read and could not use is `failed` with a reason, at `200`, because
the server did what was asked and concluded something. A refused *request* —
too large, disabled, empty — is a `4xx` with the archive's own error envelope.
Those are different things and the interface says them differently.

### What bounds it

| Bound | Why |
| --- | --- |
| `GPX_VIEW_UPLOAD_ENABLED=false` | A deployment that cannot assume a trusted network keeps every read and refuses this |
| `import_max_bytes`, checked while reading | A limit applied after loading the body has already paid the cost it exists to prevent |
| Declared `Content-Length` over the ceiling | Refused before a chunk is pulled |
| One file per request | A batch endpoint is one verdict for twenty files, or this response inside a list |
| The existing document limits | Tracks, segments and positions per document are bounded by the pipeline, not by the caller |

### What it does not pretend

This is not authentication, and it does not reduce the need for it. The security
section of the README says what the archive exposes, and it now says that the
exposure includes writes. Authentication stays deferred and stays the right
answer for a deployment that is reachable from anywhere.

## Rejected alternatives

**Multipart upload.** The conventional shape, and it needs `python-multipart` in
the dependency tree to parse a format we would use for one field. A raw body is
what a single file already is.

**A batch endpoint.** Twenty files in one request produce one response, and a
reader watching twenty files arrive wants to know which of them the archive
could not read. Answering that inside a batch response means building this
response into a list; answering it with one status means hiding it.

**Writing into the import directory.** Uploading a file into
`GPX_VIEW_IMPORT_DIR` and letting `scan` pick it up would reuse a path that
already exists. It would also break the promise that directory carries — that
nothing in it is written, renamed, moved or deleted — and turn a read-only mount
into a writable one. The upload writes where every other import writes: the
managed archive under the data directory.

**Leaving it until authentication exists.** The position this reverses. It is
defensible and it was chosen once; what it cost was the archive's usability for
the person it was built for, in exchange for a property their network already
provides differently.
