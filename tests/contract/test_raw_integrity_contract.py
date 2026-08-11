"""Executable contract for raw import integrity.

A raw import is two things that have to agree: a row saying the archive holds
these bytes, and the bytes themselves, under a path named after their digest.
Recognising the row proves only that the archive *once* held them.

```
DB metadata + hash-valid managed artifact   -> healthy duplicate
DB metadata + missing artifact              -> repairable from the same bytes
DB metadata + wrong bytes                   -> fail closed, repair nothing
artifact without DB metadata                -> reuse the bytes, record the row
```

The failure this guards against is quiet: a duplicate is reported as "nothing to
do", so an archive that lost an artifact would keep answering "already imported"
to the only offer of those bytes it will ever get again.
"""

import hashlib
from pathlib import Path

import pytest

from gpx_view.application import ImportErrorCode, TrackImportError
from gpx_view.application.import_tracks import ImportOutcome, ImportRequest, ImportStatus
from gpx_view.application.ports import RawArtifactState
from gpx_view.config import Settings
from gpx_view.infrastructure.assembly import TrackServices, build_services
from gpx_view.infrastructure.filesystem import raw_store as raw_store_module

pytestmark = [pytest.mark.contract, pytest.mark.storage, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

SOURCE = "recorded-measurements.gpx"


@pytest.fixture
def archive(tmp_path: Path) -> TrackServices:
    """Return a wired archive in a throwaway data directory."""
    services = build_services(Settings(data_dir=tmp_path / "data"))
    services.prepare_storage()
    return services


def _content(name: str = SOURCE) -> bytes:
    """Return the bytes of a synthetic fixture."""
    return (FIXTURES / name).read_bytes()


def _offer(archive: TrackServices, content: bytes | None = None) -> ImportOutcome:
    """Offer bytes to the one canonical import use case."""
    return archive.import_tracks(
        ImportRequest(content=_content() if content is None else content, original_filename=SOURCE)
    )


# --- A duplicate is only healthy while its evidence is ------------------------


def test_offering_known_bytes_whose_artifact_is_healthy_is_a_duplicate(
    archive: TrackServices,
) -> None:
    """The ordinary case stays a cheap no-op."""
    first = _offer(archive)

    again = _offer(archive)

    assert first.status is ImportStatus.IMPORTED
    assert again.status is ImportStatus.DUPLICATE
    assert again.track_ids == first.track_ids
    assert archive.raw_store.integrity(again.sha256) is RawArtifactState.HEALTHY


def test_a_missing_managed_artifact_is_not_reported_as_a_healthy_duplicate(
    archive: TrackServices,
) -> None:
    """A row without its bytes is not "already imported", it is damage.

    The archive kept the row saying it holds these bytes and lost the bytes. If
    that answers `DUPLICATE`, the one moment the source is offered again -- the
    only chance to recover -- passes silently.
    """
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).unlink()

    again = _offer(archive)

    assert again.status is not ImportStatus.DUPLICATE


def test_re_offering_the_same_bytes_restores_a_missing_managed_artifact(
    archive: TrackServices,
) -> None:
    """The same source evidence was offered again, so it can be put back.

    Recovery is safe because it is not a guess: the bytes hash to the digest the
    raw import is filed under, which is the same proof the first import needed.
    """
    first = _offer(archive)
    original = archive.raw_store.read(first.sha256)
    archive.raw_store.path_for(first.sha256).unlink()

    repaired = _offer(archive)

    assert repaired.status is ImportStatus.REPAIRED
    assert repaired.sha256 == first.sha256
    assert repaired.track_ids == first.track_ids
    assert archive.raw_store.read(first.sha256) == original


def test_a_repair_does_not_append_a_processing_run(archive: TrackServices) -> None:
    """Putting bytes back is a storage repair, not a reprocessing.

    The normalized generation was never in question, so nothing about it changed
    and the history must not suggest otherwise.
    """
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).unlink()

    _offer(archive)

    assert archive.store.run_count(first.sha256) == 1


def test_a_corrupt_managed_artifact_is_never_silently_overwritten(
    archive: TrackServices,
) -> None:
    """Wrong bytes under a digest are evidence of a problem, not a stale cache.

    Disk corruption, tampering and a broken file system all look like this, and
    replacing the artifact would destroy the only trace of any of them.
    """
    first = _offer(archive)
    damaged = b"<gpx>these are not the bytes this hash names</gpx>"
    archive.raw_store.path_for(first.sha256).write_bytes(damaged)

    again = _offer(archive)

    assert again.status is ImportStatus.FAILED
    assert again.error_code is ImportErrorCode.RAW_STORAGE_CORRUPT
    assert archive.raw_store.path_for(first.sha256).read_bytes() == damaged


def test_a_corrupt_artifact_is_reported_as_corrupt_rather_than_missing(
    archive: TrackServices,
) -> None:
    """One integrity question, one answer, wherever it is asked."""
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).write_bytes(b"wrong")

    assert archive.raw_store.integrity(first.sha256) is RawArtifactState.CORRUPT


