# Agent rules

This file is the **canonical and only complete rule source** for automated agents
and human contributors working on TrackVault.

`AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md` are short entry
points. They point here and must never grow into a second copy of these rules.
`tests/contract/test_agent_rules_contract.py` enforces that.

TrackVault is a self-hosted, source-agnostic activity and route archive for recorded
and planned geospatial tracks. GPX is the first supported exchange format, not the
product.

---

## 1. Scope discipline

- Do exactly what the task asks for. Do not widen, narrow or transform the scope.
- Do not implement features "while you are in there". Unrelated problems get
  reported, not fixed silently.
- Do not create speculative modules, adapters or abstractions for requirements
  that have no contract yet.
- If a task turns out to be underspecified, state the assumption you made and
  keep going; ask only when proceeding either way would waste the work.

## 2. Contract-first

Every change with business meaning follows this order:

1. Define the public/business behaviour.
2. Write the reproducing or contract test.
3. Watch the test fail for the right reason.
4. Write the smallest correct implementation.
5. Keep the test.
6. Run the focused tests.
7. Run the broader regression.

Tests verify behaviour and contracts, never incidental implementation detail. Do
not write tests that merely mirror the current internal call structure, and do
not write brittle full-text snapshots of documentation.

## 3. Single source of truth

Exactly one owner per concern. `docs/technical/architecture.md` holds the full
authority table. In short:

| Concern | Authority |
| --- | --- |
| What was imported | the raw import: the original file, byte-identical, plus its content hash |
| Normalized track data | the canonical normalized track model |
| Detected classification | the classifier result, with confidence, evidence and method version |
| Effective classification | an explicit user override if present, otherwise the detected kind |
| Displayed track title | an explicit user title if present, otherwise the source title |
| Calculated statistics | the analysis layer, from the canonical normalized track |
| Analysis algorithms | the installed `AnalysisProfile` |
| Per-track derived metrics | the current successful `AnalysisRun` for the current processing generation |
| Actual aggregates | tracks whose effective kind is `RECORDED` |
| Planned aggregates | tracks whose effective kind is `PLANNED` |
| Month and year bucket | the canonical activity timestamp plus the configured aggregation timezone |
| Source metadata | evidence and provenance information, never business authority |
| Import | the single canonical `ImportTrack` use case |
| HTTP representation | projection only |
| Analysis availability | `InstalledAnalysis.availability` -- one answer, projected everywhere |
| List headline metrics | the current analysis only |
| Actual calendar placement | trusted temporal evidence, never the track kind |
| Profile and map series | the current geometry plus the installed analysis algorithms |
| Browser/frontend state | projection only |
| Installed map packages | a database row **and** a managed file that hashes to it |
| Map attribution | the installed package's own metadata |
| Which map draws behind a track | `SelectMapCoverage` over installed coverage |
| Exported original bytes | the managed raw artifact, verified against its hash |
| Exported exchange document | the current normalized generation, never the source file |
| What an archive holds | its own manifest, including what it deliberately omits |
| Whether an archive may be restored | one compatibility rule over format and schema version |
| Configuration | `trackvault.config` backend settings |

No UI and no import adapter may ever create a second, independent business truth.
Projections are allowed to reshape data; they are not allowed to decide it.

## 4. Source-agnostic architecture

- **Input format is never the domain model.** `GPX != Track`, `FIT != Track`,
  `Locus != Track`, `Komoot != Track`. Formats and source applications are
  adapters and metadata, never business types.
- Every importer normalizes onto the canonical normalized track. From there the
  core application must not need to know the original format.
- A format adapter ends at the normalization boundary. GPX XML structures, FIT
  messages and similar format types must not travel further inwards.
- **Single import authority**: manual upload, watched import folder and a future
  API import are input paths, not pipelines. All of them run through the same
  canonical `ImportTrack` use case. Never one parser or persistence path per
  channel.
- Unknown vendor extensions must not needlessly fail a parse, and must not become
  business fields of a domain model either.
