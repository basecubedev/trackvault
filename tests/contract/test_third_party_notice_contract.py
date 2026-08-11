"""The third-party notice document may not drift from what is actually locked.

``docs/legal/third-party-notices.md`` is the canonical statement of what
TrackVault depends on, in which version, for what, and under which licence. It is
written by hand, which is exactly why it needs a gate: a dependency bump changes
the lock file and leaves the document stating last month's version, and nothing
about the result looks wrong.

Three facts are checked per direct dependency, and no more:

```
it exists                 the document names a package the lock file resolves
the version matches       what is documented is what is locked
the licence matches       what is documented is what the package declares
```

Transitive dependencies are deliberately *not* listed in the document -- a few
hundred rows nobody reads is a worse statement than none -- and they stay
covered by ``scripts/audit_licenses.py`` and ``web/scripts/audit-licenses.mjs``,
which read the whole resolved trees.

The policy's ``reviewed`` map gets its own check here, because a manually
reviewed exception that hangs on a package *name* survives that package being
relicensed. What was read was a licence, so the exception names one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOTICES = PROJECT_ROOT / "docs" / "legal" / "third-party-notices.md"
POLICY_FILE = PROJECT_ROOT / "license-policy.json"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
UV_LOCK = PROJECT_ROOT / "uv.lock"
NPM_MANIFEST = PROJECT_ROOT / "web" / "package.json"
NPM_LOCK = PROJECT_ROOT / "web" / "package-lock.json"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

PYTHON_RUNTIME_SECTION = "Python — runtime"
PYTHON_DEVELOPMENT_SECTION = "Python — development and test"
BROWSER_RUNTIME_SECTION = "Browser — runtime"
BROWSER_DEVELOPMENT_SECTION = "Browser — build and test"


@dataclass(frozen=True, slots=True)
class DocumentedDependency:
    """One row of a notice table."""

    package: str
    version: str
    purpose: str
    license: str


def _audit_module() -> ModuleType:
    """Load the licence audit script as a module.

    The script is the one owner of "what licence does this distribution
    declare?", and this test asks it rather than re-implementing the same
    metadata reading beside it.
    """
    specification = importlib.util.spec_from_file_location(
        "trackvault_license_audit", PROJECT_ROOT / "scripts" / "audit_licenses.py"
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _sections() -> dict[str, list[DocumentedDependency]]:
    """Parse every dependency table out of the notice document.

    Columns are located by their heading rather than by position, so adding a
    column to one table does not silently shift what this reads.
    """
    sections: dict[str, list[DocumentedDependency]] = {}
    heading: str | None = None
    columns: dict[str, int] | None = None

    for line in NOTICES.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped.removeprefix("## ").strip()
            columns = None
            continue
        if not stripped.startswith("|") or heading is None:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if columns is None:
            columns = {name.lower(): index for index, name in enumerate(cells)}
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if "package" not in columns or "version" not in columns:
            continue
        sections.setdefault(heading, []).append(
            DocumentedDependency(
                package=cells[columns["package"]].strip("`"),
                version=cells[columns["version"]],
                purpose=cells[columns["purpose"]],
                license=cells[columns["licence"]],
            )
        )
    return sections


def _documented(section: str) -> dict[str, DocumentedDependency]:
    """Return one section's rows, by package name."""
    rows = _sections().get(section, [])
    assert rows, f"the notice document has no dependency table under {section!r}"
    by_name: dict[str, DocumentedDependency] = {}
    for row in rows:
        assert row.package not in by_name, f"{row.package} is documented twice under {section!r}"
        by_name[row.package] = row
    return by_name


def _locked_python_versions() -> dict[str, str]:
    """Return every version ``uv.lock`` resolved, by distribution name."""
    locked = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return {package["name"].lower(): package["version"] for package in locked["package"]}


