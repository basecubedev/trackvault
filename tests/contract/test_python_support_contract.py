"""Contract for the declared Python support range.

The project supports exactly the Python version it is tested, type-checked and
shipped with. A wider ``requires-python`` would promise support for interpreter
versions no check in this repository ever exercises.

Every place that states the supported version -- packaging metadata, the pinned
developer interpreter, the linter and type-checker targets, the container base
image and CI -- has to express the same contract.
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SUPPORTED_MINOR = 13
REQUIRES_PYTHON = f">=3.{SUPPORTED_MINOR},<3.{SUPPORTED_MINOR + 1}"
PYTHON_VERSION_FILE = f"3.{SUPPORTED_MINOR}"
RUFF_TARGET = f"py3{SUPPORTED_MINOR}"
BASE_IMAGE_PREFIX = f"python:3.{SUPPORTED_MINOR}-"


def _pyproject() -> dict[str, object]:
    """Return the parsed project metadata."""
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _read(relative_path: str) -> str:
    """Return the text of a repository file."""
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.contract
def test_packaging_metadata_declares_a_bounded_support_range() -> None:
    """``requires-python`` names one supported minor version, not an open future."""
    project = _pyproject()["project"]
    assert isinstance(project, dict)

    assert project["requires-python"] == REQUIRES_PYTHON


@pytest.mark.contract
def test_the_running_interpreter_is_inside_the_supported_range() -> None:
    """The suite runs on exactly the interpreter the project claims to support."""
    assert sys.version_info[:2] == (3, SUPPORTED_MINOR)


@pytest.mark.contract
def test_the_pinned_developer_interpreter_matches_the_support_range() -> None:
    """``.python-version`` pins the same minor version uv installs and CI uses."""
    assert _read(".python-version").strip() == PYTHON_VERSION_FILE


@pytest.mark.contract
def test_linter_and_type_checker_target_the_supported_version() -> None:
    """Ruff and mypy judge the code against the version that is shipped."""
    tools = _pyproject()["tool"]
    assert isinstance(tools, dict)
    ruff = tools["ruff"]
    mypy = tools["mypy"]
    assert isinstance(ruff, dict)
    assert isinstance(mypy, dict)

    assert ruff["target-version"] == RUFF_TARGET
    assert mypy["python_version"] == PYTHON_VERSION_FILE


@pytest.mark.contract
def test_the_container_base_image_matches_the_support_range() -> None:
    """The runtime image ships the supported interpreter, not a floating one."""
    dockerfile = _read("Dockerfile")

    base_images = re.findall(r"^FROM\s+(python:\S+)", dockerfile, flags=re.MULTILINE)

    assert base_images, "the Dockerfile must build on an explicit Python base image"
    assert all(image.startswith(BASE_IMAGE_PREFIX) for image in base_images), base_images


@pytest.mark.contract
def test_ci_and_docker_use_the_same_pinned_uv_version() -> None:
    """A reproducible build needs one controlled uv version, not a floating tag."""
    workflow = _read(".github/workflows/ci.yml")
    dockerfile = _read("Dockerfile")

    ci_versions = re.findall(r'UV_VERSION:\s*"([^"]+)"', workflow)
    docker_versions = re.findall(r"ghcr\.io/astral-sh/uv:(\S+)", dockerfile)

    assert ci_versions, "CI must pin the uv version it installs"
    assert docker_versions, "the Dockerfile must pin the uv image it copies from"
    assert set(ci_versions) == set(docker_versions), (
        f"CI pins uv {ci_versions} while the Dockerfile pins {docker_versions}"
    )
    assert all(version.count(".") == 2 for version in docker_versions), (
        f"pin uv to an exact patch version, got {docker_versions}"
    )
