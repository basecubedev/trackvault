"""What this product is called, what it is licensed under, and where it listens.

Three statements a user meets before they meet any feature, and all three are
spread across files that have no other reason to agree with each other -- a
Dockerfile, a shell installer, a compose file, a React header, an OpenAPI
document. That is exactly the shape of thing that drifts silently, so it is
checked here rather than noticed by somebody reading a stale page.

```
name      TrackVault: the product, the package, the command and the image.
licence   one LICENSE file, and every declaration derives from it.
port      8081 published on the host, 8080 inside the container.
```

The name check is repository-wide on purpose. There is one name and no alias
beside it, so a surviving identifier from the earlier name is not compatibility
-- it is a reference to something that does not exist.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_NAME = "TrackVault"
DISTRIBUTION_NAME = "trackvault"
LICENSE_IDENTIFIER = "AGPL-3.0-only"
HOST_PORT = "8081"
CONTAINER_PORT = "8080"

FORMER_NAME = re.compile(rb"gpx[-_ ]?view(?!er)", re.IGNORECASE)
"""Every spelling the project used before it was named, in one pattern.

Product name, distribution, module path, console script, image, compose service
and environment prefix all derived from it, and all of them changed together --
so unlike a rename that keeps its identifiers, this one has nothing left to
exempt. ``GPX viewer`` is excluded because that is a sentence about what this
is not, and ``GPX`` on its own is an exchange format that stays.
"""

USER_FACING_FILES = (
    "README.md",
    "Dockerfile",
    "compose.yaml",
    ".env.example",
    "install-docker.sh",
    ".github/workflows/release.yml",
    "web/index.html",
    "web/package.json",
    "web/src/app/Header.tsx",
    "web/src/app/About.tsx",
    "web/src/pages/Credits/Credits.tsx",
)


def _read(relative_path: str) -> str:
    """Return the text of a repository file."""
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _pyproject() -> dict[str, object]:
    """Return the parsed project table."""
    table: dict[str, object] = tomllib.loads(_read("pyproject.toml"))["project"]
    return table


def _tracked_files() -> list[str]:
    """Return every path Git tracks, or skip: this contract is about the repository."""
    executable = shutil.which("git")
    if executable is None or not (PROJECT_ROOT / ".git").exists():
        pytest.skip("not a git working tree")
    listing = subprocess.run(  # noqa: S603 - the executable is resolved, the arguments are literals
        [executable, "-C", str(PROJECT_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [path for path in listing.stdout.split("\0") if path]


# --- The product has one name -------------------------------------------------


@pytest.mark.parametrize("relative_path", USER_FACING_FILES)
def test_no_user_facing_file_still_shows_the_former_product_name(relative_path: str) -> None:
    """Every surface a person reads says TrackVault."""
    found = FORMER_NAME.findall(_read(relative_path).encode("utf-8"))

    assert not found, f"{relative_path} still shows {set(found)}"


def test_no_versioned_file_anywhere_still_refers_to_the_former_name() -> None:
    """One name, and no identifier left pointing at the one before it.

    Nothing was kept for compatibility, so anything matching here is a stale
    reference rather than a deliberate exception -- an import that will not
    resolve, an environment variable nothing reads, an image nobody publishes.
    """
    offenders = {
        path for path in _tracked_files() if FORMER_NAME.search((PROJECT_ROOT / path).read_bytes())
    }

    assert not offenders, f"the former name survives in {sorted(offenders)}"


def test_the_local_tool_configuration_names_the_project_after_the_product() -> None:
    """The one project identity that lives outside the versioned tree.

    ``.serena/project.yml`` is generated locally and git-ignored, so the check
    above cannot see it -- and it is exactly where a name nobody looks at
    survives a rename. It is a developer's working copy rather than repository
    content, so its absence is not a failure.
    """
    configuration = PROJECT_ROOT / ".serena" / "project.yml"
    if not configuration.is_file():
        pytest.skip("no local Serena configuration in this working copy")

    declared = yaml.safe_load(configuration.read_text(encoding="utf-8"))["project_name"]

    assert declared == DISTRIBUTION_NAME


def test_the_browser_tab_and_the_navigation_agree_on_the_name() -> None:
    """The two places a name is read without looking for it."""
    assert f"<title>{PRODUCT_NAME}</title>" in _read("web/index.html")
    assert PRODUCT_NAME in _read("web/src/app/Header.tsx")


def test_the_api_document_is_titled_with_the_product_name() -> None:
    """An OpenAPI title is what a generated client and `/docs` are called."""
    from trackvault.main import create_app

    assert create_app().openapi()["info"]["title"] == PRODUCT_NAME


def test_the_image_label_states_the_product_name() -> None:
    """What a registry listing and `docker image inspect` call it."""
    assert f'org.opencontainers.image.title="{PRODUCT_NAME}"' in _read("Dockerfile")
    assert f"org.opencontainers.image.title={PRODUCT_NAME}" in _read(
        ".github/workflows/release.yml"
    )


def test_the_installer_greets_the_user_by_the_product_name() -> None:
    """The first sentence anybody sees from this project."""
    assert f"{PRODUCT_NAME} is starting on" in _read("install-docker.sh")


def test_every_technical_identifier_derives_from_the_product_name() -> None:
    """The distribution, the package, the command, the service and the image.

    A reader who meets the product as TrackVault and then has to run a command
    named after something else has found a second name, and a second name is a
    second thing to keep true.
    """
    project = _pyproject()

    assert project["name"] == DISTRIBUTION_NAME
    assert project["scripts"] == {DISTRIBUTION_NAME: "trackvault.cli:main"}
    assert (PROJECT_ROOT / "src" / "trackvault").is_dir()
    assert f"{DISTRIBUTION_NAME}:" in _read("compose.yaml")
    assert f"/{DISTRIBUTION_NAME}" in _read(".github/workflows/release.yml")
    assert json.loads(_read("web/package.json"))["name"] == f"{DISTRIBUTION_NAME}-web"


def test_the_archive_format_names_the_product_that_writes_it() -> None:
    """A format identifier is what a written file says about itself.

    It is compared by the restore path, so it is the one string here that a
    file on somebody's disk can disagree with. Nothing has been published yet,
    which is what makes changing it free -- and what makes changing it later
    not.
    """
    from trackvault.application.archive import ARCHIVE_FORMAT_NAME, DATABASE_MEMBER

    assert ARCHIVE_FORMAT_NAME == "trackvault-archive"
    assert DATABASE_MEMBER == "database/trackvault.sqlite3"


# --- The project has one licence ----------------------------------------------


def test_the_licence_text_is_the_agpl_itself() -> None:
    """A LICENSE file that summarises a licence grants nothing."""
    text = _read("LICENSE")

    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 19 November 2007" in text
    assert "END OF TERMS AND CONDITIONS" in text
    for section in ("0. Definitions.", "13. Remote Network Interaction", "15. Disclaimer"):
        assert section in text, section


def test_every_declaration_states_the_licence_in_the_repository() -> None:
    """One file, and three machine-readable pointers at it.

    A package index, an npm manifest and a container label are the three places
    somebody finds out what they may do with this without opening the source.
    """
    project = _pyproject()

    assert project["license"] == LICENSE_IDENTIFIER
    assert project["license-files"] == ["LICENSE"]
    assert json.loads(_read("web/package.json"))["license"] == LICENSE_IDENTIFIER
    assert f'org.opencontainers.image.licenses="{LICENSE_IDENTIFIER}"' in _read("Dockerfile")


def test_the_installed_distribution_carries_the_identifier() -> None:
    """What a resolver, an SBOM and the licence audit actually read."""
    from importlib.metadata import metadata

    assert metadata(DISTRIBUTION_NAME)["License-Expression"] == LICENSE_IDENTIFIER


def test_the_image_conveys_the_licence_of_the_program_it_ships() -> None:
    """An AGPL binary travels with its licence, not with a link to one."""
    assert "COPY --chown=app:app LICENSE /app/LICENSE" in _read("Dockerfile")
    assert "!LICENSE" in _read(".dockerignore")


def test_the_project_licence_is_not_smuggled_into_the_dependency_allowlist() -> None:
    """Two unrelated questions, and conflating them would weaken the gate.

    What this project licenses its own code under says nothing about which
    licences it accepts from somebody else's. The own distributions are excluded
    from the audits by name instead.
    """
    policy = json.loads(_read("license-policy.json"))

    assert LICENSE_IDENTIFIER not in policy["allowed"]
    assert set(policy["self"]) == {DISTRIBUTION_NAME, f"{DISTRIBUTION_NAME}-web"}


def test_the_notice_document_states_the_project_licence() -> None:
    """The one document an operator is pointed at for licence questions."""
    notices = _read("docs/legal/third-party-notices.md")

    assert f"## {PRODUCT_NAME} itself" in notices
    assert LICENSE_IDENTIFIER in notices
    assert "declares no licence of its own" not in notices


def test_the_readme_states_the_licence_without_restating_it() -> None:
    """A reader is told the identifier and pointed at the text."""
    readme = _read("README.md")

    assert LICENSE_IDENTIFIER in readme
    assert "Affero" in readme
    assert "LICENSE" in readme


# --- Two ports, and they are different numbers --------------------------------


def test_the_compose_file_publishes_the_host_default_onto_the_container_port() -> None:
    """8081 outside, 8080 inside, and the host side stays overridable."""
    assert f'"${{TRACKVAULT_HTTP_PORT:-{HOST_PORT}}}:{CONTAINER_PORT}"' in _read("compose.yaml")


def test_the_installer_defaults_to_the_same_host_port() -> None:
    """A user who follows the README and a user who runs the installer agree."""
    script = _read("install-docker.sh")

    assert f'DEFAULT_PORT="{HOST_PORT}"' in script
    assert f'CONTAINER_PORT="{CONTAINER_PORT}"' in script
    assert f'"${{TRACKVAULT_HTTP_PORT}}:{CONTAINER_PORT}"' in script


def test_the_application_still_binds_the_container_port_by_default() -> None:
    """`TRACKVAULT_PORT` is the address the process binds, not the published one.

    Moving it to 8081 would break the image's health check, which asks the
    container's own port and must keep doing so however the host publishes it.
    """
    from trackvault.config import Settings

    assert Settings(data_dir=PROJECT_ROOT / "does-not-exist").port == int(CONTAINER_PORT)
    assert f"TRACKVAULT_PORT={CONTAINER_PORT}" in _read("Dockerfile")


def test_the_container_health_check_asks_the_container_and_not_the_host() -> None:
    """A health check aimed at the published port fails on every other machine."""
    recipe = _read("Dockerfile")

    assert f"http://127.0.0.1:{CONTAINER_PORT}/healthz" in recipe
    assert f"EXPOSE {CONTAINER_PORT}" in recipe
    assert HOST_PORT not in recipe


def test_the_readme_sends_the_reader_to_the_published_port() -> None:
    """The address in the quick start is the one the default deployment answers."""
    assert f"localhost:{HOST_PORT}" in _read("README.md")
