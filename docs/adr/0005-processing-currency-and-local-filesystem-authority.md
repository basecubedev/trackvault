# ADR 0005: Processing currency and local filesystem authority

## Status

Accepted (2026-08-08)

Extends ADR 0003 and ADR 0004, both of which stay valid. ADR 0004 named the
current generation and deferred `--outdated` because the versions involved did
not live in one place. This is that contract, together with the three other
places where a *record* was being trusted instead of the thing it describes.

## Context

The productive import pipeline had four boundaries where something was believed
on the strength of a name rather than of what was actually there.

**A version nobody bumped.** The GPX importer had claimed version 1 through four
changes to what it produces -- candidate identities, evidence locality, link
semantics, root detection -- and the normalization schema had claimed version 1
through the model change that made candidates identifiable at all. Nothing
noticed, because nothing compared. The versions also did not live together: the
importer's and the schema's were on the processing run, the classifier's was on
each track's classification result, so a run that produced no candidate recorded
no classifier version at all.

**A row instead of the bytes.** Recognising a content hash made an import a
duplicate. It proved that the archive *once* held those bytes; whether the
managed artifact was still there, and still had that content, was never asked. An
archive that lost an artifact kept answering "already imported" to the only offer
of those bytes it would ever get again.

**A word instead of a schema.** Extension elements were matched by local name in
any namespace. GPX lets a document carry any element from any namespace, so a
foreign vocabulary using the word `course` produced the measurement evidence that
decides `RECORDED`, and one using `activity` produced an `Activity`.

**A path instead of a file.** The import directory scan checked a path with
`is_symlink()` and `is_file()`, and the reader then opened that path again. Both
statements were about the past; the second open followed anything planted in
between.

Beside those, private movement data was created with whatever the umask allowed
-- world-readable under the ordinary 022 -- and GPX detection assumed the root
element appears within the first 4096 bytes, which an XML prolog is under no
obligation to respect.

## Decision

### 1. `ProcessingProfile` is the processing, and one function compares it

Importer name and version, normalization schema version, classifier name and
version are one value on the run. Migration 4 adds the classifier columns;
existing rows keep them null, because what ran back then is not knowable now.

`is_processing_current` is the only comparison. Not current means: any component
differs, the run cannot prove its profile, there is no successful run, the
adapter is gone, or the stored profile is *newer* than the installed one. That
last case is a downgrade, and reporting newer data as up to date would mean never
regenerating it.

The GPX importer becomes version 2 and the normalization schema version 2. The
classifier stays at 2: better evidence reaching unchanged rules is not a rules
change, and the importer version already distinguishes the output.

### 2. `--outdated` and `--failed` answer different questions

```
--outdated   the current generation was not produced by the installed profile
--failed     the newest attempt failed, whatever readers currently see
```

A source that never processed successfully is in both. One whose newest attempt
failed beside a healthy, matching generation is only in `--failed`. One that
works but was normalized by an older importer is only in `--outdated`. Both read
one snapshot query and the one currency authority, and `processing-status`
reports the same facts for a single source, so a batch run and a status page
cannot disagree.

Nothing reprocesses itself: no start-up migration of the archive, and a scan
still recognises known bytes and stops.

### 3. A duplicate is healthy only with a hash-valid artifact

`RawImportStore.integrity` reads and hashes the managed copy, and every caller
reads that one answer. Missing is repaired from the bytes offered again --
provable, because they hash to the digest the raw import is filed under. Corrupt
fails closed: disk corruption, tampering and a broken file system look alike, and
the artifact is the only trace of any of them. An artifact without its row is
adopted rather than rewritten, which is what makes the crash window between
installing bytes and committing the transaction recoverable.

`REPAIRED` becomes an import outcome of its own. Hiding a repair inside
`DUPLICATE` would make the archive silently fix itself; hiding an integrity
failure there would make it silently not.

### 4. Extension semantics are namespace-aware

