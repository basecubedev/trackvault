# ADR 0012: Getting data out, and getting a deployment back

## Status

Accepted (2026-08-10)

Extends ADR 0003 (import and persistence) with the outbound direction. Nothing
that ADR decided changes: the raw import is still the source authority, the
content hash is still the identity, and `ImportTracks` is still the only way in.
This adds ways *out*, and one way back.

## Context

The archive could take data in and could analyse it. It could not hand any of it
back. That left three different problems wearing one word:

```
"export my track"      give me my file           -> the bytes that arrived
"export my track"      give me something Garmin reads  -> a generated document
"export my archive"    let me move to a new machine    -> everything at once
```

Answering those with one feature would have meant answering at least two of them
wrongly. A file "exported" from the normalized model is not the file that was
imported, however good the importer is; and an archive export that hands back
GPX documents has silently discarded every classification, every title somebody
corrected and every derived metric.

The backup story was worse, because it was a README section rather than a
feature: stop the container, `tar czf` the data directory, start it again. That
procedure is wrong in a way that only shows up when it is needed. Write-ahead
logging means the `.sqlite3` file alone is an incomplete database, and a copy
taken while anything is writing can produce one that will not open. The
instruction was also unverifiable — nothing checked that the resulting tar held
what it should, and the first test of a backup taken that way is the restore
somebody attempts after losing the original.

## Decision

### Three exports, named separately

| Export | Produces | Changes when |
| --- | --- | --- |
| raw source | the bytes that arrived, byte-identical | never |
| exchange document | GPX 1.1 from the current normalized generation | the importer or the writer moves on |
| archive | database + every raw source + a manifest | the archive does |

`export raw` and `export track` are separate commands rather than one command
with a flag, because the difference between them is the whole point. A contract
test asserts that the two never produce the same bytes: if they ever did, one of
them would be lying about what it is, and it would be whichever somebody had
chosen for their backups.

### The exchange document exports less than it could

It carries the displayed title, the activity, the segment boundaries, the
positions, and elevation, instants and sensor readings where they exist. It does
**not** carry the classification. `RECORDED` and `PLANNED` are conclusions this
project reaches from its own evidence rules, at a confidence, with a classifier
version, and no exchange format has a field that means that. A private extension
would publish a claim no other reader could evaluate, so the archive keeps the
verdict and the exchange format does not carry it.

Sensor readings are the one thing written into a vendor namespace, and they are
not an exception to that rule: heart rate and cadence arrived in a defined,
namespaced vocabulary and are written back into the same one. What goes out is
what came in.

Nothing absent is invented. No `<ele>0</ele>` for a position that recorded no
altitude; no clock on a planned route.

### The archive is a plain `tar.gz`

Not a bespoke container, and not compressed with anything exotic. The situation
in which somebody needs a backup is exactly the situation in which TrackVault may
not run, and a backup format only its own application can open is a backup with
a dependency. Any operating system can list and unpack this one: the worst case
still leaves somebody holding their own GPX files and a SQLite database that any
tool can read.

The alternatives were considered and rejected for that reason. `tar.zst`
compresses better and needs a dependency Python 3.13 does not ship. A logical
export — every table as JSON — would have been more portable in theory and would
have made restore a re-import, which is a second write path into an archive
whose whole architecture exists to have one.

```
manifest.json                       first member, so a dry-run reads one small file
database/trackvault.sqlite3         captured through SQLite's own online backup
raw/sha256/ab/abcdef….raw           every original, byte-identical
```

### The manifest is the authority, and it states its omissions

`format_name`, `format_version`, `created_at`, `trackvault_version`,
`schema_version`, a checksum and size for every member, counts of what the
archive holds, and — the field that matters most — what it deliberately leaves
out.

Installed map packages are excluded. They are public datasets that can be
fetched again and they are by far the largest thing a deployment holds. But an
archive that quietly held part of a deployment while calling itself a backup
would be the exact failure this project spends its architecture avoiding, so the
omission is a field rather than a silence. "Complete" and "complete except for
the part nobody mentioned" are different promises.

The format version and the schema version are separate on purpose. They move for
different reasons: one when a table changes, the other when the container does.

### The snapshot names the sources, not the storage directory

The first version of this listed raw members by walking the storage directory.
That is a different question, and it is the wrong one: it answers about a
deployment that is still running, and a source whose file has gone missing does
not appear in the answer at all. The manifest counted the database's sources and
the container held whatever was on disk, so the one failure a backup must never
have — holding less than it says — produced a file with nothing detectably wrong
about it.

