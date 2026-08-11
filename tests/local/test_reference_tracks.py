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
import math
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application import ImportLimits
from trackvault.application.analysis import InstalledAnalysis
from trackvault.application.analyze import AnalyzeStatus
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.application.ports import RawArtifactState, TrackQuery, TrackSummary
from trackvault.application.reprocess import ReprocessStatus
from trackvault.application.statistics import AggregationScope, GetYearStatistics
from trackvault.config import Settings
from trackvault.domain import (
    Activity,
    ClassificationResult,
    EvidenceCode,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    SourceMetadata,
    TemporalEvidence,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
    supports_actual_calendar_placement,
    supports_actual_timing,
)
from trackvault.domain.analysis import MetricName
from trackvault.infrastructure.assembly import (
    TrackServices,
    build_services,
    import_limits_from,
)
from trackvault.infrastructure.gpx import GpxImporter
from trackvault.infrastructure.gpx.extensions import (
    GARMIN_TRACK_POINT_EXTENSION_V2,
    LOCUS_MAP_EXTENSIONS,
)
from trackvault.main import create_app

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
    """The developer's own files are input. TrackVault does not write to them.

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


# --- Analysis against real data ---------------------------------------------


def test_the_recorded_reference_analyses_to_plausible_values(archive: TrackServices) -> None:
    """A real recording produces every metric its data can support.

    Bounds rather than snapshots. Pinning a private file's exact distance would
    turn one person's walk into the project's specification, and the first
    justified algorithm change would then look like a regression. What is
    checked is that the numbers are of the right order and internally
    consistent -- which is what would actually break if an algorithm went wrong.
    """
    _, (track_id,) = _import(archive, _path(RECORDED))

    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    metrics = stored.metrics

    distance = metrics[MetricName.DISTANCE].value
    elapsed = metrics[MetricName.ELAPSED_DURATION].value
    moving = metrics[MetricName.MOVING_DURATION].value
    stopped = metrics[MetricName.STOPPED_DURATION].value
    unobserved = metrics[MetricName.UNOBSERVED_GAP_DURATION].value

    # A day walk, not a marathon and not a stroll round the block.
    assert 1_000.0 < distance < 100_000.0
    assert 600.0 < elapsed < 24 * 3600.0
    assert moving + stopped + unobserved <= elapsed + 1.0
    assert moving > 0.0


def test_the_recorded_reference_keeps_its_speeds_believable(archive: TrackServices) -> None:
    """A real receiver produces bad fixes, and none of them may become a headline.

    This is the outlier strategy meeting the data it exists for. A single jump
    in a real recording would show up here as a maximum speed no walker reaches.
    """
    _, (track_id,) = _import(archive, _path(RECORDED))

    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    maximum = stored.metrics[MetricName.MAXIMUM_SPEED].value
    moving_average = stored.metrics[MetricName.MOVING_AVERAGE_SPEED].value

    assert 0.0 < moving_average < 4.0
    assert moving_average <= maximum
    assert maximum < 15.0


def test_the_recorded_reference_reports_filtered_elevation(archive: TrackServices) -> None:
    """Real phone elevation is noisy, and the filter has to survive it.

    The check that matters is the relationship: ascent derived from a filtered
    profile cannot plausibly dwarf the raw range it was derived from. An
    unfiltered sum over real data would.
    """
    _, (track_id,) = _import(archive, _path(RECORDED))

    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    metrics = stored.metrics

    lowest = metrics[MetricName.ELEVATION_MINIMUM].value
    highest = metrics[MetricName.ELEVATION_MAXIMUM].value
    gain = metrics[MetricName.ELEVATION_GAIN].value

    assert highest > lowest
    assert gain >= highest - lowest
    assert gain < 5.0 * (highest - lowest)


def test_the_planned_reference_is_measurable_without_being_an_activity(
    archive: TrackServices,
) -> None:
    """The real planned route carries timestamps, and they prove nothing.

    This is the contract that "timestamps present does not prove `RECORDED`"
    exists for, and the real file is what makes it concrete: a planner wrote
    times into it, so the analysis derives durations and speeds from them
    exactly as it would for a recording. Those numbers are real arithmetic over
    the data present -- and the archive still does not claim anybody walked it.

    The separation is therefore not made by withholding metrics. It is made by
    the effective kind, which decides which totals a track reaches. Suppressing
    the metrics instead would be the classifier deciding twice, in a place that
    has no evidence to decide with.
    """
    _, (track_id,) = _import(archive, _path(PLANNED))

    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    (summary,) = [
        candidate
        for candidate in archive.store.list_tracks(TrackQuery()).tracks
        if candidate.track_id == track_id
    ]

    assert stored.metrics[MetricName.DISTANCE].value > 1_000.0
    assert MetricName.ELEVATION_GAIN in stored.metrics
    assert summary.effective_kind is not TrackKind.RECORDED
    assert not summary.classification.contributes_to_actual_totals


def test_a_real_import_is_analysed_immediately(archive: TrackServices) -> None:
    """Importing a real file leaves nothing for the batch selection to find."""
    _import(archive, _path(RECORDED))

    assert archive.analyze.outdated_tracks() == ()


def test_the_planned_reference_stays_out_of_the_actual_totals(
    archive: TrackServices,
) -> None:
    """The whole point, on real data: a planned route is not a walk you did."""
    _import(archive, _path(RECORDED))
    _import(archive, _path(PLANNED))

    statistics = GetYearStatistics(
        repository=archive.store, timezone="UTC", analysis=InstalledAnalysis()
    )
    actual = statistics(2025, scope=AggregationScope.RECORDED)
    (recorded_summary,) = [
        summary
        for summary in archive.store.list_tracks(TrackQuery()).tracks
        if summary.effective_kind is TrackKind.RECORDED
    ]
    stored = archive.store.current_analysis(recorded_summary.track_id)

    assert stored is not None
    assert actual.totals.track_count == 1
    assert actual.totals.distance_m == pytest.approx(stored.metrics[MetricName.DISTANCE].value)


def test_reanalysing_a_real_track_reproduces_its_numbers(archive: TrackServices) -> None:
    """Derived state is rebuildable, and a rebuild must agree with what it replaces."""
    _, (track_id,) = _import(archive, _path(RECORDED))
    before = archive.store.current_analysis(track_id)

    assert archive.analyze(track_id).status is AnalyzeStatus.ANALYZED

    after = archive.store.current_analysis(track_id)
    assert before is not None
    assert after is not None
    assert after.metrics == before.metrics


def test_a_real_analysis_survives_reprocessing(archive: TrackServices) -> None:
    """A regeneration brings its metrics along rather than leaving them stale."""
    sha256, (track_id,) = _import(archive, _path(RECORDED))
    before = archive.store.current_analysis(track_id)

    assert archive.reprocess(sha256).status is ReprocessStatus.REPROCESSED

    assert archive.analyze.outdated_tracks() == ()
    after = archive.store.current_analysis(track_id)
    assert before is not None
    assert after is not None
    assert after.metrics == before.metrics


# --- Independent cross-check against the real files --------------------------


def _independent_distance(segments: tuple[TrackSegment, ...]) -> float:
    """Sum a track's length with a formula the production code does not use.

    The spherical law of cosines on the same sphere. Deliberately not haversine:
    calling the production function again would prove only that it is
    deterministic. Two different formulas agreeing to a fraction of a percent is
    evidence that the number means what it says.
    """
    radius = 6_371_008.8
    total = 0.0
    for segment in segments:
        for start, end in zip(segment.points, segment.points[1:], strict=False):
            first = math.radians(start.latitude)
            second = math.radians(end.latitude)
            delta = math.radians(end.longitude - start.longitude)
            cosine = math.sin(first) * math.sin(second) + math.cos(first) * math.cos(
                second
            ) * math.cos(delta)
            total += radius * math.acos(max(-1.0, min(1.0, cosine)))
    return total


def _independent_elapsed(segments: tuple[TrackSegment, ...]) -> float:
    """Return the raw span between the first and last instant the file carries."""
    instants = [point.time for segment in segments for point in segment.points if point.time]
    return (instants[-1] - instants[0]).total_seconds()


@pytest.fixture(params=REFERENCES, ids=lambda reference: reference.filename[:24])
def analysed(request: pytest.FixtureRequest, archive: TrackServices) -> tuple[int, TrackServices]:
    """Import one reference and return its track identity."""
    assert isinstance(request.param, Reference)
    _, (track_id,) = _import(archive, _path(request.param))
    return track_id, archive


def test_the_production_distance_matches_an_independent_calculation(
    analysed: tuple[int, TrackServices],
) -> None:
    """Two formulas, one answer, within the tolerance a sphere allows."""
    track_id, archive = analysed
    segments = archive.store.get_geometry(track_id)
    assert segments is not None
    stored = archive.store.current_analysis(track_id)
    assert stored is not None

    assert stored.metrics[MetricName.DISTANCE].value == pytest.approx(
        _independent_distance(segments), rel=0.001
    )


def test_the_production_elapsed_span_matches_the_raw_timestamps(
    analysed: tuple[int, TrackServices],
) -> None:
    """The elapsed duration is first observation to last, and nothing cleverer."""
    track_id, archive = analysed
    segments = archive.store.get_geometry(track_id)
    assert segments is not None
    stored = archive.store.current_analysis(track_id)
    assert stored is not None

    assert stored.metrics[MetricName.ELAPSED_DURATION].value == pytest.approx(
        _independent_elapsed(segments)
    )


def test_a_real_tracks_time_is_fully_attributed(analysed: tuple[int, TrackServices]) -> None:
    """The four durations add up to the elapsed one, on real data.

    Synthetic fixtures can be built to add up. A real recording with dropouts,
    duplicated instants and a receiver that lost its fix is where an attribution
    rule actually leaks.
    """
    track_id, archive = analysed
    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    metrics = stored.metrics

    attributed = sum(
        metrics[name].value
        for name in (
            MetricName.MOVING_DURATION,
            MetricName.STOPPED_DURATION,
            MetricName.UNOBSERVED_GAP_DURATION,
            MetricName.UNATTRIBUTED_DURATION,
        )
    )
    assert attributed == pytest.approx(metrics[MetricName.ELAPSED_DURATION].value)


def test_the_recorded_reference_gains_roughly_what_it_loses(archive: TrackServices) -> None:
    """The real recording is a round tour, so it ends where it started.

    Not exactly -- a receiver's first and last altitude differ by metres -- but
    an ascent and a descent that disagree by a third would mean the filter is
    direction-dependent.
    """
    _, (track_id,) = _import(archive, _path(RECORDED))
    stored = archive.store.current_analysis(track_id)
    assert stored is not None

    gain = stored.metrics[MetricName.ELEVATION_GAIN].value
    loss = stored.metrics[MetricName.ELEVATION_LOSS].value
    assert gain == pytest.approx(loss, rel=0.1)


def test_the_recorded_reference_earns_observed_timing(archive: TrackServices) -> None:
    """A real receiver wrote quality values, so its instants are observations."""
    _, (track_id,) = _import(archive, _path(RECORDED))
    summary = archive.store.get_track(track_id)
    assert summary is not None

    assert summary.temporal_evidence is TemporalEvidence.OBSERVED
    assert supports_actual_timing(summary.effective_kind, summary.temporal_evidence)


def test_the_planned_reference_never_claims_observed_timing(archive: TrackServices) -> None:
    """The most important assertion in this file.

    The real planned route carries 391 positions with plausible timestamps and
    no measurement metadata whatsoever. The analysis derives durations and
    speeds from those instants, because they are real arithmetic over the data
    present -- and nothing in the archive may call the result observed movement.
    """
    _, (track_id,) = _import(archive, _path(PLANNED))
    summary = archive.store.get_track(track_id)
    assert summary is not None
    stored = archive.store.current_analysis(track_id)
    assert stored is not None

    assert summary.temporal_evidence is not TemporalEvidence.OBSERVED
    assert not supports_actual_timing(summary.effective_kind, summary.temporal_evidence)
    assert stored.metrics[MetricName.MOVING_DURATION].value > 0.0, (
        "the arithmetic is not withheld; only the claim about it is"
    )


def test_the_planned_reference_contributes_to_no_actual_or_planned_total(
    archive: TrackServices,
) -> None:
    """`UNKNOWN` is a third set, not a weaker form of either other one."""
    _, (track_id,) = _import(archive, _path(PLANNED))
    summary = archive.store.get_track(track_id)
    assert summary is not None
    if summary.effective_kind is not TrackKind.UNKNOWN:
        pytest.skip("the classifier reached a verdict; this checks the unknown case")

    statistics = GetYearStatistics(
        repository=archive.store, timezone="UTC", analysis=InstalledAnalysis()
    )
    for scope in (AggregationScope.RECORDED, AggregationScope.PLANNED):
        year = statistics(2025, scope=scope)
        assert year.totals.track_count == 0, scope
        assert year.unplaced.without_date.track_count == 0, scope
        assert year.unplaced.with_unverified_date.track_count == 0, scope


def test_the_planned_reference_is_not_placed_in_a_calendar_month(
    archive: TrackServices,
) -> None:
    """Its first position carries an October instant. That is not a date.

    The file states a plausible time and nothing in it shows anything was
    measuring, so the archive may show the timeline and may not use it to say
    the route happened in October. It belongs to no month, and its distance
    belongs to no monthly total.
    """
    _, (track_id,) = _import(archive, _path(PLANNED))
    summary = archive.store.get_track(track_id)
    assert summary is not None
    assert summary.started_at is not None, "the timeline itself is present"
    assert not supports_actual_calendar_placement(summary.temporal_evidence)

    statistics = GetYearStatistics(
        repository=archive.store, timezone="UTC", analysis=InstalledAnalysis()
    )
    year = statistics(summary.started_at.year, scope=AggregationScope.UNKNOWN)

    if summary.effective_kind is TrackKind.UNKNOWN:
        assert year.totals.track_count == 0
        assert year.unplaced.with_unverified_date.track_count == 1
        assert year.unplaced.with_unverified_date.distance_m is not None


def test_a_kind_override_never_dates_the_planned_reference(archive: TrackServices) -> None:
    """Saying "this was recorded" does not say "this clock was measured"."""
    _, (track_id,) = _import(archive, _path(PLANNED))
    assert archive.store.set_override(
        track_id, TrackKind.RECORDED, datetime(2026, 8, 9, tzinfo=UTC)
    )
    summary = archive.store.get_track(track_id)
    assert summary is not None
    assert summary.effective_kind is TrackKind.RECORDED

    statistics = GetYearStatistics(
        repository=archive.store, timezone="UTC", analysis=InstalledAnalysis()
    )
    assert summary.started_at is not None
    year = statistics(summary.started_at.year, scope=AggregationScope.RECORDED)

    assert year.totals.track_count == 0, "an override placed an unverified date in a year"
    assert year.unplaced.with_unverified_date.track_count == 1


# --- What the product shows for the two real files ---------------------------


def _serve(archive: TrackServices, settings: Settings) -> Iterator[TestClient]:
    """Yield an HTTP client over an archive that already holds the references."""
    for reference in REFERENCES:
        _import(archive, _path(reference))
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def served(tmp_path: Path) -> Iterator[TestClient]:
    """Serve an archive holding both real reference files."""
    settings = Settings(data_dir=tmp_path / "data")
    archive = build_services(settings)
    archive.prepare_storage()
    yield from _serve(archive, settings)


def test_the_two_references_reach_the_product_surfaces_they_belong_on(
    served: TestClient,
) -> None:
    """What a reader would see, over the two files a developer actually has.

    No coordinate, no filename and no title is asserted: the point is which
    *statements* the archive makes about real data, not what that data is.
    """
    listed = served.get("/api/v1/tracks").json()

    assert listed["total"] == len(REFERENCES)
    recorded = [
        track
        for track in listed["tracks"]
        if track["classification"]["effective_kind"] == "recorded"
    ]
    assert len(recorded) == 1
    assert recorded[0]["analysis"]["status"] == "current"
    assert recorded[0]["analysis"]["distance_m"] > 0.0
    assert recorded[0]["timeline"]["is_actual_calendar_time"] is True


def test_the_undecided_reference_is_visible_without_being_dated(served: TestClient) -> None:
    """The unplaced state, on a real file that produces it.

    The planned reference carries plausible instants and no measurement
    metadata. Its timeline is reported, its calendar placement is refused, and
    it contributes nothing to any period -- three separate statements, all of
    which have to hold at once.
    """
    unknown = next(
        track
        for track in served.get("/api/v1/tracks").json()["tracks"]
        if track["classification"]["effective_kind"] != "recorded"
    )

    years = served.get(
        "/api/v1/statistics/years", params={"scope": unknown["classification"]["effective_kind"]}
    ).json()

    assert unknown["timeline"]["started_at"] is not None
    assert unknown["timeline"]["is_actual_calendar_time"] is False
    assert unknown["timeline"]["is_actual_activity_timing"] is False
    assert years["years"] == []
    assert years["unplaced"]["with_unverified_date"] == 1


def test_the_recorded_reference_gives_the_dashboard_a_year_to_open_on(
    served: TestClient,
) -> None:
    """The archive names its own calendar, from a real recording."""
    years = served.get("/api/v1/statistics/years", params={"scope": "recorded"}).json()

    assert years["years"], "the recorded reference should place itself in a year"
    newest = years["years"][0]
    totals = served.get(f"/api/v1/statistics/year/{newest}", params={"scope": "recorded"}).json()
    assert totals["totals"]["track_count"] == 1
    assert totals["totals"]["analysed_track_count"] == 1


def test_a_real_track_draws_a_bounded_profile_and_a_map(served: TestClient) -> None:
    """The two series a detail page renders, over a real 831-position recording."""
    track_id = next(
        track["id"]
        for track in served.get("/api/v1/tracks").json()["tracks"]
        if track["classification"]["effective_kind"] == "recorded"
    )

    profile = served.get(f"/api/v1/tracks/{track_id}/profile", params={"max_samples": 200}).json()
    geometry = served.get(f"/api/v1/tracks/{track_id}/geometry", params={"max_points": 100}).json()

    assert profile["sample_count"] <= 200
    assert profile["total_sample_count"] == RECORDED.points
    assert geometry["point_count"] <= 100
    assert geometry["simplified"] is True
    distances = [
        sample["distance_m"] for segment in profile["segments"] for sample in segment["samples"]
    ]
    assert distances == sorted(distances)


def test_renaming_a_real_track_never_writes_near_the_source_file(
    served: TestClient, tmp_path: Path
) -> None:
    """The correction is the archive's data. The file is the developer's.

    Both reference files are hashed before and after, with their modification
    times, so a rename that reached back to the source would fail here rather
    than in somebody's directory.
    """
    before = {
        reference.filename: (
            hashlib.sha256(_path(reference).read_bytes()).hexdigest(),
            _path(reference).stat().st_mtime_ns,
        )
        for reference in REFERENCES
    }
    track_id = served.get("/api/v1/tracks").json()["tracks"][0]["id"]

    renamed = served.patch(
        f"/api/v1/tracks/{track_id}/metadata",
        json={"title": "A name of my own", "note": "kept beside the file, never in it"},
    ).json()
    reset = served.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": None}).json()

    assert renamed["title"] == "A name of my own"
    assert reset["title"] == reset["metadata"]["source_title"]
    assert reset["metadata"]["note"] == "kept beside the file, never in it"
    for reference in REFERENCES:
        path = _path(reference)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before[reference.filename][0]
        assert path.stat().st_mtime_ns == before[reference.filename][1]
    assert not list(LOCAL_TRACKS.glob("*.sqlite3"))
    assert not list(tmp_path.parent.glob("**/*.gpx"))
