"""Local regression against the developer's real reference tracks.

These files are private movement data. They live under ``./import-tracks``, the
whole directory is git-ignored, and nothing here may ever be committed, copied
into a fixture, or printed.

Run them deliberately:

```
uv run pytest -m local_tracks
```

They are **not** part of the default suite and CI must never need them. Everything
they check also exists as a contract against small synthetic fixtures; what they
add is proof that those generic rules survive contact with real data -- a
recording that carries what a real receiver writes, and a planned route that
carries what a real planner writes.

The direction of that proof matters. These two files are regression evidence, not
a specification: no production rule may be tuned so that *they* come out a certain
way, no filename may become a classification signal, and a conservative `UNKNOWN`
for the planned route is an acceptable result rather than a failure.

Nothing here asserts or reports a coordinate, a timestamp of an actual movement,
or file content. Counts, evidence codes, verdicts and hashes are the whole
vocabulary.
"""

import hashlib
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application import ImportLimits
from gpx_view.application.import_tracks import ImportRequest, ImportStatus
from gpx_view.application.ports import RawArtifactState, TrackSummary
from gpx_view.application.reprocess import ReprocessStatus
from gpx_view.config import Settings
from gpx_view.domain import (
    Activity,
    ClassificationResult,
    EvidenceCode,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)
from gpx_view.infrastructure.assembly import (
    TrackServices,
    build_services,
    import_limits_from,
)
from gpx_view.infrastructure.gpx import GpxImporter
from gpx_view.infrastructure.gpx.extensions import (
    GARMIN_TRACK_POINT_EXTENSION_V2,
    LOCUS_MAP_EXTENSIONS,
)

pytestmark = [pytest.mark.local_tracks, pytest.mark.integration]

LOCAL_TRACKS = Path(__file__).resolve().parents[2] / "import-tracks"

RECORDED_REFERENCE = "2025-10-20_Mallorca_talaia_d_alcudia.gpx"
PLANNED_REFERENCE = "Coll_de_na_Benet__Ermita_de_la_Victòria_Runde_von_Bonaire.gpx"


@dataclass(frozen=True, slots=True)
class Reference:
    """One real file and the structural facts it is known to carry.

    Only structure. What the classifier makes of it is a result to be observed,
    not an expectation to be encoded here -- that is the difference between
    regression evidence and a specification.
    """

    filename: str
    tracks: int
    segments: int
    points: int


RECORDED = Reference(filename=RECORDED_REFERENCE, tracks=1, segments=1, points=831)
PLANNED = Reference(filename=PLANNED_REFERENCE, tracks=1, segments=1, points=391)

REFERENCES = (RECORDED, PLANNED)


def _limits() -> ImportLimits:
    """Return the limits a default deployment enforces."""
    return import_limits_from(Settings())


def _path(reference: Reference) -> Path:
    """Return the reference's path, skipping if this machine does not have it."""
    path = LOCAL_TRACKS / reference.filename
    if not path.is_file():
        pytest.skip(f"local reference {reference.filename} is not available")
    return path


@pytest.fixture
def archive(tmp_path: Path) -> TrackServices:
    """Return a wired archive in a throwaway data directory.

    The real files are read; nothing is ever written near them.
    """
    services = build_services(Settings(data_dir=tmp_path / "data"))
    services.prepare_storage()
    return services


@pytest.fixture(params=REFERENCES, ids=lambda reference: reference.filename[:24])
def reference(request: pytest.FixtureRequest) -> Reference:
    """Yield each reference track in turn."""
    assert isinstance(request.param, Reference)
    return request.param


def _import(archive: TrackServices, path: Path) -> tuple[str, tuple[int, ...]]:
    """Import a real file and return its content hash and track identities."""
    outcome = archive.import_tracks(
        ImportRequest(content=path.read_bytes(), original_filename=path.name)
    )
    assert outcome.status is ImportStatus.IMPORTED
    return outcome.sha256, outcome.track_ids


def _summaries(archive: TrackServices, sha256: str) -> Iterator[TrackSummary]:
    """Yield the current tracks of one raw import."""
    for track_id in archive.store.track_ids_for(sha256):
        summary = archive.store.get_track(track_id)
        assert summary is not None
        yield summary


