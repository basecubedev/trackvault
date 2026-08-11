"""One authority for what release a build is: the Git tag it was built from.

A version stated in the repository *and* in a tag is stated wrong in one of
them the first time somebody tags without editing the file, and the one nobody
notices is the one an operator reads off a container label while reporting a
bug. So the repository states no version at all. The tag is the version, and
everything else derives from it:

```
git tag v1.2.3            the version
distribution metadata     hatch-vcs writes it while the wheel is built
trackvault.__version__    read back from the installed distribution
OpenAPI `info.version`    FastAPI reads the same value
image label               the argument the build was given
```

A build that cannot see the tags is *told* the version instead of guessing one.
The image build is exactly that case -- its context deliberately carries no
`.git` -- so one build argument feeds both the distribution metadata and the
label, and the two cannot disagree about which release they describe.

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

# What an image build labels itself with when nobody told it which release it
# is. A plausible-looking number here would be a label that reads as answered.
UNKNOWN_VERSION = "0.0.0+unknown"


def _pyproject() -> dict[str, object]:
    """Return the parsed project file."""
    parsed: dict[str, object] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return parsed


def _version_argument_default() -> str:
    """Return the version the image recipe falls back to."""
    recipe = DOCKERFILE.read_text(encoding="utf-8")
    declared = re.search(r"^ARG TRACKVAULT_VERSION=(.+)$", recipe, flags=re.MULTILINE)

    assert declared is not None, "the image recipe declares no version argument"
    return declared.group(1).strip()


def test_the_repository_states_no_version_of_its_own() -> None:
    """A number in the file is a second authority, and tagging forgets it.

    This is the whole point of the arrangement: there is nothing to keep in
    sync, because there is only one statement of the version anywhere.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)

    assert "version" not in project, "pyproject.toml states a version beside the tag"
    assert "version" in project["dynamic"]


def test_the_version_is_read_out_of_the_version_control_tag() -> None:
    """The build backend resolves it, so a release is `git tag` and nothing else."""
    parsed = _pyproject()
    build_system = parsed["build-system"]
    hatch = parsed["tool"]
    assert isinstance(build_system, dict)
    assert isinstance(hatch, dict)

    assert any("hatch-vcs" in requirement for requirement in build_system["requires"])
    assert hatch["hatch"]["version"]["source"] == "vcs"


def test_the_package_reports_the_version_the_distribution_was_built_with() -> None:
    """``trackvault.__version__`` answers, and answers something real.

    What is asserted is not a particular number -- that changes with every
    release, which is the point -- but that the value survives the trip from
    the tag through the build into the installed metadata.
    """
    from trackvault import __version__

    assert re.fullmatch(r"\d+(\.\d+)*([._a-zA-Z0-9+-]*)", __version__), __version__


def test_the_openapi_document_states_the_release_this_build_is() -> None:
    """The schema the browser types are generated from names the same release."""
    from trackvault import __version__
    from trackvault.main import create_app

    schema = create_app().openapi()

    assert schema["info"]["version"] == __version__


def test_the_image_build_is_told_the_version_it_cannot_look_up() -> None:
    """The build context carries no `.git`, so the recipe hands the version in.

    Without this the wheel build inside the image has no tag to read and fails
    outright. `SETUPTOOLS_SCM_PRETEND_VERSION` is the documented way to state
    one, and it is fed from the *same* argument that writes the label below --
    one value in, two surfaces out.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "SETUPTOOLS_SCM_PRETEND_VERSION=${TRACKVAULT_VERSION}" in recipe


def test_the_distribution_is_built_rather_than_taken_from_a_cache() -> None:
    """A wheel is cached under the sources it was built from, version excluded.

    uv keys a wheel it built on the source tree, and the version is not in the
    source tree -- it arrives through the environment. So a second build of the
    same sources under a different version is handed the first build's wheel,
    and the image ships a distribution stating a release it is not while the
    label above states the right one. The two surfaces this file exists to keep
    identical would be describing different releases again, one layer deeper.

    It is not hypothetical: the argument-less `docker build` the CI job runs
    before the container tests filled that cache with an `0.0.0+unknown` wheel,
    and the tests then got it back instead of the build they asked for.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    installs = [
        line
        for line in recipe.splitlines()
        if "uv sync" in line and "--no-install-project" not in line
    ]

    assert installs, "no layer installs the project"
    for line in installs:
        assert "--no-cache" in line, line


def test_the_image_label_states_the_version_the_build_was_given() -> None:
    """A container label that describes another release is a label that misleads."""
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert 'org.opencontainers.image.version="${TRACKVAULT_VERSION}"' in recipe


def test_a_build_that_was_not_told_a_version_says_so() -> None:
    """An ordinary `docker compose build` knows no tag, and admits it.

    The alternative is a default that names some release the image is not, and
    an operator has no way to tell that apart from the truth.
    """
    assert _version_argument_default() == UNKNOWN_VERSION


def test_the_image_claims_no_metadata_the_build_cannot_know() -> None:
    """No empty placeholders.

    An ordinary build knows the title, the description and the version it was
    given. It knows neither the commit it was built from nor the URL of the
    repository it came from, and a label stating either as an empty string is
    worse than no label: it looks answered.
    """
    recipe = DOCKERFILE.read_text(encoding="utf-8")

    assert "org.opencontainers.image.title" in recipe
    assert "org.opencontainers.image.revision" not in recipe
    assert "org.opencontainers.image.source" not in recipe
