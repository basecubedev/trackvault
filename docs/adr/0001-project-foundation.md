# ADR 0001: Project foundation

## Status

Accepted (2026-08-08)

## Context

GPX-View will become a local web application for managing, analysing and
visualising planned and recorded GPS tracks. The long-term feature set (GPX
import from Locus Map and other sources, recorded/planned separation, activity
handling, monthly and yearly statistics, maps, elevation and speed profiles) is
known in outline, but almost none of the technical detail is settled.

The risk in a project like this is not too little technology, it is committing
early to a parser, a schema or a frontend and then bending the business rules
around that choice. At the same time, a throwaway prototype would not survive the
number of features planned.

We therefore need a foundation that is small, reproducible and testable, with
explicit boundaries that later decisions can be plugged into.

## Decision

1. **Python 3.13** as the implementation language, with `src/` layout and the
   package name `gpx_view`. The declared support range is bounded
   (`>=3.13,<3.14`): the project supports exactly the interpreter it is tested,
   type-checked and shipped with, and widening the range is a deliberate decision
   with its own CI matrix, not a side effect.
2. **uv** for dependency and environment management. `uv.lock` is committed and
   verified in CI with `uv lock --check`.
3. **FastAPI + Uvicorn** as the HTTP runtime, kept to a single `GET /healthz`
   endpoint for now.
4. **`src/` layout** so that tests run against the installed package rather than
   accidental relative imports.
5. **Layered architecture** (`domain`, `application`, `infrastructure`, `api`,
   plus `main` as composition root) with boundaries enforced by an AST-based
   contract test rather than by convention.
6. **Contract-first development**: define behaviour, write the failing contract
   test, implement the smallest correct solution, keep the test.
7. **Docker-first deployability**: `Dockerfile`, `compose.yaml` and
   `.dockerignore` exist from the first commit, with a non-root runtime user and
   a healthcheck against `/healthz`.
8. **SQLite** is the currently intended first persistence backend, but no schema,
   no ORM and no migration tool are introduced yet.
9. **Frontend technology is deliberately deferred.** No JavaScript framework and
   no map library are chosen.
10. **GPX parser technology is deliberately deferred.** No parsing library is
    added until the import contract is defined.
11. **Canonical agent rules** live in `docs/developer/agent-rules.md`. `AGENTS.md`,
    `CLAUDE.md` and `.github/copilot-instructions.md` are entry points only, which
    a contract test enforces.

## Consequences

Positive:

- Business rules can be written and tested without a database, a parser or a UI.
- The architecture contract fails loudly when a layer boundary is crossed, so the
  design cannot erode quietly.
- The project is reproducible from a lock file and buildable as a container from
  day one, which makes CI and later deployment uneventful.
- Deferred decisions stay genuinely open, because no "preparation" code has been
  written against a guessed interface.

Negative / accepted costs:

- The `application` and `infrastructure` packages currently contain only their
  documented boundary. That is intentional emptiness, not an oversight.
- Contract tests for documentation (agent rules, architecture) add a small
  maintenance cost when the documents are restructured.
- Deferring persistence means the `/data` volume exists without a consumer. The
  path is reserved so that it does not have to change later.
- FastAPI and Pydantic are runtime dependencies before any real endpoint exists.
  Accepted, because the health endpoint has to prove the whole chain from package
  to container.

## Alternatives considered

- **No layering, single module.** Rejected: recorded/planned separation and
  provenance tracking are exactly the kind of rules that get lost in HTTP handlers.
- **Choosing SQLAlchemy and a schema now.** Rejected: the normalized track model
  is not designed yet, and a schema would become the de facto domain model.
- **Poetry or pip-tools instead of uv.** Rejected: uv gives a single tool for
  Python version, environment, lock file and script execution, which keeps both
  the container build and CI short.
