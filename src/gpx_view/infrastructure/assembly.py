"""Wiring concrete adapters to the application use cases.

Both entry points -- the HTTP application and the command line -- need the same
object graph. Building it once here keeps the composition root a bootstrap and
avoids a second, subtly different wiring per entry point.

This is plain construction: a function that returns the services, not a factory
hierarchy.
"""

from dataclasses import dataclass

from gpx_view.application import ImportLimits
from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.analyze import AnalyzeTrack
from gpx_view.application.import_tracks import ImportTracks
from gpx_view.application.normalization import NormalizeRawImport
from gpx_view.application.processing_status import GetProcessingStatus
from gpx_view.application.reprocess import ReprocessRawImport
from gpx_view.config import Settings
from gpx_view.infrastructure.clock import SystemClock
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.filesystem import FilesystemRawImportStore
from gpx_view.infrastructure.gpx import GpxImporter


def import_limits_from(settings: Settings) -> ImportLimits:
    """Return the safety limits configured for this deployment."""
    return ImportLimits(
        max_bytes=settings.import_max_bytes,
        max_tracks=settings.import_max_tracks,
        max_segments_per_track=settings.import_max_segments_per_track,
        max_points=settings.import_max_points,
    )


@dataclass(frozen=True, slots=True)
class TrackServices:
    """The wired object graph one process works with.

    Attributes:
        settings: The configuration everything was built from.
        store: The SQLite repository.
        raw_store: The managed raw import storage.
        import_tracks: The single canonical import use case.
        reprocess: Regenerating the normalized data of a source already held.
        processing_status: What happened to one source, and whether the
            installed processing outdates it.
        analyze: The single canonical analysis use case, and the selection of
            what needs analysing again.
    """

    settings: Settings
    store: SqliteTrackStore
    raw_store: FilesystemRawImportStore
    import_tracks: ImportTracks
    reprocess: ReprocessRawImport
    processing_status: GetProcessingStatus
    analyze: AnalyzeTrack

    def prepare_storage(self) -> None:
        """Bring the database up to the current schema before serving anything."""
        self.store.migrate()


def build_services(settings: Settings) -> TrackServices:
    """Build the object graph for one process from the configuration.

    Importing and reprocessing share one ``NormalizeRawImport``. Building two
    would be the second pipeline this project spends most of its architecture
    avoiding, and the composition root is exactly where that mistake is easiest
    to make and hardest to notice.
    """
    store = SqliteTrackStore(settings.database_path)
    raw_store = FilesystemRawImportStore(settings.raw_storage_dir)
    clock = SystemClock()
    normalize = NormalizeRawImport(
        importers=(GpxImporter(),),
        repository=store,
        clock=clock,
        limits=import_limits_from(settings),
    )
    # One analysis use case, shared by the command line, the import path and the
    # reprocessing path. Two would be two answers to "how long is this track",
    # and the second one is the one nobody reruns when an algorithm changes.
    analyze = AnalyzeTrack(repository=store, clock=clock, analysis=InstalledAnalysis())
    return TrackServices(
        settings=settings,
        store=store,
        raw_store=raw_store,
        import_tracks=ImportTracks(
            raw_store=raw_store,
            repository=store,
            clock=clock,
            normalize=normalize,
            analyze=analyze,
        ),
        reprocess=ReprocessRawImport(
            raw_store=raw_store,
            repository=store,
            normalize=normalize,
            analyze=analyze,
        ),
        processing_status=GetProcessingStatus(
            repository=store,
            # The same object that stamps a run judges whether it is current, so
            # what wrote the version and what reads it cannot be two opinions.
            processing=normalize.processing,
            analysis=analyze.installed,
        ),
        analyze=analyze,
    )