`(namespace, element)` maps onto meaning, from a table of the schemas this
project has evidence for. Receiver quality is read from the document's own GPX
namespace and only as a direct child of a position, which is the only place the
schema allows it. Unknown namespaces produce no activity and no evidence, still
never fail a parse, and still survive in the extension summary.

The table lists only schemas a sample in this repository uses. The track-point
extension version that documents a heading is listed; the earlier one of the same
family, which documents temperature, heart rate and cadence but no heading, is
not.

### 5. Discovery and opening are one boundary

The import root is opened once; every candidate is opened relative to that
descriptor with `O_NOFOLLOW` and `O_NONBLOCK`, and what a name refers to is
decided by `fstat` on the open file. `ImportDirectoryEntry` carries a name, not a
path. The configured root stays trusted and may itself be a link, exactly as the
managed raw storage root does.

### 6. Private data is created private

Directories `0700`, files `0600`, stated to the call that creates the object. The
database file is claimed before `sqlite3` opens it, which also decides `-wal` and
`-shm`. Nothing existing is chmodded: a path inside the data directory may carry
an operator's own access decision.

### 7. Detection parses for the root

Parsing stops at the first start tag. The input is already bounded by the import
byte limit, so detection needs no size assumption of its own.

### 8. `source_links` becomes `external_links`

The field claimed the document declared where its geometry came from. ADR 0004
already deprecated the evidence code for exactly that overstatement; the field it
was read from kept it. Migration 3 renames the table.

## Consequences

Positive:

- An importer or classifier upgrade is now an operation an operator can carry
  out: see which sources are behind, reprocess exactly those, and check
  afterwards.
- A lost managed artifact recovers the next time its source is offered instead of
  being masked as a duplicate forever.
- A document cannot reach a business verdict with a word borrowed from someone
  else's vocabulary.
- A file swapped under the scan cannot make the archive read outside its import
  root.
- The archive's own files stop being readable by every user on the machine.

Trade-offs, accepted:

- Every generation written before this branch counts as outdated, because none of
  them can prove a classifier version. That is the intended direction: `reprocess
  --outdated` regenerates them, and doing nothing leaves them readable.
- The duplicate check now reads and hashes the managed artifact, so a repeated
  scan costs one read per known file rather than one database lookup. That is
  what makes the answer true.
- Only schemas this project has a sample of are interpreted. A recorder using a
  vocabulary nobody here has seen loses its heading or its activity until a
  sample proves the namespace -- which is the conservative direction, because the
  alternative is inventing evidence.
- There is no HTTP diagnostics endpoint. `/api/v1/imports/{sha256}/processing`
  would open a resource family whose other verbs do not exist, and the operator
  who can act on the answer is on the machine that holds the data.

## Alternatives considered

- **Deriving currency from the stored versions without a profile.** Rejected:
  the classifier version lives on a track, and a successful run that produced no
  track has none, so the question would be unanswerable for exactly the runs
  worth asking about.
- **Backfilling the classifier columns with today's version.** Rejected: it would
  make every stored generation claim the installed processing, which is the one
  thing the columns exist to disprove.
- **Treating a missing artifact as an integrity failure.** Rejected in favour of
  repair: the same bytes are being offered, they verify against the same digest,
  and refusing them means an operator has to delete a row to recover from a
  problem the archive could fix itself. Corruption stays fail-closed, because
  there the offered bytes and the stored bytes disagree about what is true.
- **Overwriting a corrupt artifact with the offered bytes.** Rejected: it would
  destroy the only evidence that something damaged it.
- **Keeping `exists()` beside `integrity()`.** Rejected: a presence check beside
  an integrity check is the second opinion this ADR is about.
- **A plugin system for extension schemas.** Rejected: a table of two vendors and
  the GPX core is not a plugin problem, and a registry would invite entries
  nobody has a sample for.
- **Resolving import-directory candidates to absolute paths and re-checking
  them.** Rejected: any check on a path is a statement about the past, however
  often it is repeated.
- **`chmod`ing existing files into line.** Rejected: GPX-View does not know why a
  file inside the data directory has the mode it has.