def _direct_python_dependencies() -> tuple[set[str], set[str]]:
    """Return the runtime and development distributions this project asks for."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    runtime = {_requirement_name(item) for item in project["project"]["dependencies"]}
    development = {_requirement_name(item) for item in project["dependency-groups"]["dev"]}
    return runtime, development


def _requirement_name(requirement: str) -> str:
    """Return the distribution a requirement string names."""
    name = requirement
    for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
        name = name.split(separator, 1)[0]
    return name.strip().lower()


def _declared_python_licenses() -> Mapping[str, str | None]:
    """Return what every installed distribution declares, by name."""
    audit = _audit_module()
    return {package.name.lower(): package.license for package in audit.installed_packages()}


def _npm_lock() -> Mapping[str, Mapping[str, object]]:
    """Return the resolved npm tree, by ``node_modules`` path."""
    lock = json.loads(NPM_LOCK.read_text(encoding="utf-8"))
    packages: Mapping[str, Mapping[str, object]] = lock["packages"]
    return packages


def _direct_npm_dependencies() -> tuple[set[str], set[str]]:
    """Return the browser's runtime and development packages."""
    manifest = json.loads(NPM_MANIFEST.read_text(encoding="utf-8"))
    return set(manifest["dependencies"]), set(manifest["devDependencies"])


def _resolved_npm(name: str) -> Mapping[str, object]:
    """Return what the lock file resolved for one package."""
    entry = _npm_lock().get(f"node_modules/{name}")
    assert entry is not None, f"{name} is not resolved by web/package-lock.json"
    return entry


# --- The document covers exactly the direct dependencies ----------------------


def test_every_direct_python_runtime_dependency_is_documented() -> None:
    """A runtime dependency nobody documented is one nobody decided to ship."""
    runtime, _ = _direct_python_dependencies()

    documented = set(_documented(PYTHON_RUNTIME_SECTION))

    assert runtime <= documented, f"undocumented runtime dependencies: {runtime - documented}"


def test_every_direct_python_development_dependency_is_documented() -> None:
    """The development table states what builds and tests the project."""
    _, development = _direct_python_dependencies()

    documented = set(_documented(PYTHON_DEVELOPMENT_SECTION))

    assert development <= documented, f"undocumented dev dependencies: {development - documented}"


def test_the_python_tables_document_nothing_that_is_not_a_direct_dependency() -> None:
    """A row for a dependency that is gone is a claim about a package nobody ships."""
    runtime, development = _direct_python_dependencies()

    documented = set(_documented(PYTHON_RUNTIME_SECTION)) | set(
        _documented(PYTHON_DEVELOPMENT_SECTION)
    )

    assert documented <= runtime | development, (
        f"documented but not a direct dependency: {documented - (runtime | development)}"
    )


def test_a_python_dependency_is_documented_in_the_table_it_belongs_to() -> None:
    """Runtime and development are different claims about what ships."""
    runtime, development = _direct_python_dependencies()

    assert set(_documented(PYTHON_RUNTIME_SECTION)) == runtime
    assert set(_documented(PYTHON_DEVELOPMENT_SECTION)) == development


def test_every_direct_browser_dependency_is_documented() -> None:
    """Everything bundled into the served assets is named."""
    runtime, development = _direct_npm_dependencies()

    assert set(_documented(BROWSER_RUNTIME_SECTION)) == runtime
    assert set(_documented(BROWSER_DEVELOPMENT_SECTION)) == development


# --- The document states what is actually locked ------------------------------


def test_documented_python_versions_are_the_versions_the_lock_file_resolved() -> None:
    """A documented version that is not the locked one describes another build."""
    locked = _locked_python_versions()

    for section in (PYTHON_RUNTIME_SECTION, PYTHON_DEVELOPMENT_SECTION):
        for name, row in _documented(section).items():
            assert name in locked, f"{name} is documented but not locked"
            assert row.version == locked[name], (
                f"{name} is documented as {row.version} and locked as {locked[name]}"
            )


def test_documented_python_licences_are_what_the_distributions_declare() -> None:
    """The licence in the document is the licence the package arrived under."""
    declared = _declared_python_licenses()

    for section in (PYTHON_RUNTIME_SECTION, PYTHON_DEVELOPMENT_SECTION):
        for name, row in _documented(section).items():
            assert name in declared, f"{name} is documented but not installed"
            assert row.license == declared[name], (
                f"{name} is documented as {row.license} and declares {declared[name]}"
            )


