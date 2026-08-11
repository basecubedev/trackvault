"""Executable architecture contract.

The rules checked here mirror ``docs/technical/architecture.md``. Changing a layer
boundary means changing that document and this test in the same commit.

The check is a deliberately small AST import analysis -- no extra dependency is
introduced just to guard the architecture.
"""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
PACKAGE_ROOT = SRC_ROOT / "trackvault"

# Which ``trackvault.*`` modules each layer is allowed to import.
# `trackvault.release` is a leaf module holding the build's version string. Every
# layer but the domain may read it: a User-Agent and a system-info response both
# legitimately state which release is speaking. The domain may not -- a business
# rule that behaves differently in one release is not a business rule.
ALLOWED_INTERNAL_IMPORTS: dict[str, frozenset[str]] = {
    "domain": frozenset({"trackvault.domain"}),
    "application": frozenset({"trackvault.domain", "trackvault.application", "trackvault.release"}),
    "infrastructure": frozenset(
        {
            "trackvault.domain",
            "trackvault.application",
            "trackvault.infrastructure",
            "trackvault.config",
            "trackvault.release",
        }
    ),
    "api": frozenset(
        {
            "trackvault.domain",
            "trackvault.application",
            "trackvault.api",
            "trackvault.config",
            "trackvault.release",
        }
    ),
}

# Layers that may not use third-party packages at all.
STDLIB_ONLY_LAYERS = frozenset({"domain"})

LAYERS = tuple(sorted(ALLOWED_INTERNAL_IMPORTS))

# Modules the domain must never import. The standard library entries matter as much
# as the third-party ones: `xml`, `sqlite3` and `pathlib` are stdlib but they are
# infrastructure concerns, so the stdlib-only rule alone would not catch them.
FORBIDDEN_DOMAIN_IMPORTS = frozenset(
    {
        # HTTP and web framework
        "fastapi",
        "starlette",
        "pydantic",
        "pydantic_settings",
        "uvicorn",
        "httpx",
        "httpx2",
        "http",
        "urllib",
        "socket",
        # persistence
        "sqlalchemy",
        "alembic",
        "sqlite3",
        "psycopg",
        "asyncpg",
        "redis",
        # exchange formats and parsers
        "xml",
        "lxml",
        "defusedxml",
        "gpxpy",
        "fitparse",
        "fitdecode",
        "garmin_fit_sdk",
        "tcxreader",
        "simplekml",
        "geojson",
        "json",
        "csv",
        "yaml",
        # file system and process
        "os",
        "io",
        "pathlib",
        "shutil",
        "tempfile",
        "glob",
        "subprocess",
    }
)

# Concrete exchange-format parsers belong to infrastructure adapters only. A GPX or
# FIT type must never travel inwards past the normalization boundary.
FORMAT_MODULES = frozenset(
    {
        "xml",
        "lxml",
        "defusedxml",
        "gpxpy",
        "fitparse",
        "fitdecode",
        "garmin_fit_sdk",
        "tcxreader",
        "simplekml",
        "geojson",
    }
)

FORMAT_FREE_LAYERS = ("domain", "application", "api")

# Source applications are metadata, never business types. `if source == "locus"`
# style shortcuts are exactly what the classification contract forbids.
VENDOR_NAMES = frozenset(
    {
        "locus",
        "komoot",
        "garmin",
        "wahoo",
        "osmand",
        "gpslogger",
        "strava",
    }
)

SOURCE_AGNOSTIC_LAYERS = ("domain", "application", "api")

# Storage technology belongs to one package. Everything else speaks to it through
# the repository port.
PERSISTENCE_MODULES = frozenset({"sqlite3", "sqlalchemy", "alembic", "psycopg", "asyncpg"})

PERSISTENCE_PACKAGES = (
    "trackvault.infrastructure.database",
    "trackvault.infrastructure.maps.mbtiles",
)
"""Which modules may name the SQLite driver, and why there are two.

`infrastructure.database` owns the archive's own database. `maps.mbtiles` reads
a *foreign* container that happens to be SQLite -- a downloaded map package --
strictly read-only, through its own connections, and it never touches the
archive's file. That is a format adapter, and keeping it out of the database
package is what stops "the store" from meaning two things.
"""

