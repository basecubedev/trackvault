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
from gpx_view.application.archive import CreateArchive, RestoreArchive
from gpx_view.application.export import ExportRawSource, ExportTrackDocument
from gpx_view.application.import_tracks import ImportTracks
from gpx_view.application.maps import (
    GetMapCatalog,
    GetMapInstallJob,
    InstallMapPackage,
    ListInstalledMaps,
    LocateTracks,
    RecoverMapStorage,
    RemoveMapPackage,
    SelectMapCoverage,
    SuggestMapRegions,
)
from gpx_view.application.normalization import NormalizeRawImport
from gpx_view.application.processing import InstalledProcessing
from gpx_view.application.processing_status import GetProcessingStatus
from gpx_view.application.reprocess import ReprocessRawImport
from gpx_view.config import Settings
from gpx_view.infrastructure.clock import SystemClock
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.database.archive_source import SqliteArchiveSource
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore
from gpx_view.infrastructure.database.migrations import SCHEMA_VERSION
from gpx_view.infrastructure.filesystem import FilesystemRawImportStore
from gpx_view.infrastructure.gpx import GpxDocumentWriter, GpxImporter
from gpx_view.infrastructure.maps import (
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    GeofabrikMapProvider,
    MapInstallJobs,
    MbtilesPackageInspector,
    MbtilesTileReader,
)
from gpx_view.release import VERSION


def import_limits_from(settings: Settings) -> ImportLimits:
    """Return the safety limits configured for this deployment."""
    return ImportLimits(
        max_bytes=settings.import_max_bytes,
        max_tracks=settings.import_max_tracks,
        max_segments_per_track=settings.import_max_segments_per_track,
        max_points=settings.import_max_points,
    )


@dataclass(frozen=True, slots=True)
class MapServices:
    """The offline map capability, wired.

    Held beside the track services rather than inside them. The two share a
    database file and nothing else, and keeping the object graphs apart is what
    stops "the map manager needs a track repository" from becoming true by
    accident.

    Attributes:
        enabled: Whether this deployment may install packages at all.
        catalog: What the provider offers, cached.
        installed: What is installed, and whether it can still be proved.
        coverage: Which installed maps belong behind a rectangle.
        suggestions: Which regions could be installed for one nothing covers.
        locate: Roughly where a track was, from the cached catalog.
        jobs: Queues and runs installations off the request thread.
        job_query: Reading a job's state.
        remove: Deleting an installed package.
        recover: What has to be true again after a crash.
        tiles: Serving single tiles out of installed packages.
        repository: The package and job rows.
        storage: The managed files.
        provider_name: Who the packages come from, for the credits page.
    """

    enabled: bool
    catalog: GetMapCatalog
    installed: ListInstalledMaps
    coverage: SelectMapCoverage
    suggestions: SuggestMapRegions
    locate: LocateTracks
    jobs: MapInstallJobs
    job_query: GetMapInstallJob
    remove: RemoveMapPackage
    recover: RecoverMapStorage
    tiles: MbtilesTileReader
    repository: SqliteMapPackageStore
    storage: FilesystemMapPackageStorage
    provider_name: str


