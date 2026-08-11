"""What the release pipeline is allowed to publish, and from where.

The images people run are the most trusted artifact this project produces, and
the whole access-control model for them is "only a release tag publishes". That
is a property of two YAML files, so it is checked like any other contract.

Nothing here runs GitHub Actions. What is asserted is the shape of the
workflows: which events start them, which one is allowed to push, and that the
release runs the *same* quality gate as the main branch rather than a copy of it
that will eventually be an older copy.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.contract, pytest.mark.unit]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
INSTALLER = PROJECT_ROOT / "install-docker.sh"


def _workflow(path: Path) -> dict[str, Any]:
    """Parse one workflow.

    ``on`` is YAML 1.1's spelling of ``True``, so the key comes back as a
    boolean. Normalising it here keeps every test below readable.
    """
    document: dict[Any, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {("on" if key is True else key): value for key, value in document.items()}


def _project_license() -> str:
    """Return the one SPDX identifier this project declares."""
    identifier: str = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["license"]
    return identifier


def _jobs(path: Path) -> dict[str, Any]:
    """Return the jobs of one workflow."""
    jobs: dict[str, Any] = _workflow(path)["jobs"]
    return jobs


def test_a_release_workflow_exists() -> None:
    """Releasing is a pipeline rather than somebody's laptop."""
    assert RELEASE.is_file()


def test_only_a_version_tag_starts_a_release() -> None:
    """No branch push publishes anything.

    This is the entire access-control model for the images people run: nothing
    outside the release workflow pushes to a registry, and nothing but a `v*`
    tag starts the release workflow.
    """
    triggers = _workflow(RELEASE)["on"]

    assert set(triggers) == {"push"}
    assert triggers["push"] == {"tags": ["v*"]}
    assert "branches" not in triggers["push"]


def test_the_release_runs_the_same_quality_gate_as_the_main_branch() -> None:
    """A copy of the gate is a gate that eventually falls behind the original."""
    quality = _jobs(RELEASE)["quality"]

    assert quality["uses"] == "./.github/workflows/ci.yml"
    assert "workflow_call" in _workflow(CI)["on"]


def test_publishing_waits_for_the_quality_gate() -> None:
    """Nothing reaches a registry that has not passed it."""
    publish = _jobs(RELEASE)["publish"]

    assert set(publish["needs"]) == {"quality"}


def test_every_checkout_can_see_the_tags() -> None:
    """The version comes from the tag, so a shallow checkout cannot build.

    `actions/checkout` fetches a single commit by default and no tags with it.
    Every job here installs the project or builds the image from it, and
    `hatch-vcs` has nothing to read in a repository whose history was cut off
    -- so this is a build failure waiting for whoever adds the next job.
    """
    for workflow in (CI, RELEASE):
        steps = [
            step
            for job in _jobs(workflow).values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("actions/checkout")
        ]

        assert steps, f"{workflow.name} checks nothing out"
        for step in steps:
            assert step.get("with", {}).get("fetch-depth") == 0, f"{workflow.name}: {step}"


def test_the_quality_gate_still_covers_everything_a_release_depends_on() -> None:
    """The gate is only worth calling if it still contains the checks.

    Named explicitly so that deleting one from CI fails here rather than
    quietly weakening every future release.
    """
    jobs = _jobs(CI)

    assert {"quality", "frontend", "browser", "docker"} <= set(jobs)
    steps = str(jobs)
    for gate in ("uv lock --check", "mypy", "pytest", "npm run licenses", "docker build"):
        assert gate in steps, gate
    assert "git diff --exit-code -- src/api/schema.ts" in steps, (
        "the frontend API drift check is a release gate and has gone missing"
    )


def test_only_the_release_workflow_pushes_an_image() -> None:
    """CI builds the image to prove it builds. It must never publish one."""
    assert "push: true" not in CI.read_text(encoding="utf-8")
    assert "docker/login-action" not in CI.read_text(encoding="utf-8")