# Exchange format names must not become type names outside the adapter that owns
# the format. The *values* of the public error codes deliberately do name a
# format -- "not GPX at all" and "broken GPX" are different problems for whoever
# has to fix the file -- but no class or function outside the adapter may.
FORMAT_NAMES = ("gpx", "fit", "tcx", "kml", "geojson", "xml")

FORMAT_ADAPTER_PACKAGES = ("trackvault.infrastructure.gpx",)

# Where the meaning of a vendor extension schema is decided. Naming concrete
# namespaces is what an adapter is for; knowing them anywhere else would make a
# vendor's vocabulary part of the business model.
EXTENSION_SEMANTICS_MODULE = "trackvault.infrastructure.gpx.extensions"

# An adapter observes; it must not be able to decide a business verdict. Not
# naming the types at all is a stronger guarantee than promising not to use them:
# a lookup table mapping a creator string to a kind would be just as forbidden as
# an `if`, and an AST check for `if source == "..."` would not catch it.
VERDICT_TYPES = ("TrackKind", "ClassificationResult", "TrackClassification")


def _module_name(path: Path) -> str:
    """Return the dotted module name of a file inside ``src/``."""
    parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative_import(path: Path, node: ast.ImportFrom) -> str:
    """Resolve a relative ``from ... import ...`` into an absolute module name."""
    parts = _module_name(path).split(".")
    if path.name != "__init__.py":
        parts.pop()
    parts = parts[: max(0, len(parts) - (node.level - 1))]
    if node.module:
        parts.append(node.module)
    return ".".join(parts)


def _imported_modules(path: Path) -> Iterator[str]:
    """Yield every module name imported by a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield _resolve_relative_import(path, node)
            elif node.module:
                yield node.module


def _layer_modules(layer: str) -> list[Path]:
    """Return every Python file belonging to a layer."""
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def _is_allowed(imported: str, allowed: frozenset[str]) -> bool:
    """Report whether an internal import is covered by an allowed prefix."""
    return any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in allowed)


def _root_module(imported: str) -> str:
    """Return the top-level package of a dotted module name."""
    return imported.split(".")[0]


def _vendor_hits(text: str) -> list[str]:
    """Return the source-application names that appear in a piece of text."""
    lowered = text.lower()
    return [vendor for vendor in sorted(VENDOR_NAMES) if vendor in lowered]


def _defined_names(tree: ast.AST) -> Iterator[str]:
    """Yield the names of every class and function defined in a module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name


def _branching_string_literals(tree: ast.AST) -> Iterator[str]:
    """Yield string literals a module makes decisions on.

    Covers comparisons (``==``, ``in``, ...) and ``match``/``case`` patterns, which
    is where a vendor shortcut would actually change behaviour.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for candidate in (node.left, *node.comparators):
                yield from _string_constants(candidate)
        elif isinstance(node, ast.MatchValue):
            yield from _string_constants(node.value)


def _string_constants(node: ast.expr) -> Iterator[str]:
    """Yield the string constants an expression holds, including inside literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        for element in node.elts:
            yield from _string_constants(element)


@pytest.mark.contract
@pytest.mark.parametrize("layer", LAYERS)
def test_layer_package_exists(layer: str) -> None:
    """Every documented layer exists as an importable package."""
    assert (PACKAGE_ROOT / layer / "__init__.py").is_file()


