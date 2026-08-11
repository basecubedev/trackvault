"""Contract for keeping personal GPS data out of the repository.

``docs/developer/agent-rules.md`` states the rule; this test makes it executable.
A developer keeps real recordings and planned routes under ``./import-tracks`` to
explore against and to run the local reference regression. Those files are
private movement data about real people, so the repository has to exclude them
whatever they are called.

The check is behavioural: it asks Git itself whether a path would be ignored,
rather than reading a pattern out of ``.gitignore`` and trusting that it means
what it looks like. None of the paths below has to exist -- the contract is about
the rule, not about one developer's working copy.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LOCAL_TRACKS_DIRNAME = "import-tracks"

# Paths that must be excluded. The two reference tracks are the files that
# actually live there today; the others prove the rule covers the *directory*
# rather than the extensions we happen to know about now.
PRIVATE_PATHS = (
    f"{LOCAL_TRACKS_DIRNAME}/2025-10-20_Mallorca_talaia_d_alcudia.gpx",
    f"{LOCAL_TRACKS_DIRNAME}/Coll_de_na_Benet__Ermita_de_la_Victòria_Runde_von_Bonaire.gpx",
    f"{LOCAL_TRACKS_DIRNAME}/notes.txt",
    f"{LOCAL_TRACKS_DIRNAME}/README.md",
    f"{LOCAL_TRACKS_DIRNAME}/nested/recording.fit",
)

# The exemption the fixtures rely on. An exclusion broad enough to swallow the
# synthetic committed fixtures would be a different kind of mistake, and one that
# only shows up when a fixture silently stops being versioned.
COMMITTED_PATHS = (
    "tests/fixtures/gpx/ambiguous-minimal.gpx",
    "tests/contract/test_private_data_contract.py",
)

_IGNORED = 0
_NOT_IGNORED = 1


def _git() -> str:
    """Return the Git executable, or skip: this contract is about Git's behaviour."""
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("git is not available")
    if not (PROJECT_ROOT / ".git").exists():
        pytest.skip("not a git working tree")
    return executable


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command against the repository and capture its result."""
    return subprocess.run(  # noqa: S603 - the executable is resolved, the arguments are literals
        [_git(), "-C", str(PROJECT_ROOT), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_ignored(path: str) -> bool:
    """Report whether Git would exclude that path. The path need not exist."""
    result = _run("check-ignore", "-q", "--", path)
    if result.returncode not in (_IGNORED, _NOT_IGNORED):
        pytest.fail(f"git check-ignore failed for {path!r}: {result.stderr.strip()}")
    return result.returncode == _IGNORED


@pytest.mark.contract
@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_local_reference_tracks_are_excluded(path: str) -> None:
    """Everything below the local reference directory stays out of the repository."""
    assert _is_ignored(path), f"{path} would be committable"


@pytest.mark.contract
def test_the_whole_local_reference_directory_is_excluded() -> None:
    """The exclusion names the directory, not the two filenames that live there now.

    A rule listing today's files would leave the next recording unprotected, which
    is the failure mode worth guarding: nobody edits ``.gitignore`` before dropping
    a file into a directory that already works.
    """
    assert _is_ignored(f"{LOCAL_TRACKS_DIRNAME}/"), "the directory itself is not excluded"


@pytest.mark.contract
@pytest.mark.parametrize("path", COMMITTED_PATHS)
def test_repository_content_stays_committable(path: str) -> None:
    """The exclusions do not reach the synthetic fixtures or the suite itself."""
    assert not _is_ignored(path), f"{path} is excluded and could not be committed"


@pytest.mark.contract
def test_no_local_reference_track_is_tracked() -> None:
    """Nothing below the local reference directory is in the index.

    An ignore rule does not apply to a file Git already tracks, so "it is ignored"
    and "it is not committed" are two separate statements and both have to hold.
    """
    result = _run("ls-files", "--", f"{LOCAL_TRACKS_DIRNAME}/")
    assert result.returncode == 0, f"git ls-files failed: {result.stderr.strip()}"
    assert not result.stdout.strip(), (
        f"private files are tracked in git: {result.stdout.strip().splitlines()}"
    )