- Adding a format means adding an adapter, not a factory hierarchy. A `Protocol`
  plus, only if genuinely needed, a small dispatcher. Keep it boring.

## 5. Business invariants

Full detail in `docs/technical/contracts.md`. Non-negotiable:

- **Raw imports are immutable source evidence.** Originals are never modified,
  reprocessing always starts from the original again, and a normalized projection
  never replaces the source. Raw data is not deleted because the current parser
  version succeeded. A raw import is identified by its content hash; its filename
  is display metadata and never authorises a storage location.
- **Processing provenance is not source authority.** Importer name, importer
  version and normalization schema version belong to the processing run, never to
  the raw import. Reprocessing with a newer importer must leave the raw import
  byte-identical and unchanged.
- **Source metadata is evidence, not authority.** No `if source == "locus"` or
  `if source == "komoot"` shortcut may decide a track kind.
- **Recorded, planned and unknown are distinct.** `unknown` is permanent and
  first-class; failing to unknown beats invented certainty. Neither a GPX `<trk>`
  or `<rte>` element, nor the presence or absence of timestamps, nor the exporting
  application proves a kind.
- A `RECORDED` or `PLANNED` classification must state evidence, a confidence and
  the classifier method and version. Without evidence, answer `UNKNOWN`.
- **Explicit user overrides beat automatic classification.** Reprocessing replaces
  the detected result and keeps the override.
- **One authority for the effective track kind.** `TrackClassification` owns it
  through `effective_kind`. No normalized track, database row or HTTP payload may
  hold a second, independently assignable `kind`; anything else is a projection.
- **Measured, derived and estimated metrics must not be conflated.** Provenance is
  part of a metric's meaning and must never be silently discarded: values of
  different provenance are never summed, averaged or presented as one number.
  Comparing them *as a comparison*, with both provenances visible, is explicitly
  allowed — that is how quality checks and deviation analysis work.
- Actual and planned aggregates are separate sets, selected by the *effective*
  kind. `UNKNOWN` contributes to neither and is never silently assigned.
- Activity is independent of format, source and track kind, and is never guessed.
  The taxonomy stays small and flat.
- `exact_duplicate != semantic_duplicate`. Byte-identical detection and
  same-activity detection are different problems; no premature heuristic for the
  second.
- **Persisted processing semantics require an explicit version bump.** A change
  to the normalized identity, the candidate set, the evidence semantics or the
  interpretation of a field means a new importer, normalization or classifier
  version. An internal refactoring with identical output does not. A run that
  claims a version whose output it could not have produced makes every later
  currency decision wrong.
- **A known database record does not prove its managed raw artifact is healthy.**
  Recognising a content hash proves the archive *once* held those bytes. A
  duplicate is healthy only with a hash-valid managed artifact beside its
  metadata; a missing artifact is repairable from the same bytes, and a corrupt
  one fails closed and is never overwritten.
- **Unknown XML namespaces are metadata, not semantic authority.** Extension
  elements are interpreted on the namespaced name, from a small table of schemas
  this project has evidence for. An element that merely shares a local name
  decides nothing, and an unknown namespace never fails a parse either.
- **Derived metrics are rebuildable and never source authority.** Analysis reads
  the current normalized track and produces a cache of it. It never modifies the
  geometry it read -- an outlier is an analysis decision, not a deletion -- and a
  failed analysis never costs a track its import or the metrics it already had.
- **Analysis semantics require explicit version bumps.** A changed distance,
  movement or elevation algorithm, or a changed metric meaning, is a new
  `AnalysisProfile` version. Stored metrics that cannot say which algorithms
  produced them make every later "is this still right?" unanswerable. Metrics
  are also bound to the processing generation they were derived from: new
  geometry outdates them, a user override does not. An algorithm change that
  alters a user-visible number is such a change, however small the diff looks.
- **Synthetic timestamps must not silently become observed activity metrics.**
  A timestamp proves that something wrote a time, not that anybody moved.
  Temporal evidence is derived from evidence of *measurement* -- never from the
  presence of an instant, the exporting application or the detected track kind
  -- and a clock-dependent metric is never presented without it. Analysis still
  derives every number the data supports; what is gated is the claim, not the
  calculation.