@pytest.mark.contract
@pytest.mark.parametrize("layer", LAYERS)
def test_layer_only_imports_allowed_internal_modules(layer: str) -> None:
    """A layer imports only the inner layers the architecture permits."""
    allowed = ALLOWED_INTERNAL_IMPORTS[layer]
    violations = [
        f"{_module_name(module)} imports {imported}"
        for module in _layer_modules(layer)
        for imported in _imported_modules(module)
        if imported.startswith("trackvault") and not _is_allowed(imported, allowed)
    ]
    assert not violations, (
        f"layer '{layer}' may only import {sorted(allowed)}; violations: {violations}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("layer", sorted(STDLIB_ONLY_LAYERS))
def test_stdlib_only_layer_has_no_third_party_imports(layer: str) -> None:
    """The domain stays technology-free and uses the standard library only."""
    violations = [
        f"{_module_name(module)} imports {imported}"
        for module in _layer_modules(layer)
        for imported in _imported_modules(module)
        if not imported.startswith("trackvault")
        and imported.split(".")[0] not in sys.stdlib_module_names
    ]
    assert not violations, f"layer '{layer}' must use the standard library only: {violations}"


@pytest.mark.contract
@pytest.mark.parametrize("forbidden", ["trackvault.api", "trackvault.infrastructure", "fastapi"])
def test_domain_does_not_import(forbidden: str) -> None:
    """The domain never reaches outwards to HTTP, adapters or FastAPI."""
    imports = {
        imported for module in _layer_modules("domain") for imported in _imported_modules(module)
    }
    assert not any(name == forbidden or name.startswith(f"{forbidden}.") for name in imports)


@pytest.mark.contract
def test_domain_imports_no_web_persistence_format_or_filesystem_module() -> None:
    """The domain knows no technology, not even via the standard library."""
    violations = [
        f"{_module_name(module)} imports {imported}"
        for module in _layer_modules("domain")
        for imported in _imported_modules(module)
        if _root_module(imported) in FORBIDDEN_DOMAIN_IMPORTS
    ]
    assert not violations, f"domain must stay technology-free: {violations}"


@pytest.mark.contract
@pytest.mark.parametrize("layer", FORMAT_FREE_LAYERS)
def test_exchange_format_parsers_stay_inside_infrastructure(layer: str) -> None:
    """A concrete parser type must not travel past the normalization boundary."""
    violations = [
        f"{_module_name(module)} imports {imported}"
        for module in _layer_modules(layer)
        for imported in _imported_modules(module)
        if _root_module(imported) in FORMAT_MODULES
    ]
    assert not violations, (
        f"layer '{layer}' must not know a concrete exchange format; "
        f"parsers belong in trackvault.infrastructure: {violations}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("layer", SOURCE_AGNOSTIC_LAYERS)
def test_no_vendor_specific_identifiers_outside_infrastructure(layer: str) -> None:
    """Source applications never become modules, classes or functions of their own."""
    violations = []
    for module in _layer_modules(layer):
        if hits := _vendor_hits(module.stem):
            violations.append(f"module {_module_name(module)} names {hits}")
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        violations.extend(
            f"{_module_name(module)}.{name} names {hits}"
            for name in _defined_names(tree)
            if (hits := _vendor_hits(name))
        )
    assert not violations, (
        f"layer '{layer}' must stay source-agnostic; a source application is "
        f"metadata, not a business type: {violations}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("layer", SOURCE_AGNOSTIC_LAYERS)
def test_no_branching_on_vendor_names_outside_infrastructure(layer: str) -> None:
    """No `if source == "locus"` shortcut: source metadata is evidence, not authority."""
    violations = []
    for module in _layer_modules(layer):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        violations.extend(
            f"{_module_name(module)} branches on {literal!r} ({hits})"
            for literal in _branching_string_literals(tree)
            if (hits := _vendor_hits(literal))
        )
    assert not violations, (
        f"layer '{layer}' must not decide business meaning from a source name: {violations}"
    )


@pytest.mark.contract
def test_application_does_not_import_api() -> None:
    """Use cases never depend on their HTTP projection."""
    imports = {
        imported
        for module in _layer_modules("application")
        for imported in _imported_modules(module)
    }
    assert not any(name.startswith("trackvault.api") for name in imports)


@pytest.mark.contract
def test_api_may_use_the_inner_layers() -> None:
    """The HTTP projection is permitted to call domain and application code."""
    allowed = ALLOWED_INTERNAL_IMPORTS["api"]
    assert {"trackvault.domain", "trackvault.application"} <= allowed


@pytest.mark.contract
def test_infrastructure_may_implement_inner_contracts() -> None:
    """Adapters are permitted to implement domain and application contracts."""
    allowed = ALLOWED_INTERNAL_IMPORTS["infrastructure"]
    assert {"trackvault.domain", "trackvault.application"} <= allowed
    assert not _is_allowed("trackvault.api", allowed)


@pytest.mark.contract
def test_composition_root_holds_no_business_logic() -> None:
    """``main`` only wires the application together."""
    main_module = PACKAGE_ROOT / "main.py"
    functions = [
        node.name
        for node in ast.parse(main_module.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert functions == ["create_app"]


def _package_of(module: Path) -> str:
    """Return the dotted package a module file belongs to."""
    return _module_name(module).rpartition(".")[0]


def _referenced_names(tree: ast.AST) -> Iterator[str]:
    """Yield every bare name and attribute name a module mentions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield alias.name


@pytest.mark.contract
def test_storage_technology_stays_inside_the_database_package() -> None:
    """Infrastructure owns SQLite, and one package inside it owns the driver."""
    violations = [
        f"{_module_name(module)} imports {imported}"
        for layer in LAYERS
        for module in _layer_modules(layer)
        for imported in _imported_modules(module)
        if _root_module(imported) in PERSISTENCE_MODULES
        and not _module_name(module).startswith(PERSISTENCE_PACKAGES)
    ]
    assert not violations, (
        f"only {list(PERSISTENCE_PACKAGES)} may know the storage driver: {violations}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("layer", FORMAT_FREE_LAYERS)
def test_no_format_specific_type_is_defined_outside_its_adapter(layer: str) -> None:
    """`GpxImporter` belongs to the GPX adapter; no inner layer defines its like."""
    violations = []
    for module in _layer_modules(layer):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        violations.extend(
            f"{_module_name(module)}.{name}"
            for name in _defined_names(tree)
            if any(token in name.lower() for token in FORMAT_NAMES)
        )
    assert not violations, (
        f"layer '{layer}' must not name an exchange format in a type: {violations}"
    )


@pytest.mark.contract
def test_a_format_adapter_cannot_decide_a_track_kind() -> None:
    """An adapter observes evidence; only the classifier reaches a verdict.

    The adapter does not mention the verdict types at all, which also rules out a
    hidden mapping table from a creator or namespace to a kind -- the failure mode
    an "no `if source == ...`" check would not catch.
    """
    violations = []
    for package in FORMAT_ADAPTER_PACKAGES:
        directory = SRC_ROOT / Path(*package.split("."))
        for module in sorted(directory.rglob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            violations.extend(
                f"{_module_name(module)} references {name}"
                for name in _referenced_names(tree)
                if name in VERDICT_TYPES
            )
    assert not violations, f"a format adapter must not reach a verdict: {violations}"


@pytest.mark.contract
def test_vendor_extension_semantics_stay_inside_the_format_adapter() -> None:
    """Only the adapter that owns a format may know what a vendor schema means.

    The adapter maps ``(namespace, element)`` onto evidence and activities, which
    is exactly its job. The moment an inner layer imports that table, a vendor's
    vocabulary has become part of the business model -- and the "no
    `if source == ...`" checks would not notice, because a namespace lookup is
    not a comparison against a creator string.
    """
    violations = [
        f"{_module_name(module)} imports {EXTENSION_SEMANTICS_MODULE}"
        for layer in LAYERS
        for module in _layer_modules(layer)
        for imported in _imported_modules(module)
        if imported == EXTENSION_SEMANTICS_MODULE
        and not _module_name(module).startswith(FORMAT_ADAPTER_PACKAGES[0])
    ]
    assert not violations, f"extension semantics belong to the format adapter: {violations}"


@pytest.mark.contract
def test_the_extension_semantics_module_belongs_to_the_adapter_package() -> None:
    """The table exists, and it exists where the format is understood."""
    module = SRC_ROOT / Path(*EXTENSION_SEMANTICS_MODULE.split(".")).with_suffix(".py")

    assert module.is_file(), f"{EXTENSION_SEMANTICS_MODULE} is missing"
    assert _module_name(module).startswith(FORMAT_ADAPTER_PACKAGES[0])


@pytest.mark.contract
def test_the_api_knows_no_concrete_repository() -> None:
    """Routes call use cases; which storage answers them is not their business."""
    imports = {
        imported for module in _layer_modules("api") for imported in _imported_modules(module)
    }
    assert not [name for name in imports if name.startswith("trackvault.infrastructure")]


@pytest.mark.contract
def test_every_adapter_package_is_reachable_only_through_infrastructure() -> None:
    """Adapters are wired in one place, so a second wiring cannot drift."""
    wiring = {
        _module_name(module)
        for layer in LAYERS
        for module in _layer_modules(layer)
        if any(
            imported.startswith("trackvault.infrastructure.gpx")
            or imported.startswith("trackvault.infrastructure.database")
            or imported.startswith("trackvault.infrastructure.filesystem")
            for imported in _imported_modules(module)
        )
    }
    assert all(name.startswith("trackvault.infrastructure") for name in wiring), wiring


ANALYSIS_PACKAGE = PACKAGE_ROOT / "domain" / "analysis"


@pytest.mark.contract
@pytest.mark.analysis
def test_the_analysis_package_exists_inside_the_domain() -> None:
    """Analysis is a business rule, not an adapter concern.

    Its home decides what it can reach: inside the domain, the stdlib-only and
    format-free rules apply to it automatically rather than by promise.
    """
    assert ANALYSIS_PACKAGE.is_dir()
    assert (ANALYSIS_PACKAGE / "__init__.py").is_file()


@pytest.mark.contract
@pytest.mark.analysis
def test_analysis_imports_no_exchange_format_and_no_outer_layer() -> None:
    """The source-agnostic guarantee, made structural rather than promised.

    A future FIT adapter must reach the same numbers as the GPX one, and the
    only way to be sure is that analysis cannot see either. It reads normalized
    geometry and nothing else -- no parser, no repository, no route, no
    settings.
    """
    for module in sorted(ANALYSIS_PACKAGE.rglob("*.py")):
        for imported in _imported_modules(module):
            root = _root_module(imported)
            assert root not in FORMAT_MODULES, f"{_module_name(module)} imports {imported}"
            assert not imported.startswith(
                (
                    "trackvault.infrastructure",
                    "trackvault.application",
                    "trackvault.api",
                    "trackvault.config",
                )
            ), f"{_module_name(module)} imports {imported}"


@pytest.mark.contract
@pytest.mark.analysis
def test_analysis_names_no_exchange_format_or_vendor() -> None:
    """A metric that mentions a format is a metric that will grow a special case.

    `distance` means the same thing whether the positions arrived as GPX, FIT or
    TCX. The day it does not, statistics stop being comparable across sources,
    which is the whole reason the normalization boundary exists.
    """
    for module in sorted(ANALYSIS_PACKAGE.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        defined = {name.lower() for name in _defined_names(tree)}
        assert not _vendor_hits(" ".join(defined)), _module_name(module)
        for name in defined:
            assert not any(name.startswith(fmt) or name.endswith(fmt) for fmt in FORMAT_NAMES), (
                f"{_module_name(module)} defines {name}"
            )


@pytest.mark.contract
@pytest.mark.analysis
def test_no_route_derives_a_metric_of_its_own() -> None:
    """Analysis has one authority, and a route is a projection.

    A distance computed in a route would be a second answer to a question that
    already has one -- and the answer nobody reruns when the algorithm changes.
    """
    for module in _layer_modules("api"):
        for imported in _imported_modules(module):
            assert imported != "trackvault.domain.analysis.analyze", _module_name(module)
            assert not imported.endswith((".distance", ".movement", ".elevation")), (
                f"{_module_name(module)} imports {imported}"
            )
