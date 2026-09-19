"""Every set of dependencies this repository locks is watched for advisories.

A lock file is a promise about what gets installed, and a promise nobody
re-reads goes stale quietly. The archive has three of them -- the browser's
`package-lock.json`, the Python `uv.lock`, and the actions the workflows pin --
and `.github/dependabot.yml` is what reads them every week.

The failure this guards against is not a missed advisory. It is a *fourth*
manifest: somebody adds a tool with a lock file of its own, Dependabot never
hears about it, and nothing anywhere reports that it is unwatched. The config
looks complete because it is complete for the three that existed when it was
written.

So the two directions are checked against each other:

```
every manifest    is named by an entry
every entry       names a directory that has that manifest
```

The second half matters as much as the first. An entry pointing at a directory
that no longer holds a manifest is a watch that silently covers nothing, and it
reads exactly like a watch that works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT = PROJECT_ROOT / ".github" / "dependabot.yml"

# What each ecosystem's manifest is called. Dependabot is told a directory; this
# is how the directory is recognised as holding that kind of dependency.
MANIFESTS = {
    "npm": "package.json",
    "uv": "uv.lock",
    "github-actions": ".github/workflows",
}

# Directories that carry manifests belonging to somebody else. `node_modules`
# holds a `package.json` per installed package, and watching those would be
# watching the lock file's own output.
IGNORED = {"node_modules", ".git", ".venv", "dist", "__pycache__"}


def _config() -> dict[str, object]:
    """Return the parsed Dependabot configuration."""
    return yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))


def _updates() -> list[dict[str, object]]:
    """Return the configured update entries."""
    updates = _config()["updates"]
    assert isinstance(updates, list)
    return updates


def _watched() -> set[tuple[str, str]]:
    """Return every (ecosystem, directory) pair the configuration watches."""
    return {(str(entry["package-ecosystem"]), str(entry["directory"])) for entry in _updates()}


def _present() -> set[tuple[str, str]]:
    """Return every (ecosystem, directory) pair this repository actually has."""
    found: set[tuple[str, str]] = set()
    for ecosystem, manifest in MANIFESTS.items():
        for path in PROJECT_ROOT.rglob(manifest):
            relative = path.relative_to(PROJECT_ROOT)
            if IGNORED & set(relative.parts):
                continue
            # The workflows live in a fixed place; Dependabot is told the
            # repository root for them, not the directory they are found in.
            parts = () if manifest == ".github/workflows" else relative.parent.parts
            found.add((ecosystem, "/" + "/".join(parts)))
    return found


def test_the_configuration_parses() -> None:
    """A file Dependabot cannot read is a watch that never runs."""
    assert _config()["version"] == 2
    assert _updates(), "no ecosystem is watched at all"


def test_every_locked_ecosystem_is_watched() -> None:
    """A manifest nobody watches reports no advisory, which reads like none."""
    unwatched = _present() - _watched()

    assert not unwatched, f"these hold dependencies nobody watches: {sorted(unwatched)}"


def test_every_watch_points_at_something_that_exists() -> None:
    """A watch on an empty directory covers nothing and looks like it covers."""
    stale = _watched() - _present()

    assert not stale, f"these are watched but hold no such manifest: {sorted(stale)}"


def test_no_group_carries_a_major_upgrade() -> None:
    """A major is a change to the code, so it arrives on its own to fail on its own.

    Grouped with a dozen patch bumps, a breaking upgrade is one line in a diff
    nobody reads line by line -- and the CI failure it causes is attributed to
    the group rather than to the package that broke.
    """
    for entry in _updates():
        groups = entry.get("groups") or {}
        assert isinstance(groups, dict)
        for name, group in groups.items():
            types = group.get("update-types")
            assert types, f"group '{name}' restricts nothing and would swallow majors"
            assert "major" not in types, f"group '{name}' hides a major among version bumps"
