"""Contracts for the Docker installer.

The installer is the first thing a new user runs and the only part of TrackVault
that executes before anything of TrackVault exists. It is therefore held to the
one promise that matters most: **it does not destroy data**. Every test below
that looks paranoid is about that.

These tests run the real script with ``sh``. They never start a container --
``--dry-run`` and ``--no-start`` are exactly the two modes that let an installer
be tested without a daemon, which is also why they exist for users.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trackvault.config import Settings

pytestmark = pytest.mark.integration

INSTALLER = Path(__file__).resolve().parents[2] / "install-docker.sh"


def _run(
    *arguments: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the installer and return its result, without raising on failure."""
    return subprocess.run(  # noqa: S603
        ["sh", str(INSTALLER), *arguments],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_installer_is_executable_and_documents_itself() -> None:
    """`--help` is the first thing a cautious person runs. It must not need Docker."""
    result = _run("--help")

    assert result.returncode == 0
    for flag in ("--tag", "--port", "--import-dir", "--no-start", "--dry-run", "--force"):
        assert flag in result.stdout


def test_a_dry_run_changes_nothing_at_all(tmp_path: Path) -> None:
    """The mode that lets somebody look before they install."""
    target = tmp_path / "trackvault"

    result = _run("--dir", str(target), "--dry-run")

    assert result.returncode == 0
    assert not target.exists()
    assert "dry run" in result.stdout


def test_installing_into_an_empty_directory_creates_the_whole_layout(tmp_path: Path) -> None:
    """The headline claim: an empty directory becomes an installation."""
    target = tmp_path / "trackvault"

    result = _run("--dir", str(target), "--no-start")

    assert result.returncode == 0, result.stderr
    assert (target / "docker-compose.yml").is_file()
    assert (target / ".env").is_file()
    for directory in ("data", "import", "backups"):
        assert (target / directory).is_dir()


def test_data_and_import_are_separate_directories(tmp_path: Path) -> None:
    """Application-owned storage and external input are not the same place.

    If they were, a backup written into the import folder would be re-read as an
    import, and a restore would overwrite the files somebody was still syncing.
    """
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start")

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")

    assert "./data:/data" in compose
    assert ":/import:ro" in compose
    assert "./backups:/backups" in compose


def test_the_import_mount_is_read_only(tmp_path: Path) -> None:
    """The mount enforcing what the application already promises.

    `scan` reads the import directory and never writes, renames, moves or
    deletes in it. The `:ro` is the second, independent reason that stays true
    if the first is ever wrong.
    """
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start")

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")

    assert "${TRACKVAULT_IMPORT_PATH}:/import:ro" in compose


def test_the_container_is_told_where_to_import_from(tmp_path: Path) -> None:
    """The configuration whose absence disables scanning, set for the user.

    A deployment that mounts a folder and forgets this variable has a scan that
    reports "no import directory configured" while the files sit there.
    """
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start")

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")

    assert "TRACKVAULT_IMPORT_DIR: /import" in compose
    assert "TRACKVAULT_DATA_DIR: /data" in compose
    assert "TRACKVAULT_BACKUP_DIR: /backups" in compose


def test_the_container_does_not_run_as_root(tmp_path: Path) -> None:
    """Non-root is not negotiable, and it is not solved with `chmod 777` either."""
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start")

    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")
    environment = (target / ".env").read_text(encoding="utf-8")

    assert 'user: "${PUID}:${PGID}"' in compose
    assert f"PUID={os.getuid()}" in environment
    assert f"PGID={os.getgid()}" in environment


def test_a_chosen_tag_is_what_gets_installed(tmp_path: Path) -> None:
    """Pinning a release is how somebody decides their own upgrades."""
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start", "--tag", "v9.9.9")

    assert "TRACKVAULT_IMAGE=ghcr.io/basecubedev/trackvault:v9.9.9" in (target / ".env").read_text(
        encoding="utf-8"
    )


def test_the_default_tag_is_a_release_rather_than_a_branch(tmp_path: Path) -> None:
    """No deployment silently runs whatever was last pushed to the main branch."""
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start")

    environment = (target / ".env").read_text(encoding="utf-8")
    assert environment.count("TRACKVAULT_IMAGE=") == 1
    assert ":latest" in environment
    assert "main" not in environment and "edge" not in environment


def test_the_default_host_port_is_8081(tmp_path: Path) -> None:
    """The address somebody opens when they typed no flags.

    Asserted on both halves of the mapping: 8081 is the host side and 8080 is
    the container's, and the whole point of the default is that the two are not
    the same number.
    """
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start")
    announced = _run("--dir", str(tmp_path / "elsewhere"), "--dry-run")

    assert "TRACKVAULT_HTTP_PORT=8081" in (target / ".env").read_text(encoding="utf-8")
    assert '"${TRACKVAULT_HTTP_PORT}:8080"' in (target / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "http://localhost:8081" in announced.stdout


def test_a_chosen_port_reaches_the_configuration(tmp_path: Path) -> None:
    """Somebody who already runs something on 8081 has to be able to say so.

    Only the host side moves. The container keeps listening on 8080, which is
    what the image's health check asks and what the mapping's right-hand side
    therefore has to stay.
    """
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start", "--port", "9090")

    environment = (target / ".env").read_text(encoding="utf-8")
    compose = (target / "docker-compose.yml").read_text(encoding="utf-8")

    assert "TRACKVAULT_HTTP_PORT=9090" in environment
    assert "TRACKVAULT_HTTP_PORT=8081" not in environment
    assert '"${TRACKVAULT_HTTP_PORT}:8080"' in compose


def test_a_chosen_import_directory_reaches_the_configuration(tmp_path: Path) -> None:
    """The phone sync folder is usually somewhere else entirely."""
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start", "--import-dir", "/srv/sync/locus")

    assert "TRACKVAULT_IMPORT_PATH=/srv/sync/locus" in (target / ".env").read_text(encoding="utf-8")


def test_a_non_numeric_port_is_refused(tmp_path: Path) -> None:
    """A port that is not a port fails now rather than at `docker compose up`."""
    target = tmp_path / "trackvault"

    result = _run("--dir", str(target), "--no-start", "--port", "eighty")

    assert result.returncode != 0
    assert not target.exists()


def test_a_flag_does_not_swallow_the_next_flag_as_its_value(tmp_path: Path) -> None:
    """`--port --force` must not install an archive on port "--force"."""
    target = tmp_path / "trackvault"

    result = _run("--dir", str(target), "--no-start", "--port", "--force")

    assert result.returncode != 0
    assert "needs a value" in result.stderr


def test_an_unknown_option_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """A typo that silently installs something else is worse than a failure."""
    result = _run("--dir", str(tmp_path / "trackvault"), "--no-strat")

    assert result.returncode != 0
    assert "unknown option" in result.stderr


# --- the promise: nothing of yours is destroyed ---------------------------


def test_reinstalling_preserves_an_existing_configuration(tmp_path: Path) -> None:
    """Running the installer twice is something people do. It must be safe."""
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start", "--port", "9123")
    (target / "docker-compose.yml").write_text("# hand-edited\n", encoding="utf-8")

    result = _run("--dir", str(target), "--no-start")

    assert result.returncode == 0
    assert (target / "docker-compose.yml").read_text(encoding="utf-8") == "# hand-edited\n"
    assert "TRACKVAULT_HTTP_PORT=9123" in (target / ".env").read_text(encoding="utf-8")
    assert "keeping the existing" in result.stdout


def test_reinstalling_preserves_data_import_and_backups(tmp_path: Path) -> None:
    """The files nobody can recreate, across the most careless possible rerun."""
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start")
    (target / "data" / "trackvault.sqlite3").write_bytes(b"an archive")
    (target / "import" / "ride.gpx").write_bytes(b"a recording")
    (target / "backups" / "backup.tar.gz").write_bytes(b"a backup")

    _run("--dir", str(target), "--no-start", "--force")

    assert (target / "data" / "trackvault.sqlite3").read_bytes() == b"an archive"
    assert (target / "import" / "ride.gpx").read_bytes() == b"a recording"
    assert (target / "backups" / "backup.tar.gz").read_bytes() == b"a backup"


def test_force_replaces_the_configuration_and_only_the_configuration(
    tmp_path: Path,
) -> None:
    """`--force` is about this script's own output, and says so."""
    target = tmp_path / "trackvault"
    _run("--dir", str(target), "--no-start", "--port", "9123")

    _run("--dir", str(target), "--no-start", "--port", "9124", "--force")

    environment = (target / ".env").read_text(encoding="utf-8")
    assert "TRACKVAULT_HTTP_PORT=9124" in environment
    assert "TRACKVAULT_HTTP_PORT=9123" not in environment


def test_an_installation_does_not_switch_writing_on_for_you(tmp_path: Path) -> None:
    """The installer writes a settings file, so it is a second place to get this wrong.

    Somebody running this script has read nothing -- that is what it is for --
    and an unauthenticated write endpoint must not be reachable because they
    typed one command. The generated `.env` therefore has to agree with the
    application's own default rather than quietly overriding it.
    """
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start")

    environment = (target / ".env").read_text(encoding="utf-8")
    assert "TRACKVAULT_UPLOAD_ENABLED=false" in environment
    assert "TRACKVAULT_UPLOAD_ENABLED=true" not in environment
    assert Settings(data_dir=tmp_path / "unused").upload_enabled is False


def test_the_created_directories_are_private(tmp_path: Path) -> None:
    """They hold a movement profile of a real person from the first file in them."""
    target = tmp_path / "trackvault"

    _run("--dir", str(target), "--no-start")

    for directory in ("data", "import", "backups"):
        assert (target / directory).stat().st_mode & 0o077 == 0, directory
    assert (target / ".env").stat().st_mode & 0o077 == 0


def test_starting_without_docker_fails_before_it_writes_nonsense(tmp_path: Path) -> None:
    """A machine with no Docker gets a sentence, not a stack of compose errors."""
    target = tmp_path / "trackvault"
    without_docker = dict(os.environ, PATH=str(tmp_path / "empty-path"))
    (tmp_path / "empty-path").mkdir()
    for tool in ("sh", "mkdir", "cat", "id", "chmod"):
        if (found := shutil.which(tool)) is not None:
            (tmp_path / "empty-path" / tool).symlink_to(found)

    result = _run("--dir", str(target), env=without_docker)

    assert result.returncode != 0
    assert "docker is not installed" in result.stderr


def test_the_installer_needs_no_python_node_or_repository_checkout() -> None:
    """The claim that makes this a product rather than a project.

    Asserted against what the script *runs* rather than what it says: comments
    are free to mention Python precisely because the code never invokes it.
    """
    executable_lines = [
        line
        for line in INSTALLER.read_text(encoding="utf-8").lower().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    for forbidden in ("python", "pip ", "npm ", "node ", "git ", "uv "):
        assert not [line for line in executable_lines if forbidden in line], forbidden
