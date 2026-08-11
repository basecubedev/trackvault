# Agent rules

This file is the **canonical and only complete rule source** for automated agents
and human contributors working on GPX-View.

`AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md` are short entry
points. They point here and must never grow into a second copy of these rules.
`tests/contract/test_agent_rules_contract.py` enforces that.

GPX-View is a self-hosted, source-agnostic activity and route archive for recorded
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
| Calculated statistics | the analysis layer, from the canonical normalized track |
| Actual aggregates | tracks whose effective kind is `RECORDED` |
| Planned aggregates | tracks whose effective kind is `PLANNED` |
| Source metadata | evidence and provenance information, never business authority |
| Import | the single canonical `ImportTrack` use case |
| HTTP representation | projection only |
| Browser/frontend state | projection only |
| Configuration | `gpx_view.config` backend settings |

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

## 6. Architecture boundaries

Layers, inner to outer: `domain` -> `application` -> `infrastructure` / `api`.

- `gpx_view.domain` uses the Python standard library only, and not its
  infrastructure corners either: no `xml`, `sqlite3`, `pathlib`, `os`, `json`,
  `csv`, `http` or `urllib`. No FastAPI, database, Docker, GPX/FIT parser or
  frontend imports. No vendor-specific classes, and no branching on vendor name
  literals.
- `gpx_view.application` may use the domain. External systems are reached only
  through explicit ports (`typing.Protocol`). It knows no concrete parser and must
  not import FastAPI, the API layer or concrete adapters.
- `gpx_view.infrastructure` holds concrete adapters and is the only layer that
  knows formats and storage. It implements the contracts of the inner layers and
  must not import the API layer.
- `gpx_view.api` is the HTTP projection: validate, call a use case, project the
  result. No classification or analysis heuristic in a route.
- A future browser UI is projection only. Browser state is never business
  authority.
- `gpx_view.main` is the composition root. Bootstrap only, never business logic.

`tests/contract/test_architecture_contract.py` enforces these boundaries with an
AST check. If a boundary genuinely has to change, change the documentation and the
contract test in the same commit, and say so.

## 7. Test isolation

- Tests must pass with no internet, no external services, no real GPS files and
  no Docker.
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

## 10. Personal GPS data

GPS files are private movement data about real people.

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