def _record_an_older_generation(archive: TrackServices, sha256: str) -> None:
    """Make the current generation claim the processing of the first build."""
    raw = archive.store.find_raw_import(sha256)
    assert raw is not None
    archive.store.record_import(
        raw,
        ProcessingRun(
            raw_import_sha256=sha256,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=1,
            processed_at=datetime(2026, 1, 1, tzinfo=UTC),
            status=ProcessingStatus.SUCCEEDED,
            classifier="evidence-weights",
            classifier_version="1",
        ),
        [
            NormalizedTrack(
                source_key="trk:0",
                segments=(TrackSegment(points=(TrackPoint(latitude=0.0, longitude=0.0),)),),
                source=SourceMetadata(exchange_format="gpx", format_version="1.1"),
                classification=TrackClassification(
                    detected=ClassificationResult(
                        kind=TrackKind.UNKNOWN,
                        confidence=0.0,
                        method="evidence-weights",
                        method_version="1",
                    )
                ),
            )
        ],
    )


# --- The adapter reads what these files actually contain ---------------------


def test_a_reference_track_is_detected_as_gpx(reference: Reference) -> None:
    """Real files are recognised from their content, like any other."""
    assert GpxImporter().detects(_path(reference).read_bytes())


def test_a_reference_track_normalizes_to_its_known_structure(reference: Reference) -> None:
    """Track, segment and point counts are the structural regression."""
    candidates = GpxImporter().import_tracks(_path(reference).read_bytes(), _limits())

    assert len(candidates) == reference.tracks
    assert sum(candidate.segment_count for candidate in candidates) == reference.segments
    assert sum(candidate.point_count for candidate in candidates) == reference.points


def test_the_recorded_reference_states_its_activity() -> None:
    """The recording's activity metadata normalizes onto the shared taxonomy.

    This is what "source metadata is evidence, not authority" looks like from the
    other side: the source stated an activity, so the activity is known. It says
    nothing about the track kind.
    """
    (candidate,) = GpxImporter().import_tracks(_path(RECORDED).read_bytes(), _limits())

    assert candidate.activity is Activity.WALKING


def test_the_recorded_reference_carries_measurement_evidence() -> None:
    """A real receiver reports how well it measured, and that is the signal."""
    (candidate,) = GpxImporter().import_tracks(_path(RECORDED).read_bytes(), _limits())

    assert EvidenceCode.GPS_ACCURACY_PRESENT in candidate.evidence
    assert EvidenceCode.COURSE_MEASUREMENTS_PRESENT in candidate.evidence
    assert EvidenceCode.MEASUREMENT_METADATA_ABSENT not in candidate.evidence


def test_the_planned_reference_carries_no_measurement_evidence() -> None:
    """A planner has nothing to report about how well it measured."""
    (candidate,) = GpxImporter().import_tracks(_path(PLANNED).read_bytes(), _limits())

    assert EvidenceCode.MEASUREMENT_METADATA_ABSENT in candidate.evidence
    assert EvidenceCode.GPS_ACCURACY_PRESENT not in candidate.evidence
    assert EvidenceCode.COURSE_MEASUREMENTS_PRESENT not in candidate.evidence


# --- What the classifier makes of them ---------------------------------------


def test_the_recorded_reference_is_classified_recorded(archive: TrackServices) -> None:
    """The generic measurement rule still recognises a real recording.

    Nothing about this file is special-cased. It comes out `RECORDED` because a
    device wrote receiver quality and headings into it, which is the same reason
    the synthetic fixture does.
    """
    sha256, _ = _import(archive, _path(RECORDED))

    (summary,) = _summaries(archive, sha256)
    assert summary.detected_kind is TrackKind.RECORDED
    assert summary.effective_kind is TrackKind.RECORDED
    assert summary.classification.detected.confidence > 0.5


def test_the_planned_reference_is_planned_or_honestly_unknown(archive: TrackServices) -> None:
    """`UNKNOWN` is an acceptable answer here, and deliberately so.

    The file carries timestamps, no measurement metadata and an external link.
    Since a link decides nothing, the remaining evidence is an absence -- and an
    absence describes a recording stripped of its metadata just as well. Whatever
    a human knows about this file, the data does not say it.

    `PLANNED` is allowed only if it follows from generic evidence the rules
    already weigh. What is forbidden is inventing a rule so that this one file
    comes out `PLANNED`; a user override exists for exactly that.
    """
    sha256, _ = _import(archive, _path(PLANNED))

    (summary,) = _summaries(archive, sha256)
    assert summary.detected_kind in (TrackKind.PLANNED, TrackKind.UNKNOWN)
    if summary.detected_kind is TrackKind.UNKNOWN:
        assert summary.classification.detected.confidence == pytest.approx(0.0)
    else:
        assert summary.classification.detected.confidence > 0.5