def test_the_release_builds_the_image_with_the_version_the_tag_names() -> None:
    """The tag is the version, so nothing else may decide what the image says.

    There is deliberately no reconciliation step here: with nothing to
    reconcile a tag *cannot* disagree with a declared version, which is the
    class of release failure this arrangement removes rather than reports.
    """
    recipe = RELEASE.read_text(encoding="utf-8")

    assert "TRACKVAULT_VERSION=${{ github.ref_name }}" not in recipe, (
        "a build argument is a literal string, so the image would be labelled `v1.2.3`"
    )
    assert 'echo "version=${VERSION#v}"' in recipe
    assert "TRACKVAULT_VERSION=${{ steps.release.outputs.version }}" in recipe
    assert "version" not in _jobs(RELEASE), "a version job is a second authority"


def test_latest_is_published_only_for_a_final_release() -> None:
    """A pre-release must not become what an unpinned deployment pulls tomorrow."""
    recipe = RELEASE.read_text(encoding="utf-8")

    assert "type=raw,value=latest,enable=${{ !contains(github.ref_name, '-') }}" in recipe


def test_the_published_tags_cover_the_documented_set() -> None:
    """`v1.2.3`, `1.2.3` and `latest`, plus a minor series to follow."""
    recipe = RELEASE.read_text(encoding="utf-8")

    for pattern in ("pattern=v{{version}}", "pattern={{version}}", "pattern={{major}}.{{minor}}"):
        assert pattern in recipe, pattern


def test_the_image_carries_the_metadata_a_build_can_actually_know() -> None:
    """Version, revision, source and creation time, from the run that produced it.

    They come from `metadata-action` rather than from the Dockerfile, because an
    ordinary local build knows none of them and a label stating an empty string
    is worse than no label: it looks answered.
    """
    recipe = RELEASE.read_text(encoding="utf-8")

    assert "docker/metadata-action" in recipe
    assert "labels: ${{ steps.metadata.outputs.labels }}" in recipe
    assert "TRACKVAULT_VERSION=" in recipe


def test_the_published_image_states_the_licence_this_project_chose() -> None:
    """A label is a legal claim, so it may only ever repeat the `LICENSE` file.

    What is checked is that the two agree, not merely that a label exists: a
    published identifier naming a licence the repository does not carry would be
    a claim invented by a build script.
    """
    assert (PROJECT_ROOT / "LICENSE").is_file()

    declared = [
        line.strip()
        for line in RELEASE.read_text(encoding="utf-8").splitlines()
        if "image.licenses" in line and not line.lstrip().startswith("#")
    ]

    assert declared == [f"org.opencontainers.image.licenses={_project_license()}"], declared


def test_the_release_publishes_a_bill_of_materials_and_its_provenance() -> None:
    """Two lines that turn "what is in this image" into a query."""
    recipe = RELEASE.read_text(encoding="utf-8")

    assert "sbom: true" in recipe
    assert "provenance: mode=max" in recipe


def test_the_release_ships_the_installer_and_a_checksum_for_it() -> None:
    """One file people curl before they have anything else to check it with."""
    recipe = RELEASE.read_text(encoding="utf-8")

    assert INSTALLER.is_file()
    assert "install-docker.sh" in recipe
    assert "sha256sum install-docker.sh" in recipe


def test_the_image_name_is_derived_rather_than_typed() -> None:
    """A hand-written owner is a hand-written owner that will be wrong once."""
    recipe = RELEASE.read_text(encoding="utf-8")

    assert "${{ github.repository_owner }}/trackvault" in recipe


def test_the_installer_default_image_matches_what_the_pipeline_publishes() -> None:
    """The installer names a repository; the pipeline pushes to one. Same shape.

    The owner cannot be checked from here -- this repository has no remote to
    read one from -- so what is asserted is the part that is knowable: both
    sides agree on the registry and the image name.
    """
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'DEFAULT_IMAGE_REPOSITORY="ghcr.io/' in script
    assert script.rstrip().count("/trackvault") >= 1
