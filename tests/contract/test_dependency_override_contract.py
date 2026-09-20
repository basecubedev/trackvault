"""An override is only a fix if the lock file resolves what it claims to force.

An `overrides` entry in `web/package.json` exists for one reason: a package deep
in the tree pins a version this repository will not install, and the pin belongs
to somebody else's manifest. The entry rewrites that decision.

What makes it worth a gate is how it fails. An override that stops taking
effect -- because the dependent moved, because a nested copy reappeared, because
the entry names a package nobody depends on any more -- removes nothing and
reports nothing. The line is still in the manifest, the reason for it is still
in the commit message, and the vulnerable version is installed again. Unlike a
missing dependency, it does not break a build; it just quietly stops being true.

So the entry and the tree are checked against each other:

```
every override    names a package the tree actually installs
every override    is the version that dependent resolves
```

Ranges are refused on purpose. Every dependency here is pinned exactly, and an
override is the one place where a version is chosen *against* the declaring
package's own judgement -- the version that was tried is the version that gets
installed, not whatever the range admits later.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NPM_MANIFEST = PROJECT_ROOT / "web" / "package.json"
NPM_LOCK = PROJECT_ROOT / "web" / "package-lock.json"

# An exact version and nothing else: no `^`, no `~`, no range, no tag.
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class Override:
    """One forced version, and the package whose choice it replaces.

    `dependent` is `None` for an override that applies to the whole tree.
    """

    dependent: str | None
    package: str
    version: str

    def __str__(self) -> str:
        """Return the entry as it reads in the manifest."""
        return f"{self.dependent}/{self.package}" if self.dependent else self.package


def _overrides() -> list[Override]:
    """Return every override the browser manifest declares, in either form."""
    manifest = json.loads(NPM_MANIFEST.read_text(encoding="utf-8"))
    declared = manifest.get("overrides") or {}

    forced: list[Override] = []
    for name, value in declared.items():
        if isinstance(value, str):
            forced.append(Override(dependent=None, package=name, version=value))
            continue
        # The scoped form: `{"<dependent>": {"<package>": "<version>"}}`.
        for package, version in value.items():
            forced.append(Override(dependent=name, package=package, version=version))
    return forced


def _installed() -> dict[str, str]:
    """Return every installed path in the lock file and the version it holds."""
    lock = json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    return {
        path: str(entry["version"])
        for path, entry in lock["packages"].items()
        if path and "version" in entry
    }


def _paths_of(package: str, installed: dict[str, str]) -> list[str]:
    """Return every place the tree installs `package`, hoisted or nested."""
    suffix = f"node_modules/{package}"
    return [path for path in installed if path == suffix or path.endswith(f"/{suffix}")]


def _lookup_order(dependent: str) -> Iterator[str]:
    """Yield the directories Node searches from `dependent` outwards.

    Node walks up from the package's own directory, looking into the
    `node_modules` of each ancestor, which is how a hoisted copy at the root
    serves a package nested three levels down.
    """
    parts = dependent.split("/")
    while parts:
        yield "/".join(parts)
        parts.pop()
    yield ""


def _resolves(dependent: str, package: str, installed: dict[str, str]) -> str | None:
    """Return the version `dependent` would load for `package`, or `None`."""
    for directory in _lookup_order(dependent):
        candidate = (
            f"{directory}/node_modules/{package}" if directory else f"node_modules/{package}"
        )
        if candidate.endswith(f"node_modules/node_modules/{package}"):
            continue
        if candidate in installed:
            return installed[candidate]
    return None


def test_every_override_pins_an_exact_version() -> None:
    """A range lets the forced version drift away from the one that was tried."""
    loose = [str(forced) for forced in _overrides() if not EXACT_VERSION.match(forced.version)]

    assert not loose, f"these override with a range rather than a version: {sorted(loose)}"


def test_every_override_names_a_package_the_tree_installs() -> None:
    """An override nobody reaches is a fix that reads as applied and is not."""
    installed = _installed()

    unreachable = [
        str(forced)
        for forced in _overrides()
        if not _paths_of(forced.package, installed)
        or (forced.dependent and not _paths_of(forced.dependent, installed))
    ]

    assert not unreachable, f"these override something not installed: {sorted(unreachable)}"


def test_every_override_is_the_version_the_lock_file_resolves() -> None:
    """The forced version has to be the one the dependent actually loads."""
    installed = _installed()

    ignored: list[str] = []
    for forced in _overrides():
        dependents = _paths_of(forced.dependent, installed) if forced.dependent else [""]
        for dependent in dependents:
            resolved = _resolves(dependent, forced.package, installed)
            if resolved != forced.version:
                ignored.append(f"{dependent or '(root)'} loads {forced.package} {resolved}")

    assert not ignored, f"these resolve past their override: {sorted(ignored)}"
