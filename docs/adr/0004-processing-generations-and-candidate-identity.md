# ADR 0004: Processing generations and stable candidate identity

## Status

Accepted (2026-08-08)

Extends ADR 0003, which stays valid. ADR 0003 established that raw imports are
the authority and processing runs are history. It did not say which run's
candidates a reader sees, and it identified a candidate by its position in the
source document. Both turned out to be load-bearing, and both were wrong.

## Context

A review of the first productive import pipeline found that the persisted data
could not answer two questions it was built to answer.

**Which tracks are current?** A processing run appended a row and updated the
tracks it produced. A run that produced fewer candidates left the others standing
as current tracks forever, and a run that produced none left all of them. A
failed run and a successful empty run were indistinguishable in the data.

**Which candidate did the user correct?** Identity was
`(raw import, source index)`. A reprocess that reported one extra candidate in
front shifted every later candidate's identity, so a correction made on one track
silently became a correction on a different one. It reproduced as
`assert ['A'] == ['B']`: nothing looks broken afterwards, which is the worst
property a data error can have.

The same review found that reprocessing — the reason the raw import layer exists
at all — had no way to be invoked, because importing recognises a known content
hash as a duplicate and stops there.

## Decision

### 1. `source_key` is the candidate identity; `source_index` is order

The importer produces an opaque `source_key` per candidate. GPX uses `trk:0`,
`rte:0`, numbered **per container type**, so that reading routes for the first
time does not rename an existing track. Nothing outside the adapter parses it,
orders by it, or reads a format out of it, which is what lets a future FIT
importer key its candidates however that format identifies them —
`activity:0`, `session:0` — without anything inward changing.

`source_index` survives as presentation order and is documented as not being an
identity. Keeping it is cheaper than deriving document order from an opaque
string, and the two concerns are genuinely different.

### 2. One successful run is the current generation

`raw_imports.active_processing_run_id` names it. A track belongs to the current
view exactly when its processing run is that run. Everything else follows:

- a successful run becomes current atomically and in full,
- a run producing no candidate is a successful *empty* generation, which is a
  different fact from a failed run and is now expressible as one,
- a failed run is appended to history and changes nothing, so a broken importer
  version cannot destroy data an older one already produced,
- a candidate a later run stopped producing keeps its row and its correction, and
  gets the correction back if it reappears.

Nothing is deleted because a parser temporarily saw less. Ordinary track queries
answer from the current generation only; what happened across runs is a separate
question and does not belong in the same endpoint.

### 3. Reprocessing is an explicit use case

`ReprocessRawImport` reads the managed raw copy — which verifies the bytes
against their content hash first — and hands them to the same normalization step
an import uses. It does no duplicate check, because "already known" and "still
current" are different statements and it exists to act on the second.

Importing and reprocessing share one `NormalizeRawImport`. A second copy would be
the second import pipeline the architecture forbids, and it would be the copy
that runs less often — the one nobody notices going wrong.

### 4. Managed raw storage verifies content and contains itself

A path named after a digest is valid only while its bytes have that digest, so
bytes are hashed before they are written and after they are read. A corrupt
artifact fails closed and is never overwritten or repaired: it is evidence of a
problem, and replacing it would destroy that evidence.

Every path component below the storage root is opened without following symbolic
links, and the write, rename and read work on the resulting directory descriptor
rather than on a path. The configured root stays trusted and may itself be a
link.

### 5. An external link decides no track kind

GPX `<link>` is a related web resource. Applications write their own home page
into it as readily as planners write a permalink, so it says nothing about how
geometry was produced. Paired with `measurement_metadata_absent` — an absence
that describes a stripped recording just as well — it was enough to reach
`PLANNED`, and an ordinary recording exported by a chatty application came out
planned because of it.

`source_link_present` is deprecated rather than redefined, because a stored code
whose meaning changes invalidates every explanation written under it.
`external_link_present` states the observation without the claim, is weighed on
neither side, and the classifier version becomes 2.

### 6. Evidence belongs to the candidate it was observed on

Navigation instructions were searched once on the document root and handed to
every candidate. They are now the candidate's own when they sit inside its
container; instructions on waypoints are attributed only when the document holds
exactly one candidate and there is nothing else they could describe.

## Consequences

Positive:

- A user's correction cannot move to another track, whatever a later importer
  reports and in whatever order.
- A parser or classifier upgrade is a normal operation on a live archive: the
  worst case of a bad version is a failed run beside data that still works.
- Reprocessing is the mechanism that makes every future rule change applicable to
  data already held, including the classifier change in this same branch.
- The machinery is format-neutral. A FIT importer plugs into the same generation
  and identity model without touching it.

Trade-offs, accepted:

- Schema-1 rows can only be keyed by what schema 1 recorded, so they carry a
  `legacy:` key derived from their index. The prefix says out loud that the
  identity was reconstructed rather than observed. The first reprocess adopts the
  row by position instead of inserting beside it — which is provable, because the
  importer reads the same document and reports candidates in the same order, and
  because the alternative is a correction stranded on a row nobody sees.
- Migration 2 rebuilds the tracks table, with foreign keys disabled and
  `PRAGMA foreign_key_check` before the commit. That is more machinery than an
  `ALTER TABLE`, and it is what moving a uniqueness constraint costs in SQLite.
- Rows of superseded generations accumulate. They are what makes a returning
  candidate recover its correction, and nothing prunes them yet.
- Documents that used to be classified `PLANNED` on a link alone are now
  `UNKNOWN`, including one of the project's own reference files. That is the
  intended direction of error, and the override exists for it.

## Alternatives considered

- **Deleting non-current candidates.** Rejected: the row is where the user's
  correction lives, and a parser that temporarily sees less would silently
  destroy it.
- **A `current` boolean on the track row.** Rejected: it is a second authority
  that can disagree with the run it was derived from, which is the same mistake
  as a stored `effective_kind`.
- **Keeping `source_index` as the identity and renumbering carefully.** Rejected:
  every future importer would have to reproduce the same numbering to stay
  compatible, which is a contract nobody can honour across formats.
- **An XPath or element path as the source key.** Rejected: it would make a
  format artifact into a cross-layer identity, and the domain would end up
  holding a GPX expression.
- **Deriving `--outdated` from the stored versions now.** Deferred: the versions
  do not live in one place — importer and normalization schema on the run, the
  classifier on the classification — so naming a currency authority is a contract
  worth writing deliberately rather than inferring.
