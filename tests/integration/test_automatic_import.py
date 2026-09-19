"""Scanning the import directory: which files are read, when, and only once.

Nothing here is a second import path. A scan reads the directory and hands each
candidate to the one ``ImportTracks`` use case, exactly like ``trackvault scan``
and the upload endpoint do. What the scan adds on its own is discovery: which
names are candidates, whether a file is finished being written, and which files
it has already offered in exactly their current state.

Time is injected rather than waited for. A file's change time is read from the
file system and the clock is set relative to it, so "written a moment ago" and
"settled" are two clock values rather than two sleeps.
"""

import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trackvault.application import ImportErrorCode
from trackvault.application.import_tracks import ImportStatus
from trackvault.application.ports import TrackQuery
from trackvault.cli import EXIT_OK, main
from trackvault.config import Settings
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.filesystem import ImportDirectoryScanner, import_directory
from trackvault.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.integration, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
SETTLE = timedelta(seconds=30)


class SettableClock:
    """A clock that says exactly what the test tells it to."""

    def __init__(self, instant: datetime) -> None:
        """Start at a chosen instant."""
        self.instant = instant

    def now(self) -> datetime:
        """Return the instant the test set."""
        return self.instant


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Return an empty import directory."""
    directory = tmp_path / "inbox"
    directory.mkdir()
    return directory


def _services(tmp_path: Path, inbox: Path) -> TrackServices:
    """Return a freshly wired archive in a throwaway data directory."""
    services = build_services(Settings(data_dir=tmp_path / "data", import_dir=inbox))
    services.prepare_storage()
    return services


def _put(inbox: Path, name: str, fixture: str = "recorded-measurements.gpx") -> Path:
    """Copy a synthetic fixture into the import directory under a chosen name."""
    target = inbox / name
    shutil.copy(FIXTURES / fixture, target)
    return target


def _changed_at(path: Path) -> datetime:
    """Return when the file system last changed the file, as the scan sees it."""
    return datetime.fromtimestamp(path.stat().st_ctime_ns / 1e9, UTC)


def _settled(*paths: Path) -> SettableClock:
    """Return a clock at which every given file has been quiet long enough."""
    return SettableClock(max(_changed_at(path) for path in paths) + SETTLE + timedelta(seconds=1))


def _scanner(services: TrackServices, inbox: Path, clock: SettableClock) -> ImportDirectoryScanner:
    """Return a scanner over the import directory, as the server wires one."""
    return ImportDirectoryScanner(inbox, services.import_tracks, clock=clock, settle_time=SETTLE)


def _break_the_import_of(content: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the GPX adapter fail with an unanticipated error for one document."""
    real_import = GpxImporter.import_tracks

    def failing(self: GpxImporter, offered: bytes, limits: object) -> object:
        if offered == content:
            raise RuntimeError("an adapter defect")
        return real_import(self, offered, limits)  # type: ignore[arg-type]

    monkeypatch.setattr(GpxImporter, "import_tracks", failing)