- **Statistics must not silently mix analysis algorithm versions.** A default
  total covers only tracks whose analysis is current, and says how many of the
  period it left out and why. Adding two algorithm generations produces a figure
  that measures neither, and there is no option to ask for one.
- **Every read surface says the same thing about an analysis.** A listing row, a
  track's own analysis resource and a total project one value -- `current`,
  `outdated`, `missing` or `invalid` -- decided in one place. A metric is
  presented as the track's own only while it is `current`; a stale or damaged
  number is the last thing that was derived, which is a different claim. An
  ordering by distance means the current distance, and a filter on availability
  is applied where the page is counted, not after it is cut.
- **Persisted derived state is validated before it is interpreted.** The
  database is the authority, which is exactly why what comes back out of it is
  untrusted input: it may come from a version that is gone, a failing disk or a
  defect since fixed. Deserialisation fails closed -- unreadable derived state
  is never current, never reaches a total, and never surfaces as a traceback.
- **Prefer metamorphic invariants to numeric snapshots.** For an algorithm,
  state the relation that must hold when the input is transformed in a way that
  should not change the answer -- reversed, resampled, shifted, offset. A pinned
  "this fixture produces 483.4" breaks on every legitimate improvement and says
  nothing about the cases nobody thought to pin.
- **Actual and planned aggregates must never be conflated.** They are separate
  sets, selected by the *effective* kind, and `UNKNOWN` stays excluded from both
  rather than being assigned to one. There is no combined default: a total that
  adds planned routes to travelled distances is about neither.
- **Missing metrics are not zero metrics.** An underivable value is absent, and
  `null` over HTTP. A planned route reports no moving time; `0` would claim it
  was travelled and nobody moved. An empty *period* may still total zero -- its
  track count says it is empty.
- **Activity date must not be inferred from import time.** A track's month and
  year come from its own positions. The import instant is when the archive
  learned about it and a document's export time is when the file was written;
  neither is an activity date, and a track without timestamps belongs to no
  month rather than to 1970.
- **Public map tile services are never an offline-download authority.**
  Community tile servers run on donated bandwidth and their usage policies
  exist to forbid the bulk download an offline archive would need. An offline
  map comes from a provider whose download service is meant for it.
- **Installed map packages are replaceable external datasets, not track source
  evidence.** A raw import is the only copy of somebody's afternoon and fails
  closed on corruption; a map package is public data that can be fetched again,
  so corruption there is discarded and reinstalled. What both share is that an
  installation is a database row *and* a file that hashes to what the row says
  — either alone is `invalid`, never `installed`.
- **Normal map viewing must not require an external network request.** With
  coverage installed, a track page reaches the provider not at all. The network
  is touched during three actions somebody pressed: refresh the catalog,
  install, update.
- **Map attribution is package metadata and must survive every projection.** It
  is read out of the package, not written into the build, and it reaches the
  map, the manager and the credits page unchanged. A package stating no author
  and no licence is refused rather than installed under an assumption, and
  credit links are structured pairs rather than markup.
- **Map package installation is atomic, and a failed update preserves the
  previous valid package.** The new package is fetched and validated beside the
  old one, never over it; the old file goes only after the new row commits.
- **Provider URLs are infrastructure-owned and may never be supplied by an API
  caller.** A caller names a region; the adapter resolves the address, and a
  redirect is followed only inside the hosts that adapter declares.
- **Remote map packages are untrusted input and are validated before
  publication.** Bounded stream, hash while streaming, declared length checked,
  container and tile vocabulary verified, and nothing a provider sends is ever
  used as a path.
- **Timestamp presence is not calendar placement.** A track's instants are its
  *timeline*; the claim that the activity happened in a given period is a
  separate, stronger statement, and only instants shown to have been measured
  support it. A planner's synthetic clock and a stripped recording look
  identical, so a track without that evidence belongs to no month -- it is
  reported beside the period, with its length intact, and separately from a
  track that carries no instants at all. A kind override never changes this: it
  says what a track is, not that its clock was measured.

