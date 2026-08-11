"""What is true after a restore that did not finish.

Publishing a restore is two renames, and two renames are not one atomic act.
Between them the deployment holds a database from the archive and a raw storage
from nowhere, which is a state nothing can read correctly and nothing should
ever be left in.

Two different interruptions have to be survived, and they are not the same
problem:

```
an exception    the handler runs    -> roll back, in the same process
the process dies no handler runs    -> a marker on disk, read at the next start
```

The first is the more dangerous of the two, because a plausible cleanup makes it
catastrophic: the displaced directory holds the *only* copy of the previous
database, and discarding it as "staging debris" turns a failed restore into
total data loss. The tests below therefore assert what is still there, never
merely that an error was raised.
"""

import contextlib
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from trackvault.application.archive import ArchiveError
from trackvault.application.diagnostics import CheckStatus, Diagnose
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.config import Settings
from trackvault.domain import InputChannel
from trackvault.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from trackvault.infrastructure.archive.publication import (
    RESTORE_MARKER_NAME,
    pending_restore,
    recover_interrupted_restore,
)
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.database.migrations import SCHEMA_VERSION
from trackvault.infrastructure.diagnostics import observe

pytestmark = [pytest.mark.contract, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
ORIGINAL = "recorded-measurements.gpx"
REPLACEMENT = "planned-route-instructions.gpx"


def _deployment(root: Path) -> TrackServices:
    """Return a wired archive over its own data directory."""
    services = build_services(Settings(data_dir=root))
    services.prepare_storage()
    return services


def _import(services: TrackServices, name: str) -> str:
    """Import one synthetic fixture and return its content hash."""
    outcome = services.import_tracks(
        ImportRequest(
            content=(FIXTURES / name).read_bytes(),
            original_filename=name,
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    assert outcome.status is ImportStatus.IMPORTED, outcome.error_code
    return outcome.sha256


def _backup_of(services: TrackServices, destination: Path) -> Path:
    """Write one archive of a deployment."""
    services.create_archive(
        FilesystemArchiveBuilder(
            destination=destination,
            database_path=services.settings.database_path,
            raw_root=services.settings.raw_storage_dir,
        )
    )
    return destination


def _extractor(archive: Path, target: Path) -> FilesystemArchiveExtractor:
    """Return an extractor publishing into a data directory."""
    settings = Settings(data_dir=target)
    return FilesystemArchiveExtractor(
        source=archive,
        data_dir=target,
        database_path=settings.database_path,
        raw_root=settings.raw_storage_dir,
    )


@pytest.fixture
def archive_and_target(tmp_path: Path) -> tuple[Path, Path, str]:
    """Return an archive of one deployment and a populated target holding another.

    The target holds a *different* recording, so "the old data survived" and
    "the new data arrived" are distinguishable rather than both looking like
    "there is a track here".
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, ORIGINAL)
    archive = _backup_of(origin, tmp_path / "backup.tar.gz")
    target_root = tmp_path / "target"
    target = _deployment(target_root)
    existing = _import(target, REPLACEMENT)
    return archive, target_root, existing


def _crash_reaching(destination: Path) -> Callable[[Path, object], Path]:
    """Return a `Path.replace` that dies just before one named move.

    Named rather than counted, for the two windows that have to be tested
    exactly. Whether the database has journal files beside it changes how many
    moves a publication makes, so a count would select a different window on
    different days -- and the windows are not interchangeable: one is "nothing
    is published yet" and the other is "the database is live and its storage is
    not".
    """
    original = Path.replace

    def replace(this: Path, target: object) -> Path:
        if Path(str(target)) == destination:
            raise OSError("the process was killed mid-publication")
        return original(this, Path(str(target)))  # type: ignore[arg-type]

    return replace


def _publish_crashing(
    monkeypatch: pytest.MonkeyPatch,
    archive: Path,
    target: Path,
    *,
    at: Callable[[Path, object], Path],
) -> FilesystemArchiveExtractor:
    """Attempt a restore whose publication is interrupted by ``at``.

    Returns the extractor that was interrupted, so a test can drive its own
    handler. An interruption that never fires simply completes the publication,
    which is a case worth covering rather than one to guard against: the
    invariant afterwards is the same either way.
    """
    monkeypatch.setattr(Path, "replace", at)
    extractor = _extractor(archive, target)
    try:
        with contextlib.suppress(OSError, ArchiveError):
            extractor.verify(extractor.manifest())
            extractor.publish()
    finally:
        monkeypatch.undo()
    return extractor


def _before_publishing_the_database(target: Path) -> Callable[[Path, object], Path]:
    """Interrupt once everything is displaced and nothing is published yet."""
    return _crash_reaching(Settings(data_dir=target).database_path)


def _before_publishing_the_storage(target: Path) -> Callable[[Path, object], Path]:
    """Interrupt once the database is live and its storage is still displaced."""
    return _crash_reaching(Settings(data_dir=target).raw_storage_dir)


def _holds_exactly(target: Path, sha256: str) -> None:
    """Assert a data directory is one readable archive holding one known source."""
    services = build_services(Settings(data_dir=target))
    services.prepare_storage()
    snapshots = services.store.processing_snapshots()
    assert [snapshot.raw_import_sha256 for snapshot in snapshots] == [sha256]
    assert services.raw_store.integrity(sha256).name == "HEALTHY"
    observation = observe(services, SCHEMA_VERSION)
    assert observation.database_intact
    assert observation.raw_artifacts_missing == 0
    assert observation.raw_artifacts_corrupt == 0
    assert not (target / RESTORE_MARKER_NAME).exists()
    assert not list(target.glob(".restore-*"))
    assert not list(target.glob(".replaced-*"))


# --- an exception during publication --------------------------------------


def test_a_failed_restore_does_not_discard_the_data_it_displaced(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catastrophic case: cleanup that removes the only copy of the old data.

    The previous database and raw storage are moved aside before the new ones
    land. Treating that directory as staging debris when the publication fails
    turns "the restore did not work" into "the archive is gone".
    """
    archive, target, existing = archive_and_target
    extractor = _publish_crashing(
        monkeypatch, archive, target, at=_before_publishing_the_database(target)
    )
    displaced = next(iter(target.glob(".replaced-*")))
    assert (displaced / Settings(data_dir=target).database_path.name).is_file(), (
        "the window this test is about was not reached"
    )

    extractor.abandon()

    _holds_exactly(target, existing)


def test_abandoning_a_partial_publication_puts_the_previous_archive_back(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolled back, not left half-restored. The deployment is the one it was."""
    archive, target, existing = archive_and_target
    extractor = _publish_crashing(
        monkeypatch, archive, target, at=_before_publishing_the_storage(target)
    )
    assert Settings(data_dir=target).database_path.is_file()

    extractor.abandon()

    _holds_exactly(target, existing)


# --- the process dying, with no handler at all ----------------------------


def test_an_interrupted_publication_is_visible_at_the_next_start(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed process runs no handler, so the evidence has to be on disk."""
    archive, target, _ = archive_and_target

    _publish_crashing(monkeypatch, archive, target, at=_before_publishing_the_storage(target))

    assert (target / RESTORE_MARKER_NAME).is_file()
    assert pending_restore(target) is not None


@pytest.mark.parametrize("where", ["database", "storage", "nowhere"])
def test_recovery_leaves_one_readable_archive_wherever_it_was_interrupted(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """The contract, across every point the publication can stop.

    Two outcomes and no others: the restore completed, or it did not happen.
    "It got far enough, so we finished it" is deliberately not among them --
    somebody whose restore reported an error has to be able to believe their
    archive is the one they started with.

    What is ruled out either way is the third state: a database whose sources
    are not on disk, or a deployment holding neither.
    """
    archive, target, existing = archive_and_target
    interruptions = {
        "database": _before_publishing_the_database(target),
        "storage": _before_publishing_the_storage(target),
        "nowhere": _crash_reaching(target / "nothing-is-ever-moved-here"),
    }
    _publish_crashing(monkeypatch, archive, target, at=interruptions[where])

    recover_interrupted_restore(target)

    services = build_services(Settings(data_dir=target))
    services.prepare_storage()
    observation = observe(services, SCHEMA_VERSION)
    assert observation.database_present
    assert observation.database_intact
    assert observation.raw_artifacts_expected == 1
    assert observation.raw_artifacts_missing == 0
    assert observation.raw_artifacts_corrupt == 0
    assert not (target / RESTORE_MARKER_NAME).exists()
    assert not list(target.glob(".restore-*"))
    assert not list(target.glob(".replaced-*"))
    # Rolled back or rolled forward, but never a mixture of the two: whichever
    # archive is in place, its database and its storage describe each other.
    held = [snapshot.raw_import_sha256 for snapshot in services.store.processing_snapshots()]
    assert len(held) == 1
    assert services.raw_store.integrity(held[0]).name == "HEALTHY"
    assert (held[0] == existing) == (where != "nowhere")


def test_starting_the_archive_recovers_an_interrupted_restore_by_itself(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody has to know a recovery command exists.

    The same arrangement interrupted map installations already have: the
    composition root brings storage back to a readable state before it serves
    anything.
    """
    archive, target, _ = archive_and_target
    _publish_crashing(monkeypatch, archive, target, at=_before_publishing_the_storage(target))

    services = build_services(Settings(data_dir=target))
    services.prepare_storage()

    assert not (target / RESTORE_MARKER_NAME).exists()
    assert Diagnose()(observe(services, SCHEMA_VERSION)).status is not CheckStatus.ERROR


def test_the_doctor_reports_an_interrupted_restore_rather_than_a_healthy_archive(
    archive_and_target: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` changes nothing, so it has to be able to *say* what it found.

    An operator who ran a restore and lost the container needs to be told the
    publication is unfinished, not shown a clean report of whichever half is
    currently in place.
    """
    archive, target, _ = archive_and_target
    _publish_crashing(monkeypatch, archive, target, at=_before_publishing_the_storage(target))

    services = build_services(Settings(data_dir=target))
    report = Diagnose()(observe(services, SCHEMA_VERSION))

    check = next(item for item in report.checks if item.name == "restore")
    assert check.status is CheckStatus.ERROR
    assert (target / RESTORE_MARKER_NAME).is_file(), "doctor must not clear what it reports"


def test_a_deployment_that_never_restored_reports_nothing_about_one(tmp_path: Path) -> None:
    """The baseline: no marker, no check, no advice about a restore nobody ran."""
    services = _deployment(tmp_path / "data")
    _import(services, ORIGINAL)

    report = Diagnose()(observe(services, SCHEMA_VERSION))

    check = next(item for item in report.checks if item.name == "restore")
    assert check.status is CheckStatus.OK
    assert pending_restore(tmp_path / "data") is None


def test_a_completed_restore_leaves_no_marker(tmp_path: Path) -> None:
    """The marker means "unfinished", so an ordinary restore must not leave one."""
    origin = _deployment(tmp_path / "origin")
    _import(origin, ORIGINAL)
    archive = _backup_of(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"

    origin.restore_archive(_extractor(archive, target))

    assert not (target / RESTORE_MARKER_NAME).exists()
    assert pending_restore(target) is None
    assert not list(target.glob(".restore-*"))
    assert not list(target.glob(".replaced-*"))


def test_recovering_a_deployment_that_needs_nothing_does_nothing(tmp_path: Path) -> None:
    """Recovery runs at every start, so its no-op path is the common one."""
    services = _deployment(tmp_path / "data")
    _import(services, ORIGINAL)
    before = sorted(path.name for path in (tmp_path / "data").iterdir())

    assert recover_interrupted_restore(tmp_path / "data") is None

    assert sorted(path.name for path in (tmp_path / "data").iterdir()) == before


def test_a_marker_that_cannot_be_read_is_not_a_reason_to_delete_anything(
    tmp_path: Path,
) -> None:
    """An unreadable marker is a fault to report, never a licence to act.

    Recovery moves data. A marker it cannot interpret names no directories, so
    there is nothing it can safely do and it says so rather than guessing at
    which of two copies is the good one.
    """
    services = _deployment(tmp_path / "data")
    _import(services, ORIGINAL)
    (tmp_path / "data" / RESTORE_MARKER_NAME).write_text("{not json", encoding="utf-8")

    recover_interrupted_restore(tmp_path / "data")

    assert services.settings.database_path.is_file()
    report = Diagnose()(observe(services, SCHEMA_VERSION))
    assert (
        next(item for item in report.checks if item.name == "restore").status is CheckStatus.ERROR
    )


def test_recovery_needs_no_configuration_to_agree_with_the_marker(tmp_path: Path) -> None:
    """The marker names its own directories, relative to the data directory.

    A container's data directory is a mount point and its absolute path is a
    property of how it was started, not of the deployment. Recovery that
    depended on the two matching would fail exactly when somebody moved a
    volume, which is one of the reasons they were restoring.
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, ORIGINAL)
    archive = _backup_of(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    _deployment(target)

    extractor = _extractor(archive, target)
    extractor.verify(extractor.manifest())
    extractor.publish()

    moved = tmp_path / "moved"
    shutil.move(str(target), str(moved))
    services = build_services(Settings(data_dir=moved))
    services.prepare_storage()
    assert len(services.store.processing_snapshots()) == 1