def test_a_verdict_on_a_real_file_still_states_its_evidence(
    archive: TrackServices, reference: Reference
) -> None:
    """A stored verdict names its evidence and the classifier that reached it.

    That is what makes a result revisitable: when the rules change, an operator
    can see which observation the old answer rested on.
    """
    sha256, _ = _import(archive, _path(reference))

    (summary,) = _summaries(archive, sha256)
    detected = summary.classification.detected
    assert detected.evidence
    assert detected.method
    assert detected.method_version
    if detected.kind is not TrackKind.UNKNOWN:
        assert 0.0 < detected.confidence <= 1.0


# --- The raw import pipeline against real bytes ------------------------------


def test_the_managed_copy_is_byte_identical_to_the_source(
    archive: TrackServices, reference: Reference
) -> None:
    """The archive keeps the original, not a re-serialisation of it."""
    path = _path(reference)
    original = path.read_bytes()
    expected_digest = hashlib.sha256(original).hexdigest()

    sha256, _ = _import(archive, path)

    assert sha256 == expected_digest
    stored = archive.raw_store.read(sha256)
    assert stored == original
    assert hashlib.sha256(stored).hexdigest() == expected_digest


def test_importing_never_touches_the_source_file(
    archive: TrackServices, reference: Reference
) -> None:
    """The developer's own files are input. GPX-View does not write to them.

    Content and modification time are both checked: a tool that rewrote a file
    identically would still have touched data the owner did not ask it to touch.
    """
    path = _path(reference)
    before_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    before_mtime = path.stat().st_mtime_ns

    sha256, _ = _import(archive, path)
    archive.reprocess(sha256)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_offering_a_real_file_twice_is_idempotent(
    archive: TrackServices, reference: Reference
) -> None:
    """A sync tool putting the same file back costs nothing."""
    path = _path(reference)
    sha256, first_ids = _import(archive, path)

    again = archive.import_tracks(ImportRequest(content=path.read_bytes()))

    assert again.status is ImportStatus.DUPLICATE
    assert again.sha256 == sha256
    assert again.track_ids == first_ids
    assert len(list(archive.raw_store.root.rglob("*.raw"))) == 1


# --- Reprocessing against real bytes -----------------------------------------


def test_a_real_source_reprocesses_from_its_managed_copy(
    archive: TrackServices, reference: Reference
) -> None:
    """Regeneration reads the archive's own copy and produces a new generation."""
    sha256, _ = _import(archive, _path(reference))

    outcome = archive.reprocess(sha256)

    assert outcome.status is ReprocessStatus.REPROCESSED
    assert len(outcome.track_ids) == reference.tracks
    assert archive.store.run_count(sha256) == 2
    latest = archive.store.latest_run(sha256)
    assert latest is not None
    assert latest.status is ProcessingStatus.SUCCEEDED


def test_a_real_candidate_keeps_its_identity_across_reprocessing(
    archive: TrackServices, reference: Reference
) -> None:
    """The same document describes the same candidates, however often it is read."""
    sha256, _ = _import(archive, _path(reference))
    before = [summary.source_key for summary in _summaries(archive, sha256)]

    archive.reprocess(sha256)

    assert [summary.source_key for summary in _summaries(archive, sha256)] == before
    assert before == ["trk:0"]


def test_a_correction_on_a_real_track_survives_reprocessing(
    archive: TrackServices, reference: Reference
) -> None:
    """The one thing an operator must be able to rely on after an upgrade."""
    sha256, (track_id,) = _import(archive, _path(reference))
    archive.store.set_override(track_id, TrackKind.PLANNED, datetime(2026, 8, 8, tzinfo=UTC))

    archive.reprocess(sha256)

    (summary,) = _summaries(archive, sha256)
    assert summary.effective_kind is TrackKind.PLANNED
    assert summary.classification.is_overridden


def test_reprocessing_a_real_source_keeps_its_history_and_its_bytes(
    archive: TrackServices, reference: Reference
) -> None:
    """History is append-only and the source is immutable, on real data too."""
    path = _path(reference)
    original = path.read_bytes()
    sha256, _ = _import(archive, path)
    raw_before = archive.store.find_raw_import(sha256)

    archive.reprocess(sha256)
    archive.reprocess(sha256)

    assert archive.store.run_count(sha256) == 3
    assert archive.store.find_raw_import(sha256) == raw_before
    assert archive.raw_store.read(sha256) == original


