"""One authority for what version of TrackVault a deployment is running.

A version that is stated in three places is stated wrong in at least one of
them, and the one nobody notices is the one an operator reads off a container
label while reporting a bug. ``pyproject.toml`` is the authority. Everything
else derives from it:

```
pyproject.toml           the version
trackvault.__version__   read from the installed distribution metadata
OpenAPI `info.version`   FastAPI reads the same value
image label              a build argument, checked against pyproject here
```

The browser page states no version of its own at all. ``web/package.json``
carries one because npm requires the field, and it describes the npm package
rather than the product -- so the page asks the archive instead.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"


def _project_version() -> str:
    """Return the one version this project declares."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    version: str = project["project"]["version"]
    return version


def test_the_package_version_is_the_project_version() -> None:
    """``trackvault.__version__`` resolves from the installed distribution."""
    from trackvault import __version__

    assert __version__ == _project_version()


def test_the_image_label_states_the_project_version() -> None:
    """A container label that describes another release is a label that misleads.

    The Dockerfile takes the version as a build argument so a release pipeline
    can pass one, and defaults it to what ``pyproject.toml`` says so an ordinary
    ``docker compose build`` is still labelled correctly. This is the check that
    keeps that default from becoming the second authority.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    declared = re.search(r"^ARG TRACKVAULT_VERSION=(.+)$", recipe, flags=re.MULTILINE)

    assert declared is not None, "the image recipe declares no version argument"
    assert declared.group(1).strip() == _project_version()
    assert 'org.opencontainers.image.version="${TRACKVAULT_VERSION}"' in recipe


def test_the_image_claims_no_metadata_the_build_cannot_know() -> None:
    """No empty placeholders.

    An ordinary build knows the title, the description and the version. It knows
    neither the commit it was built from nor the URL of the repository it came
    from, and a label stating either as an empty string is worse than no label:
    it looks answered.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "org.opencontainers.image.title" in recipe
    assert "org.opencontainers.image.revision" not in recipe
    assert "org.opencontainers.image.source" not in recipe


def test_the_openapi_document_states_the_project_version() -> None:
    """The schema the browser types are generated from names the same release."""
    from trackvault.main import create_app

    schema = create_app().openapi()

    assert schema["info"]["version"] == _project_version()