So the member list comes from the captured snapshot, which is where the counts
already came from. A source the snapshot names and the storage cannot produce
fails the backup by name. `archive_source_incomplete` is deliberately not
`archive_incomplete`: the fault is in the live deployment rather than in any
container, and the operator's next step is `doctor` rather than another copy of
the file.

Files the snapshot does *not* name are not exported. They carry no import
instant, no filename and no classification, so there is nothing to restore them
as, and inventing one would make a backup the place where unattributed bytes
acquire a provenance. The manifest counts them instead. The rejected
alternatives were failing the backup — too harsh for debris that is by
definition unreferenced, and it would block the backup somebody takes *before*
investigating — and shipping them, which would put objects in the container that
the archive's own database cannot explain.

### A restored map row is not a restored map

Excluding the packages while carrying their rows creates the obvious hazard: a
restore onto a fresh machine would have a database saying "Germany installed"
and a disk with no Germany package on it.

No rule about restores was written for this, because none was needed. An
installation is a row *and* a file that hashes to it, decided in one place, so
the restored entry reports `INVALID` — a state with its own word and its own
instruction — rather than offering a map that answers every tile with nothing.

The case worth designing for is the machine that already has the package.
Packages are content-addressed, so the restored row names exactly the file that
is already there and the map is usable the moment the database is back, with
nothing downloaded and nothing copied.

A package the restored database does *not* name is cleared by the ordinary
start-up recovery. That is a deletion, and it is accepted for the reason the
packages are excluded in the first place: the bytes are replaceable public data,
and the installation belonged to the database the restore replaced. It happens
after the restore rather than during it, so `doctor` can report it first.

### Restore validates before it publishes

```
manifest → format version → schema compatibility → checksums → database integrity → publish
```

Nothing in the destination is touched until every step before `publish` has
passed, which is the property that makes attempting a restore safe. A damaged
archive costs nothing to try.

Compatibility has three answers rather than two. A schema *older* than this build
restores and migrates at the next start — the same path an in-place upgrade takes,
which is what makes an old backup usable rather than merely kept. A schema
*newer* is refused, for the reason the database itself refuses one: this build
does not know what the columns mean.

### Restore replaces contents, not the data directory

The obvious implementation — extract beside the data directory and rename it into
place — works on a laptop and fails on every real deployment, because in a
container the data directory is a mount point and a mount point cannot be
renamed. So what is replaced is what the archive actually carries: the database,
its journal files, and the managed raw storage. The old material is moved aside
first and deleted only once the new material is in place.

Installed maps survive, which is the same decision that keeps them out of the
archive.

The journal files go with the database they belong to. A write-ahead log left
beside a restored database describes transactions from a different database, and
SQLite would apply it.

### "Already holds data" means data, not files

Starting the server creates and migrates an empty database. If that counted as
data worth protecting, the ordinary disaster-recovery path would be
`restore --replace` — and making `--replace` the normal thing to type is how
somebody eventually types it at an archive that mattered. So the check counts
sources, and a database this build cannot account for counts as data.

### Extraction refuses rather than sanitises

A tar member name is a string somebody else chose. Nothing here extracts by
member name: a member is refused unless the manifest declares it *and* its name
survives an allow-list check, and it is then written to a path this code built
from validated components. Members that are not regular files — links, device
nodes, directories — are refused outright.

Two independent reasons, deliberately. Either would be enough today, and keeping
both means a mistake in one is not a vulnerability.

### Backup is a thin layer over the archive

`backup create` is `CreateArchive` with a timestamped name and a verification
pass. The verification re-runs the *restore* validation against the finished
file, because proving a backup with the code that would restore it is the only
proof worth having: a bespoke check would be a second opinion, and the day the
two disagreed the backup would already be the thing at stake.

Backups default to a directory beside the data directory rather than inside it.
A backup kept in the directory it protects is deleted with the volume it was
meant to survive.

## Consequences

- The archive can be moved to another machine, and that is tested end to end:
  back up, destroy, restore, and find the same tracks, corrections and metrics.
- A raw export is byte-identical forever, which is what makes the "immutable
  source evidence" claim checkable by the person who owns the data rather than
  only by the test suite.
- There is no automatic or scheduled backup. That is a feature with its own
  failure modes, and an archive that silently stopped backing itself up would be
  worse than one that never claimed to.
- There is still no HTTP export endpoint. Raw sources are personal movement
  data, the archive has no authentication, and a download URL for somebody's
  recordings is not something to add on the strength of it being convenient.
  Exporting stays an operator action on the machine that holds the data, exactly
  as reprocessing does.
- Downgrading is not supported and now says so in three places that agree: the
  database refuses a newer schema, restore refuses a newer archive, and `doctor`
  reports the situation rather than leaving somebody to infer it.