def test_documented_browser_versions_are_the_versions_the_lock_file_resolved() -> None:
    """``package.json`` states intent; the lock file states what is installed."""
    for section in (BROWSER_RUNTIME_SECTION, BROWSER_DEVELOPMENT_SECTION):
        for name, row in _documented(section).items():
            assert row.version == _resolved_npm(name)["version"], (
                f"{name} is documented as {row.version} "
                f"and locked as {_resolved_npm(name)['version']}"
            )


def test_documented_browser_licences_are_what_the_lock_file_records() -> None:
    """The npm lock records a licence per package, so nothing has to be guessed."""
    for section in (BROWSER_RUNTIME_SECTION, BROWSER_DEVELOPMENT_SECTION):
        for name, row in _documented(section).items():
            assert row.license == _resolved_npm(name)["license"], (
                f"{name} is documented as {row.license} "
                f"and resolves as {_resolved_npm(name)['license']}"
            )


def test_every_documented_dependency_states_a_purpose() -> None:
    """A dependency list without reasons is a list nobody can prune."""
    for section in (
        PYTHON_RUNTIME_SECTION,
        PYTHON_DEVELOPMENT_SECTION,
        BROWSER_RUNTIME_SECTION,
        BROWSER_DEVELOPMENT_SECTION,
    ):
        for name, row in _documented(section).items():
            assert row.purpose.strip(), f"{name} is documented without a purpose"


# --- A reviewed exception is bound to the licence it was granted for ----------


def test_a_reviewed_package_names_the_licence_it_was_reviewed_under() -> None:
    """A review reads a licence. It cannot be recorded against a name alone."""
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))

    for name, entry in policy["reviewed"].items():
        assert isinstance(entry, dict), f"{name} states a reason without a licence"
        assert entry["license"], f"{name} names no reviewed licence"
        assert entry["reason"].strip(), f"{name} names no reason"


def test_a_reviewed_exception_stops_applying_when_the_package_is_relicensed() -> None:
    """The gap this closes: an exception that survives a licence change.

    ``pathspec`` was accepted under MPL-2.0 after somebody read what MPL-2.0
    requires. The same package under AGPL-3.0 is a different decision, and a
    review recorded against the name alone would have gone on passing it.
    """
    audit = _audit_module()
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    allowed = frozenset(policy["allowed"])
    reviewed = audit.reviewed_licenses(policy)

    unchanged = audit.Package(name="pathspec", version="1.1.1", license="MPL-2.0")
    relicensed = audit.Package(name="pathspec", version="9.9.9", license="AGPL-3.0-only")

    assert audit.decide(unchanged, allowed, reviewed) == "allowed"
    assert audit.decide(relicensed, allowed, reviewed) == "relicensed"


def test_every_reviewed_package_still_declares_its_reviewed_licence() -> None:
    """The review in the policy describes the package that is installed today."""
    audit = _audit_module()
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    reviewed = audit.reviewed_licenses(policy)
    declared = _declared_python_licenses()
    resolved = _npm_lock()

    for name, expected in reviewed.items():
        installed = declared.get(name)
        if installed is None:
            entry = resolved.get(f"node_modules/{name}")
            installed = None if entry is None else str(entry.get("license"))
        assert installed is not None, f"{name} is reviewed but installed by neither ecosystem"
        assert installed == expected, (
            f"{name} was reviewed as {expected} and now declares {installed}"
        )


# --- The notices are a distribution artifact, not only documentation ----------


def test_the_image_recipe_ships_the_third_party_notices() -> None:
    """A notice nobody receives is not a notice.

    Whoever runs the container is who the attribution and licence statements are
    for, so the document travels with the runtime image rather than staying in a
    repository they may never see.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "docs/legal/third-party-notices.md" in recipe
    assert "/app/THIRD_PARTY_NOTICES.md" in recipe


def test_the_build_context_admits_the_notices() -> None:
    """A deny-everything ignore file makes shipping a file an explicit act."""
    ignored = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "!docs/legal/third-party-notices.md" in ignored
