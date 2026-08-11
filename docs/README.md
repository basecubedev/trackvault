# Documentation

| Document | Purpose |
| --- | --- |
| [technical/architecture.md](technical/architecture.md) | Product definition, the `input format != domain model` invariant, layers and boundaries, canonical normalized track, raw imports and processing provenance, the GPX adapter, persistence and managed raw storage, single import authority and input paths, logging, single-source-of-truth table, deferred decisions |
| [technical/contracts.md](technical/contracts.md) | Business invariants: track kind, classification rules and user override, evidence codes, activity, metric provenance, actual vs. planned aggregates, source metadata, duplicates, import limits and error codes, time |
| [developer/agent-rules.md](developer/agent-rules.md) | Canonical rules for agents and contributors |
| [developer/development.md](developer/development.md) | Setup, running it locally, the test suites and quality gates, API type generation, the licence audits, the projection benchmark |
| [adr/0001-project-foundation.md](adr/0001-project-foundation.md) | Why Python, uv, FastAPI, layering, Docker, and what was deferred |
| [adr/0002-source-agnostic-track-model.md](adr/0002-source-agnostic-track-model.md) | Why GPX is an adapter and not the domain, and the resulting authority rules |
| [adr/0003-import-and-persistence-model.md](adr/0003-import-and-persistence-model.md) | The first production import slice: raw imports vs. processing runs, the normalized model, evidence-based classification, SQLite, managed raw storage, one import use case |
| [adr/0004-processing-generations-and-candidate-identity.md](adr/0004-processing-generations-and-candidate-identity.md) | Stable candidate identity, the current normalized generation, explicit reprocessing, verified raw storage, and why an external link decides no track kind |
| [adr/0005-processing-currency-and-local-filesystem-authority.md](adr/0005-processing-currency-and-local-filesystem-authority.md) | The processing profile and `--outdated`, raw-import integrity and repair, namespace-aware extension schemas, the import-directory open boundary, and permissions for private data |
| [adr/0006-track-analysis-and-statistics.md](adr/0006-track-analysis-and-statistics.md) | The analysis layer: source-agnostic metric derivation, the `AnalysisProfile`, the four statements about time, actual/planned/unknown aggregation and the aggregation timezone |
| [adr/0007-temporal-evidence-and-analysis-integrity.md](adr/0007-temporal-evidence-and-analysis-integrity.md) | Temporal evidence and actual-timing eligibility, the movement and elevation algorithm corrections, full time attribution, statistics that never mix profiles, validated persisted analysis, and the bounded track listing |
| [adr/0008-read-authority-calendar-and-track-profiles.md](adr/0008-read-authority-calendar-and-track-profiles.md) | One availability answer across every read surface, timeline time vs. activity calendar time, the two-scale movement rule, the track profile and its bounded projections, and the browser application |
| [adr/0009-user-owned-metadata-and-product-read-model.md](adr/0009-user-owned-metadata-and-product-read-model.md) | User-owned titles and notes, the archive's own calendar, deterministic map hover at a self-crossing, measured large-track cost and why there is no cache, and the licence statements that ship with the image |
| [legal/third-party-notices.md](legal/third-party-notices.md) | The canonical third-party dependency list, the licence policy and the audits that keep it honest, and what the map does and does not ship |

Three documents are protected by executable contract tests in `tests/contract/`:

- the agent rules — entry points must reference them and must not duplicate them,
- the architecture boundaries — checked with an AST import and identifier analysis,
- `architecture.md` and `contracts.md` — checked for the invariants the code relies
  on, by topic anchor rather than by full-text snapshot.
