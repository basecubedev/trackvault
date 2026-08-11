"""Read the licence of every Python distribution the environment installs.

The counterpart of ``web/scripts/audit-licenses.mjs``, reading the same
``license-policy.json``: a licence decision is made once for the repository
rather than once per language.

It is an **engineering gate, not legal advice**. It answers one question -- "is
every licence here one this repository has already decided it accepts?" -- and
reports everything else for a human to read rather than claiming a verdict it is
not qualified to give.

```
allowed          on the repository's allowlist, or reviewed under this very licence
review required  a real licence, not on the list
relicensed       reviewed once, and no longer under the licence that was read
unknown          nothing stated at all
```

``relicensed`` exists because a manual exception is a statement about a
*licence*. Recorded against a package name alone it would survive that package
being relicensed, which is the one moment somebody needs to look again.

Metadata is read from the *installed* distributions rather than from
``pyproject.toml``, because what ends up in a container is what was resolved.
Older packaging metadata states a licence in a classifier instead of an SPDX
expression, so both are read and the expression wins where there is one.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

POLICY_FILE = Path(__file__).resolve().parents[1] / "license-policy.json"

CLASSIFIER_PREFIX = "License :: "
"""Where a distribution that predates SPDX metadata states its licence."""

_CLASSIFIER_SPDX = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "Python-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
}
"""Classifiers this project has actually met, mapped onto SPDX identifiers.

Deliberately small and deliberately not a general translation table. A
classifier is coarser than an expression -- "BSD License" names a family, not a
document -- so anything not listed here is reported as needing a look rather
than guessed at.
"""


@dataclass(frozen=True, slots=True)
class Package:
    """One installed distribution and what it says its licence is."""

    name: str
    version: str
    license: str | None


def installed_packages() -> list[Package]:
    """Return every installed distribution, sorted by name."""
    found: dict[str, Package] = {}
    for distribution in distributions():
        metadata = distribution.metadata
        name = metadata["Name"]
        if not name:
            continue
        found[name.lower()] = Package(
            name=name,
            version=distribution.version or "?",
            license=_declared(metadata),
        )
    return sorted(found.values(), key=lambda package: package.name.lower())


def _declared(metadata: object) -> str | None:
    """Return the SPDX expression a distribution states, if it states one."""
    get = getattr(metadata, "get", None)
    get_all = getattr(metadata, "get_all", None)
    if get is None or get_all is None:  # pragma: no cover - defensive
        return None
    expression = get("License-Expression")
    if isinstance(expression, str) and expression.strip():
        return expression.strip()
    for classifier in get_all("Classifier") or []:
        if not isinstance(classifier, str) or not classifier.startswith(CLASSIFIER_PREFIX):
            continue
        spdx = _CLASSIFIER_SPDX.get(classifier.rsplit(" :: ", 1)[-1])
        if spdx:
            return spdx
    legacy = get("License")
    if isinstance(legacy, str) and legacy.strip() and "\n" not in legacy.strip():
        return legacy.strip()
    return None


def verdict(expression: str | None, allowed: frozenset[str]) -> str:
    """Return what one licence expression amounts to under the policy.

    ``OR`` passes when either side does: a package offered under a choice may be
    taken under the one this repository accepts. ``AND`` requires every part,
    because both apply.
    """
    if not expression:
        return "unknown"
    cleaned = expression.replace("(", " ").replace(")", " ").strip()
    if " AND " in cleaned.upper():
        parts = [part.strip() for part in _split(cleaned, " AND ")]
        return "allowed" if all(part in allowed for part in parts) else "review"
    parts = [part.strip() for part in _split(cleaned, " OR ")]
    return "allowed" if any(part in allowed for part in parts) else "review"


def reviewed_licenses(policy: Mapping[str, Any]) -> dict[str, str]:
    """Return the licence each individually reviewed package was accepted under.

    Names are lowered because a distribution name is not case sensitive, and an
    exception that only applies to one spelling is an exception that stops
    applying when somebody writes the other one.
    """
    return {name.lower(): entry["license"] for name, entry in policy.get("reviewed", {}).items()}


def decide(package: Package, allowed: frozenset[str], reviewed: Mapping[str, str]) -> str:
    """Return what the policy makes of one package.

    A reviewed package is allowed **under the licence it was reviewed under**
    and under no other. That is the whole point of recording the licence beside
    the reason: the exception describes a document somebody read, so a package
    that now arrives under a different one has not been reviewed at all.
    """
    expected = reviewed.get(package.name.lower())
    if expected is None:
        return verdict(package.license, allowed)
    if package.license == expected:
        return "allowed"
    return "relicensed"


def _split(expression: str, separator: str) -> list[str]:
    """Split on a separator, ignoring case."""
    lowered = expression.upper()
    parts: list[str] = []
    start = 0
    while True:
        found = lowered.find(separator, start)
        if found < 0:
            parts.append(expression[start:])
            return parts
        parts.append(expression[start:found])
        start = found + len(separator)


def main() -> int:
    """Report the installed licences and fail on anything unreviewed."""
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    allowed = frozenset(policy["allowed"])
    reviewed = reviewed_licenses(policy)
    # This repository's own distributions are not third-party dependencies, and
    # auditing a project against a policy it wrote says nothing.
    own = {name.lower().replace("-", "_") for name in policy.get("self", [])}

    packages = [
        package
        for package in installed_packages()
        if package.name.lower().replace("-", "_") not in own
    ]
    counts: dict[str, int] = {}
    problems: list[tuple[Package, str]] = []
    for package in packages:
        counts[package.license or "unstated"] = counts.get(package.license or "unstated", 0) + 1
        decided = decide(package, allowed, reviewed)
        if decided != "allowed":
            problems.append((package, decided))

    lines = [f"{len(packages)} installed distributions"]
    for license_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  {count:>4}  {license_name}")
    if problems:
        lines.append("")
        lines.append("Not covered by the repository policy:")
        for package, decided in problems:
            stated = package.license or "unstated"
            note = (
                f" (reviewed as {reviewed[package.name.lower()]})"
                if decided == "relicensed"
                else ""
            )
            lines.append(f"  {decided:<10} {package.name}=={package.version} — {stated}{note}")
        lines.append("")
        lines.append("Add the identifier to license-policy.json only after reading the licence.")
    else:
        lines.append("")
        lines.append("Every installed distribution carries a licence this repository accepts.")

    sys.stdout.write("\n".join(lines) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