# --- The extension schemas these files actually use --------------------------


def test_the_recorded_reference_uses_the_extension_schemas_this_build_reads() -> None:
    """The namespace-aware table is exercised by real data, not only by fixtures.

    A table of schemas nobody writes is a guess. These are the namespaces the
    developer's own recorder emits, and they are why the file's activity and its
    heading are read at all.
    """
    (candidate,) = GpxImporter().import_tracks(_path(RECORDED).read_bytes(), _limits())

    assert GARMIN_TRACK_POINT_EXTENSION_V2 in candidate.source.extension_namespaces
    assert LOCUS_MAP_EXTENSIONS in candidate.source.extension_namespaces


def test_a_real_recording_still_states_its_activity_and_heading() -> None:
    """Namespace awareness must not cost the readings it was introduced to protect."""
    (candidate,) = GpxImporter().import_tracks(_path(RECORDED).read_bytes(), _limits())

    assert candidate.activity is Activity.WALKING
    assert EvidenceCode.ACTIVITY_METADATA_PRESENT in candidate.evidence
    assert EvidenceCode.COURSE_MEASUREMENTS_PRESENT in candidate.evidence


# --- Processing currency on real data ----------------------------------------


def test_a_real_import_is_current_immediately(archive: TrackServices, reference: Reference) -> None:
    """What the installed processing just produced cannot be out of date."""
    sha256, _ = _import(archive, _path(reference))

    report = archive.processing_status(sha256)
    assert report is not None
    assert report.is_outdated is False
    assert report.current_profile == report.installed_profile
    assert archive.reprocess.outdated_sources() == ()


def test_a_real_generation_from_an_older_profile_is_reprocessed_and_becomes_current(
    archive: TrackServices, reference: Reference
) -> None:
    """The upgrade path an existing archive will actually take, on real bytes.

    The old generation is simulated by recording what an earlier build would have
    recorded. What makes it outdated is the profile the run states, not how the
    normalized data was computed, so no checkout of the old implementation is
    involved.
    """
    sha256, _ = _import(archive, _path(reference))
    _record_an_older_generation(archive, sha256)
    assert archive.reprocess.outdated_sources() == (sha256,)

    outcome = archive.reprocess(sha256)

    assert outcome.status is ReprocessStatus.REPROCESSED
    assert archive.reprocess.outdated_sources() == ()
    report = archive.processing_status(sha256)
    assert report is not None
    assert report.is_outdated is False
    assert report.track_count == reference.tracks


# --- Raw integrity and privacy on real data ----------------------------------


def test_the_managed_copy_of_a_real_recording_is_healthy_and_private(
    archive: TrackServices, reference: Reference
) -> None:
    """Real movement data is stored verified and readable by its owner alone."""
    sha256, _ = _import(archive, _path(reference))
    artifact = archive.raw_store.path_for(sha256)

    assert archive.raw_store.integrity(sha256) is RawArtifactState.HEALTHY
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive.settings.database_path.stat().st_mode) == 0o600


def test_a_real_source_offered_again_after_losing_its_copy_is_repaired(
    archive: TrackServices, reference: Reference
) -> None:
    """Losing an artifact must not turn into a silent "already imported"."""
    path = _path(reference)
    sha256, track_ids = _import(archive, path)
    archive.raw_store.path_for(sha256).unlink()

    outcome = archive.import_tracks(ImportRequest(content=path.read_bytes()))

    assert outcome.status is ImportStatus.REPAIRED
    assert outcome.track_ids == track_ids
    assert archive.raw_store.integrity(sha256) is RawArtifactState.HEALTHY


def test_reading_a_real_reference_never_writes_near_it(
    archive: TrackServices, reference: Reference
) -> None:
    """The developer's own directory is input. Nothing is created beside the files.

    Content, modification time and the directory listing are all checked: a tool
    that wrote a sidecar file, or rewrote a file identically, would still have
    touched data its owner did not offer.
    """
    path = _path(reference)
    before_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    before_mtime = path.stat().st_mtime_ns
    before_listing = sorted(entry.name for entry in LOCAL_TRACKS.iterdir())

    sha256, _ = _import(archive, path)
    archive.reprocess(sha256)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert sorted(entry.name for entry in LOCAL_TRACKS.iterdir()) == before_listing