## 6. Architecture boundaries

Layers, inner to outer: `domain` -> `application` -> `infrastructure` / `api`.

- `trackvault.domain` uses the Python standard library only, and not its
  infrastructure corners either: no `xml`, `sqlite3`, `pathlib`, `os`, `json`,
  `csv`, `http` or `urllib`. No FastAPI, database, Docker, GPX/FIT parser or
  frontend imports. No vendor-specific classes, and no branching on vendor name
  literals.
- `trackvault.application` may use the domain. External systems are reached only
  through explicit ports (`typing.Protocol`). It knows no concrete parser and must
  not import FastAPI, the API layer or concrete adapters.
- `trackvault.infrastructure` holds concrete adapters and is the only layer that
  knows formats and storage. It implements the contracts of the inner layers and
  must not import the API layer.
- `trackvault.api` is the HTTP projection: validate, call a use case, project the
  result. No classification or analysis heuristic in a route.
- A future browser UI is projection only. Browser state is never business
  authority.
- `trackvault.main` is the composition root. Bootstrap only, never business logic.

`tests/contract/test_architecture_contract.py` enforces these boundaries with an
AST check. If a boundary genuinely has to change, change the documentation and the
contract test in the same commit, and say so.

## 7. Test isolation

- Tests must pass with no internet, no external services, no real GPS files and
  no Docker. This is **enforced**, not merely intended: an autouse fixture
  refuses every connection and every lookup that leaves this machine, so a test
  that reaches a provider fails rather than depending on whether the provider
  answered. Loopback stays open, because the transfer tests run a real server
  there. Wire a fixture provider before the request, never a `status_code in
  {404, 503}` that passes either way.
- Docker-dependent tests carry the `docker` marker and are opt-in
  (`uv run pytest -m docker`).
- No test may depend on another test's side effects or on execution order.
- Never write into the developer's home directory or into `/data`. Use `tmp_path`.
- Markers are declared in `pyproject.toml` and validated by `--strict-markers`.

## 8. Deterministic tests

- No dependency on the current wall-clock time, time zone, locale, random seed,
  network latency or file system ordering.
- Inject clocks and identifiers rather than reading them from global state.
- No `sleep`-based synchronisation except in explicitly marked Docker tests that
  poll a start-up condition with a bounded timeout.
- A flaky test is a defect. Fix it or delete it; never retry it into green.

## 9. Security and secrets

- No secrets, tokens, passwords or API keys in the repository, in tests, in
  fixtures or in log output.
- `.env` is git-ignored and must stay that way. `.env.example` may only contain
  non-sensitive placeholder values.
- No absolute local paths in committed files.
- Dependencies are added deliberately, one at a time, with a stated reason.
- **A path check must still be valid at open time.** The import directory and
  everything below the data directory are untrusted, whatever user the process
  runs as. Checking a path and then opening it leaves a window in which the path
  can be swapped, so discovery and opening belong to one boundary: open the
  directory once, open entries relative to that descriptor without following
  symbolic links, and decide what a name refers to with `fstat` on the open
  file. Reads stay bounded, and an open must not be able to block indefinitely.
  The configured roots themselves are operator configuration and stay trusted.

## 10. Personal GPS data

GPS files are private movement data about real people.

- **Private movement data is created with restrictive permissions.** Directories
  TrackVault creates are `0700`, files `0600` -- the database, its journal files,
  the managed raw artifacts and the temporaries they are written through. The
  mode is stated to the call that creates the object, never applied afterwards,
  and never left to the umask. Existing files are reported, not chmodded: they
  may carry an operator's own access decision.

- Never commit personal GPX, FIT or TCX recordings, not even "temporarily".
- Never copy a private track into a public test fixture. Committed fixtures are
  synthetic or explicitly cleared, anonymised, and minimal.