@dataclass(frozen=True, slots=True)
class TrackServices:
    """The wired object graph one process works with.

    Attributes:
        settings: The configuration everything was built from.
        clock: The one source of "now" this process uses. Shared rather than
            read again, so a backup's filename and its manifest cannot disagree
            about when it was taken.
        store: The SQLite repository.
        raw_store: The managed raw import storage.
        import_tracks: The single canonical import use case.
        reprocess: Regenerating the normalized data of a source already held.
        processing_status: What happened to one source, and whether the
            installed processing outdates it.
        analyze: The single canonical analysis use case, and the selection of
            what needs analysing again.
        create_archive: Writing the whole deployment into one portable file.
        restore_archive: Putting one back, having proved first that it can be.
        export_raw: Handing back the bytes of one import, unchanged.
        export_document: Rendering one current track as an exchange document.
            A different thing from ``export_raw`` on purpose -- one is the
            evidence, the other is what this build currently makes of it.
        processing: What this build turns a source into, per installed adapter.
            The same object that stamps a run and judges whether it is current,
            exposed so a deployment can state which algorithms produced the
            numbers a reader is looking at.
        maps: The offline map capability.
    """

    settings: Settings
    clock: SystemClock
    store: SqliteTrackStore
    raw_store: FilesystemRawImportStore
    import_tracks: ImportTracks
    reprocess: ReprocessRawImport
    processing_status: GetProcessingStatus
    analyze: AnalyzeTrack
    processing: InstalledProcessing
    export_raw: ExportRawSource
    export_document: ExportTrackDocument
    create_archive: CreateArchive
    restore_archive: RestoreArchive
    maps: MapServices

    def prepare_storage(self) -> None:
        """Bring the database up to the current schema before serving anything."""
        self.store.migrate()
        self.maps.storage.prepare()
        # Debris from a process that stopped mid-installation is cleared before
        # anything is served, so a reader never meets a job that says
        # `downloading` and never will.
        self.maps.recover.run()


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
        maps=build_map_services(settings, store, clock),
        settings=settings,
        clock=clock,
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
        processing=normalize.processing,
        export_raw=ExportRawSource(repository=store, raw_store=raw_store),
        # One writer, named once. The exported document states which release
        # produced it, which is what makes "why does this file differ from the
        # one I exported last year" answerable.
        export_document=ExportTrackDocument(
            repository=store, writer=GpxDocumentWriter(generator=f"GPX-View {VERSION}")
        ),
        create_archive=CreateArchive(
            source=SqliteArchiveSource(store), clock=clock, version=VERSION
        ),
        # The schema version this *build* installs, not the one the database
        # happens to hold. A restore is judged against what this code can read,
        # which is what makes an archive from a newer build a refusal rather
        # than a silent downgrade.
        restore_archive=RestoreArchive(installed_schema_version=SCHEMA_VERSION),
    )


def build_map_services(
    settings: Settings, store: SqliteTrackStore, clock: SystemClock
) -> MapServices:
    """Build the offline map object graph for one process.

    The provider is named exactly once, here. Everything above receives ports,
    so adding a second catalog is another adapter and another line in this
    function -- not a change to a use case, a route or the page.
    """
    storage = FilesystemMapPackageStorage(settings.map_storage_dir)
    repository = SqliteMapPackageStore(store)
    provider = GeofabrikMapProvider(version=VERSION)
    cache = FilesystemMapCatalogCache(storage.catalog_directory())
    catalog = GetMapCatalog(provider=provider, cache=cache, clock=clock)
    installer = InstallMapPackage(
        provider=provider,
        catalog=catalog,
        storage=storage,
        inspector=MbtilesPackageInspector(storage),
        repository=repository,
        clock=clock,
        max_download_bytes=settings.map_max_download_bytes,
    )
    installed = ListInstalledMaps(repository, storage)
    return MapServices(
        enabled=settings.maps_enabled,
        catalog=catalog,
        installed=installed,
        # One coverage authority, shared by the track page and by nothing else.
        coverage=SelectMapCoverage(installed),
        # The cache, deliberately not the provider. Suggesting a region for a
        # track is a read that must contact nobody, and a use case that cannot
        # reach a provider cannot be the one that does.
        suggestions=SuggestMapRegions(cache, installed, provider=provider.slug),
        locate=LocateTracks(cache, provider=provider.slug),
        jobs=MapInstallJobs(
            installer=installer, catalog=catalog, repository=repository, clock=clock
        ),
        job_query=GetMapInstallJob(repository),
        remove=RemoveMapPackage(storage=storage, repository=repository),
        recover=RecoverMapStorage(storage=storage, repository=repository, clock=clock),
        tiles=MbtilesTileReader(storage),
        repository=repository,
        storage=storage,
        provider_name=provider.display_name,
    )