def _append_before_every_read(track: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a writer add to the file each time, just before the scan reads it."""
    real_read = import_directory.ImportDirectory.read

    def read_after_a_write(
        self: import_directory.ImportDirectory,
        entry: import_directory.ImportDirectoryEntry,
        max_bytes: int,
        expected: import_directory.FileState | None = None,
    ) -> bytes | None:
        with track.open("ab") as writer:
            writer.write(b"<!-- still arriving -->")
        return real_read(self, entry, max_bytes, expected)

    monkeypatch.setattr(import_directory.ImportDirectory, "read", read_after_a_write)


def _track_count(services: TrackServices) -> int:
    """Return how many current tracks the archive holds."""
    return len(services.store.list_tracks(TrackQuery()).tracks)


# --- Discovery ---------------------------------------------------------------


def test_gpx_files_are_discovered_and_imported(tmp_path: Path, inbox: Path) -> None:
    """A file dropped into the folder becomes a track without anybody asking."""
    first = _put(inbox, "morning.gpx")
    second = _put(inbox, "evening.gpx", "generic-external-link.gpx")
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(first, second)).scan()

    assert [name for name, _ in scan.offered] == ["evening.gpx", "morning.gpx"]
    assert scan.count(ImportStatus.IMPORTED) == 2
    assert _track_count(services) == 2


def test_unrelated_files_are_not_discovered(tmp_path: Path, inbox: Path) -> None:
    """A photo or a note next to the tracks is neither read nor reported."""
    track = _put(inbox, "ride.gpx")
    (inbox / "notes.txt").write_text("not a track", encoding="utf-8")
    (inbox / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(track)).scan()

    assert scan.discovered == 1
    assert [name for name, _ in scan.offered] == ["ride.gpx"]


def test_an_upper_case_suffix_is_discovered(tmp_path: Path, inbox: Path) -> None:
    """``RIDE.GPX`` is the same kind of file as ``ride.gpx``."""
    track = _put(inbox, "RIDE.GPX")
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(track)).scan()

    assert scan.count(ImportStatus.IMPORTED) == 1


def test_a_file_name_that_is_not_utf_8_is_imported_under_a_readable_name(
    tmp_path: Path, inbox: Path
) -> None:
    """A name is bytes, and an old Windows share writes Latin-1 ones.

    Such a name cannot be stored, logged or sent as it is. The file is still a
    track: it is read under its real name and recorded under a readable one.
    """
    raw_name = os.fsencode(inbox) + b"/M\xfcnchen.gpx"
    try:
        shutil.copyfile(FIXTURES / "recorded-measurements.gpx", raw_name)
    except OSError:
        pytest.skip("this file system refuses names that are not UTF-8")
    services = _services(tmp_path, inbox)
    scanner = _scanner(services, inbox, _settled(Path(os.fsdecode(raw_name))))

    scan = scanner.scan()
    again = scanner.scan()

    ((name, outcome),) = scan.offered
    assert name == "M\ufffdnchen.gpx"
    assert outcome.status is ImportStatus.IMPORTED
    raw = services.store.find_raw_import(outcome.sha256)
    assert raw is not None
    assert raw.original_filename == "M\ufffdnchen.gpx"
    assert again.unchanged == 1


# --- Idempotency -------------------------------------------------------------


def test_scanning_again_creates_no_duplicate(tmp_path: Path, inbox: Path) -> None:
    """A folder the phone never clears must not become forty copies of one ride."""
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    scanner = _scanner(services, inbox, _settled(track))
    scanner.scan()

    again = scanner.scan()

    assert again.offered == ()
    assert again.unchanged == 1
    assert again.skipped == 1
    assert _track_count(services) == 1


def test_a_folder_that_cannot_be_opened_is_reported_and_forgets_nothing(
    tmp_path: Path, inbox: Path
) -> None:
    """An unmounted share is not an empty folder.

    A scan that could not open the folder says so rather than "nothing new",
    and a share that comes back unchanged is not read all over again.
    """
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    scanner = _scanner(services, inbox, _settled(track))
    scanner.scan()

    inbox.rename(tmp_path / "unmounted")
    gone = scanner.scan()
    (tmp_path / "unmounted").rename(inbox)
    back = scanner.scan()

    assert gone.unavailable is True
    assert gone.discovered == 0
    assert back.unavailable is False
    assert back.unchanged == 1


def test_a_restart_does_not_import_again(tmp_path: Path, inbox: Path) -> None:
    """What was imported is a fact of the archive, not of the process that did it.

    A new process remembers nothing about the folder. It offers every file once
    more, and the archive's own content hash recognises each of them.
    """
    track = _put(inbox, "ride.gpx")
    clock = _settled(track)
    before = _scanner(_services(tmp_path, inbox), inbox, clock).scan()

    restarted = _services(tmp_path, inbox)
    after = _scanner(restarted, inbox, clock).scan()

    assert before.count(ImportStatus.IMPORTED) == 1
    assert [outcome.status for _, outcome in after.offered] == [ImportStatus.DUPLICATE]
    assert _track_count(restarted) == 1


def test_a_file_imported_by_hand_is_recognised(tmp_path: Path, inbox: Path) -> None:
    """Manual and automatic import are one pipeline, so they know each other's work."""
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    assert main(["import", str(track)], settings=services.settings) == EXIT_OK
    (existing,) = services.store.list_tracks(TrackQuery()).tracks

    scan = _scanner(services, inbox, _settled(track)).scan()

    ((_, outcome),) = scan.offered
    assert outcome.status is ImportStatus.DUPLICATE
    assert outcome.track_ids == (existing.track_id,)
    assert _track_count(services) == 1


def test_a_file_whose_content_changed_is_offered_again(tmp_path: Path, inbox: Path) -> None:
    """A remembered file is skipped only while it is still the same file."""
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    clock = _settled(track)
    scanner = _scanner(services, inbox, clock)
    scanner.scan()

    shutil.copy(FIXTURES / "generic-external-link.gpx", track)
    clock.instant = _settled(track).instant
    again = scanner.scan()

    assert again.count(ImportStatus.IMPORTED) == 1
    assert _track_count(services) == 2


def test_automatic_and_manual_import_produce_the_same_track(tmp_path: Path) -> None:
    """The canonical track does not depend on which way the file came in."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    track = _put(inbox, "ride.gpx")
    manual = build_services(Settings(data_dir=tmp_path / "manual"))
    assert main(["import", str(track)], settings=manual.settings) == EXIT_OK
    automatic = _services(tmp_path, inbox)

    _scanner(automatic, inbox, _settled(track)).scan()

    (by_hand,) = manual.store.list_tracks(TrackQuery()).tracks
    (scanned,) = automatic.store.list_tracks(TrackQuery()).tracks
    assert scanned.source_key == by_hand.source_key
    assert scanned.classification == by_hand.classification
    assert scanned.activity == by_hand.activity
    assert (scanned.started_at, scanned.ended_at) == (by_hand.started_at, by_hand.ended_at)
    assert automatic.store.get_geometry(scanned.track_id) == manual.store.get_geometry(
        by_hand.track_id
    )


# --- File stability ----------------------------------------------------------


def test_a_file_still_being_written_is_not_imported(tmp_path: Path, inbox: Path) -> None:
    """A half-copied file is not a short track; it is not a track yet."""
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    moment_later = SettableClock(_changed_at(track) + timedelta(seconds=2))

    scan = _scanner(services, inbox, moment_later).scan()

    assert scan.waiting == ("ride.gpx",)
    assert scan.offered == ()
    assert services.store.processing_snapshots() == ()


def test_a_file_is_imported_once_it_has_settled(tmp_path: Path, inbox: Path) -> None:
    """Waiting is a delay, not a verdict: the next scan takes the finished file."""
    track = _put(inbox, "ride.gpx")
    services = _services(tmp_path, inbox)
    clock = SettableClock(_changed_at(track) + timedelta(seconds=2))
    scanner = _scanner(services, inbox, clock)
    scanner.scan()

    clock.instant = _settled(track).instant
    scan = scanner.scan()

    assert scan.waiting == ()
    assert scan.count(ImportStatus.IMPORTED) == 1


def test_a_file_that_changes_after_it_was_looked_at_is_not_read(inbox: Path) -> None:
    """The bytes offered are the bytes of the file that was looked at, or none.

    A writer that pauses longer than the settle time and then carries on must
    not leave the archive holding the first half. Here the change lands between
    looking at the file and opening it.
    """
    track = _put(inbox, "ride.gpx")
    with import_directory.open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()
        looked_at = directory.state(entry)
        assert looked_at is not None

        with track.open("ab") as writer:
            writer.write(b"<!-- still arriving -->")

        assert directory.read(entry, 1 << 20, expected=looked_at) is None
        assert directory.read(entry, 1 << 20, expected=directory.state(entry)) is not None


def test_a_file_that_changes_while_it_is_being_read_is_not_offered(
    inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that lands during the read is caught after it, on the open file.

    The write is made at the one moment the check looks again, which is what
    a writer racing the read looks like from inside it.
    """
    track = _put(inbox, "ride.gpx")
    real_fstat = os.fstat
    looks: list[int] = []

    def written_to_before_the_second_look(descriptor: int) -> os.stat_result:
        looks.append(descriptor)
        if len(looks) == 2:
            with track.open("ab") as writer:
                writer.write(b"<!-- still arriving -->")
        return real_fstat(descriptor)

    with import_directory.open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()
        looked_at = directory.state(entry)
        monkeypatch.setattr(import_directory.os, "fstat", written_to_before_the_second_look)

        assert directory.read(entry, 1 << 20, expected=looked_at) is None
        assert len(looks) == 2


def test_a_file_that_changes_under_the_scan_is_waiting_not_unreadable(
    tmp_path: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read refused because the file moved on is a delay, not a broken file."""
    track = _put(inbox, "ride.gpx")
    _append_before_every_read(track, monkeypatch)
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(track)).scan()

    assert scan.waiting == ("ride.gpx",)
    assert scan.unreadable == ()
    assert scan.failures == ()
    assert services.store.processing_snapshots() == ()


# --- Failures ----------------------------------------------------------------


def test_an_invalid_gpx_file_does_not_stop_the_scan(tmp_path: Path, inbox: Path) -> None:
    """One broken file is one failed file, and the files after it still arrive."""
    broken = _put(inbox, "a-broken.gpx", "malformed.gpx")
    valid = _put(inbox, "b-valid.gpx")
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(broken, valid)).scan()

    assert dict(scan.offered)["a-broken.gpx"].error_code is ImportErrorCode.INVALID_GPX
    assert dict(scan.offered)["b-valid.gpx"].status is ImportStatus.IMPORTED
    assert scan.failures == (("a-broken.gpx", ImportErrorCode.INVALID_GPX),)


def test_an_unreadable_file_is_reported_and_the_rest_imported(
    tmp_path: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file the archive cannot open is named, not silently dropped.

    The failure is injected at the open, as in the directory's own boundary
    tests, so the contract does not depend on which user runs the suite.
    """
    locked = _put(inbox, "a-locked.gpx")
    valid = _put(inbox, "b-valid.gpx", "generic-external-link.gpx")
    real_open = os.open

    def refuse_one(path: object, flags: int, /, *args: object, **kwargs: object) -> int:
        if path == "a-locked.gpx":
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(import_directory.os, "open", refuse_one)
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(locked, valid)).scan()

    assert scan.unreadable == ("a-locked.gpx",)
    assert scan.failures == (("a-locked.gpx", None),)
    assert dict(scan.offered)["b-valid.gpx"].status is ImportStatus.IMPORTED


def test_a_file_that_breaks_the_import_does_not_keep_the_others_out(
    tmp_path: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter defect on one document is that document's problem.

    Files are scanned in name order, so a file that broke the whole pass would
    break it again on every pass and keep everything after it out for good. The
    defect is injected, because no committed fixture is supposed to trigger one.
    """
    broken = _put(inbox, "a-breaks.gpx", "generic-external-link.gpx")
    valid = _put(inbox, "b-valid.gpx")
    _break_the_import_of(broken.read_bytes(), monkeypatch)
    services = _services(tmp_path, inbox)
    scanner = _scanner(services, inbox, _settled(broken, valid))

    scan = scanner.scan()
    again = scanner.scan()

    assert scan.crashed == ("a-breaks.gpx",)
    assert scan.failures == (("a-breaks.gpx", None),)
    assert dict(scan.offered)["b-valid.gpx"].status is ImportStatus.IMPORTED
    assert again.crashed == ("a-breaks.gpx",), "a broken import is tried again, not remembered"


def test_a_file_that_cannot_even_be_looked_at_is_reported(
    tmp_path: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder that lists names but refuses to show them is not an empty folder.

    A directory with read but no search permission does exactly this. The
    refusal is injected at the look, so the contract does not depend on which
    user runs the suite.
    """
    refused = _put(inbox, "a-refused.gpx")
    valid = _put(inbox, "b-valid.gpx", "generic-external-link.gpx")
    clock = _settled(refused, valid)
    real_stat = os.stat

    def refuse_one(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if path == "a-refused.gpx":
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(import_directory.os, "stat", refuse_one)
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, clock).scan()

    assert scan.unreadable == ("a-refused.gpx",)
    assert scan.failures == (("a-refused.gpx", None),)
    assert dict(scan.offered)["b-valid.gpx"].status is ImportStatus.IMPORTED


def test_an_empty_file_is_waiting_rather_than_imported(tmp_path: Path, inbox: Path) -> None:
    """Sync tools create the name before the content; an empty file is not a document yet.

    Offering it would store a raw import of nothing, which the upload endpoint
    refuses for the same reason.
    """
    placeholder = inbox / "placeholder.gpx"
    placeholder.write_bytes(b"")
    services = _services(tmp_path, inbox)

    scan = _scanner(services, inbox, _settled(placeholder)).scan()

    assert scan.waiting == ("placeholder.gpx",)
    assert scan.offered == ()
    assert services.store.processing_snapshots() == ()


def test_a_file_name_cannot_forge_a_log_line(
    tmp_path: Path, inbox: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The folder is untrusted, and so are the names in it -- including in the log."""
    forged = inbox / "evil\nINFO import.accepted name=fake.gpx.gpx"
    shutil.copy(FIXTURES / "malformed.gpx", forged)
    services = _services(tmp_path, inbox)

    with caplog.at_level(logging.DEBUG, logger="trackvault"):
        _scanner(services, inbox, _settled(forged)).scan()

    messages = [record.getMessage() for record in caplog.records if "evil" in record.getMessage()]
    assert messages
    assert all("\n" not in message for message in messages)


def test_a_scan_that_is_asked_to_stop_stops_between_files(tmp_path: Path, inbox: Path) -> None:
    """Shutting down waits for the file in hand, not for the whole folder."""
    first = _put(inbox, "a.gpx")
    second = _put(inbox, "b.gpx", "generic-external-link.gpx")
    services = _services(tmp_path, inbox)
    answers = iter([False, True])

    scan = _scanner(services, inbox, _settled(first, second)).scan(stopping=lambda: next(answers))

    assert [name for name, _ in scan.offered] == ["a.gpx"]
    assert _track_count(services) == 1
