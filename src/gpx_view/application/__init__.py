"""Application layer: use cases and orchestration of domain rules.

Planned use cases are ``ImportTrack``, ``ListTracks``, ``GetTrack``,
``CalculateStatistics`` and ``OverrideClassification``. None is implemented yet.

Every import path -- web upload, watched import folder, future API import -- must
run through the single canonical ``ImportTrack`` use case. There must never be a
second parsing or persistence path (see ``docs/technical/architecture.md``,
"Single import authority").

This package may depend on the domain. It reaches external systems only through
explicit ports (``typing.Protocol``) that infrastructure adapters implement, and
it knows no concrete parser: no GPX, FIT or TCX type may appear here. It must not
import FastAPI, the API layer, or concrete infrastructure adapters.
"""