- Never log GPS coordinates, EXIF or account data unless it is genuinely required,
  and never put raw payloads or coordinates into exception messages.
- Never commit personal import paths.
- Original imports stay byte-identical; parsing and normalisation never modify the
  source file.
- `*.gpx` is git-ignored by default, with an exception only for `tests/fixtures/`.
  Do not weaken that rule.
- `./import-tracks/` is the local reference directory: real private recordings and
  planned routes a developer keeps to explore against. The **whole directory** is
  git-ignored, not the filenames that happen to be in it today, so a sidecar file
  or a future FIT recording is covered too. It is optional -- nothing in the suite
  may require it to exist, and CI must never need it. Tests that read it carry the
  `local_tracks` marker and skip when it is absent. They are additional real
  regression evidence, never a substitute for a committed synthetic fixture, and
  no production rule may be tuned to make those two particular files come out a
  certain way. `tests/contract/test_private_data_contract.py` enforces the
  exclusion.

## 11. Source code language

Source code is English-only: identifiers, comments, docstrings, API field names,
error codes and commit messages. Reports and discussion with the project owner
are in German.

## 12. Comments and docstrings

- Docstrings explain contracts, invariants and the "why".
- Do not comment obvious code. No decorative banners, no commented-out code, no
  change logs in comments -- Git already records history.
- Public modules, classes and functions carry a docstring (enforced by Ruff `D`).

## 13. Git discipline

- Small, logical, English commits.
- Before every commit: `git status --short`, `git diff --check`, `git diff --stat`,
  and review every changed file.
- **No push without explicit instruction from the project owner.**
- Never run destructive Git commands: no `git reset --hard`, no `git clean -fd`,
  no force push, no history rewrite, no branch deletion on your own initiative.
- No co-author trailers.
- Never commit `.env`, `.venv`, `__pycache__`, tool caches, generated reports,
  local agent/tool data or personal GPX files.

## 14. Tooling rules

- `uv` owns dependencies and the environment. `uv.lock` is committed and must
  stay in sync (`uv lock --check`). Never edit the lock file by hand.
- Run tools through `uv run`. Do not install packages globally or with `pip`.
- Ruff is the only formatter and linter. mypy is the only type checker. pytest is
  the only test runner. Do not add a second tool for a job that already has one.
- GitNexus answers "what can this change affect?"; Serena answers "what is the
  working tree right now?". Both complement tests and reading the code -- they
  never replace them. Their local data is never committed.
- Use GitNexus impact/blast-radius analysis before changing an existing symbol
  that other code depends on, and `detect_changes` before committing. Re-index
  with `gitnexus analyze` when the index reports itself stale.
- `gitnexus analyze` appends a generated `<!-- gitnexus:start -->` block with its
  own do/don't list to `AGENTS.md` and `CLAUDE.md`. **Remove that block again.**
  It is a second rule source, which section 3 forbids; the agent rules contract
  test fails while it is present. GitNexus usage is documented here instead.
- `.gitnexus/`, `.serena/` and `.claude/skills/gitnexus/` are generated locally
  and stay git-ignored.

## 15. Validation and reporting

Before reporting a task as done, run and report the real results of:

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run python -m compileall -q src tests
git diff --check
```

- Report checks that were not executed explicitly as `not run`. Never claim a
  check passed that you did not run.
- If something failed, say so and show the output.
- Report what you skipped and why, instead of quietly reducing the scope.

## 16. Prohibited anti-patterns

Do not create:

- `utils.py` or any other grab-bag module
- a large `services.py`
- god objects
- business logic in FastAPI routes or in `main.py`
- database models used as the domain model
- format or vendor types used as the domain model
- more than one configuration system
- a second parsing or persistence path per input channel
- a duplicated copy of these agent rules
- a repository pattern for a database that does not exist yet
- abstract factories, importer managers or coordinator factories without a
  concrete need
- events or queues without a use case
- "future proof" code without a test or contract
- personal GPX data in the repository
- large comments that restate obvious code
