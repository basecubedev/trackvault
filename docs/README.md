# Documentation

| Document | Purpose |
| --- | --- |
| [technical/architecture.md](technical/architecture.md) | Product definition, the `input format != domain model` invariant, layers and boundaries, canonical normalized track, raw imports, single import authority, single-source-of-truth table, deferred decisions |
| [technical/contracts.md](technical/contracts.md) | Business invariants: track kind, classification and user override, activity, metric provenance, actual vs. planned aggregates, source metadata, duplicates |
| [developer/agent-rules.md](developer/agent-rules.md) | Canonical rules for agents and contributors |
| [adr/0001-project-foundation.md](adr/0001-project-foundation.md) | Why Python, uv, FastAPI, layering, Docker, and what was deferred |
| [adr/0002-source-agnostic-track-model.md](adr/0002-source-agnostic-track-model.md) | Why GPX is an adapter and not the domain, and the resulting authority rules |

Three documents are protected by executable contract tests in `tests/contract/`:

- the agent rules — entry points must reference them and must not duplicate them,
- the architecture boundaries — checked with an AST import and identifier analysis,
- `architecture.md` and `contracts.md` — checked for the invariants the code relies
  on, by topic anchor rather than by full-text snapshot.
