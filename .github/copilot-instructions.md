# Copilot instructions

**The canonical rules live in [`../docs/developer/agent-rules.md`](../docs/developer/agent-rules.md).
Read that file before making any change.** This page is an entry point only and
deliberately does not repeat the rules.

Non-negotiables, in short:

- Work contract-first: define the behaviour, write the failing test, then implement.
- One owner per concern; everything else is a projection (single source of truth).
- Respect the layer boundaries in [`../docs/technical/architecture.md`](../docs/technical/architecture.md).
- Personal GPS data never enters the repository.
- **No push without explicit instruction**, and no destructive Git commands.

Python 3.13, `uv`, FastAPI. Run tools through `uv run`. Source code, comments and
commit messages are English.