def test_an_absent_artifact_is_reported_as_missing(archive: TrackServices) -> None:
    """Absence and corruption are different problems with different recoveries."""
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).unlink()

    assert archive.raw_store.integrity(first.sha256) is RawArtifactState.MISSING


def test_an_unknown_hash_is_reported_as_missing(archive: TrackServices) -> None:
    """Asking about bytes the archive never held is answered, not guessed at.

    Both shapes of absence: a store that has never written anything, and a store
    whose fan-out directory for that hash simply does not exist.
    """
    assert archive.raw_store.integrity("b" * 64) is RawArtifactState.MISSING

    _offer(archive)

    assert archive.raw_store.integrity("b" * 64) is RawArtifactState.MISSING


def test_a_hash_that_could_name_nothing_is_reported_as_missing(
    archive: TrackServices,
) -> None:
    """A value that is not a digest names no artifact, so nothing is stored under it."""
    assert archive.raw_store.integrity("not-a-digest") is RawArtifactState.MISSING


def test_an_artifact_the_archive_cannot_look_at_is_reported_as_unreadable(
    archive: TrackServices,
) -> None:
    """A damaged storage tree is a third answer, not a quiet "no artifact".

    Reporting it as missing would send the import into repair -- writing bytes
    into a tree that is already broken -- instead of stopping and saying so.
    """
    first = _offer(archive)
    fanout = archive.raw_store.path_for(first.sha256).parent
    for artifact in fanout.iterdir():
        artifact.unlink()
    fanout.rmdir()
    fanout.write_bytes(b"a file where a directory belongs")

    assert archive.raw_store.integrity(first.sha256) is RawArtifactState.UNREADABLE
    with pytest.raises(TrackImportError) as raised:
        archive.raw_store.read(first.sha256)
    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED


def test_reading_a_hash_whose_directory_exists_but_holds_nothing_is_missing(
    archive: TrackServices,
) -> None:
    """Absence inside an existing fan-out directory is still absence."""
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).unlink()

    with pytest.raises(TrackImportError) as raised:
        archive.raw_store.read(first.sha256)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_MISSING


def test_a_write_that_fails_leaves_no_partial_artifact(
    archive: TrackServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed import must not leave a half-written file under a content hash.

    A name derived from a digest promises that the bytes have that digest. A
    partial file under it would be a corrupt artifact the store itself created.
    """

    def fail_the_rename(*_: object, **__: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(raw_store_module.os, "replace", fail_the_rename)

    outcome = _offer(archive)

    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code is ImportErrorCode.RAW_STORAGE_FAILED
    assert not list(archive.raw_store.root.rglob("*.raw"))
    assert not list(archive.raw_store.root.rglob("*.part"))


def test_a_repair_that_cannot_be_written_is_reported_rather_than_claimed(
    archive: TrackServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery that did not happen must not be reported as recovery."""
    first = _offer(archive)
    archive.raw_store.path_for(first.sha256).unlink()

    def fail_the_rename(*_: object, **__: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(raw_store_module.os, "replace", fail_the_rename)
    again = _offer(archive)

    assert again.status is ImportStatus.FAILED
    assert again.error_code is ImportErrorCode.RAW_STORAGE_FAILED
    assert archive.raw_store.integrity(first.sha256) is RawArtifactState.MISSING


# --- An artifact without its row ---------------------------------------------


def test_an_orphan_artifact_is_reused_rather_than_written_again(archive: TrackServices) -> None:
    """Bytes may outlive their row: storing them and committing are not one act.

    An import writes the managed artifact first and commits the database
    transaction second, so a crash in between leaves a hash-valid artifact that
    nothing references. Offering the same source again must adopt it -- the
    content is proven identical -- instead of failing or duplicating the bytes.
    """
    content = _content()
    sha256 = hashlib.sha256(content).hexdigest()
    archive.raw_store.store(content, sha256)
    assert archive.store.find_raw_import(sha256) is None

    outcome = _offer(archive)

    assert outcome.status is ImportStatus.IMPORTED
    assert archive.store.find_raw_import(sha256) is not None
    assert len(list(archive.raw_store.root.rglob("*.raw"))) == 1


def test_an_orphan_artifact_that_does_not_match_its_hash_fails_closed(
    archive: TrackServices,
) -> None:
    """A damaged orphan is still damaged. Nothing overwrites it."""
    content = _content()
    sha256 = hashlib.sha256(content).hexdigest()
    path = archive.raw_store.path_for(sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not the bytes this hash names")

    outcome = _offer(archive)

    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code is ImportErrorCode.RAW_STORAGE_CORRUPT
    assert archive.store.find_raw_import(sha256) is None


# --- The store is the authority on its own bytes ------------------------------


def test_content_that_does_not_match_the_stated_hash_is_refused(
    archive: TrackServices,
) -> None:
    """An invariant that holds only because of who calls the port is not one."""
    with pytest.raises(TrackImportError) as raised:
        archive.raw_store.store(b"some bytes", "c" * 64)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED
