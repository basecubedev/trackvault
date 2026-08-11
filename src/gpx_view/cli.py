"""Command-line entry point for server-side imports and reprocessing.

This is an *input path*, not a pipeline: it reads bytes and hands them to the one
canonical ``ImportTracks`` use case. There is no parsing and no persistence here.

`reprocess` is the operator action that regenerates normalized data from a source
the archive already holds -- after an importer or classifier upgrade, or once a
file that could not be read becomes readable. It is explicit on purpose: nothing
reprocesses itself on start-up, and a directory scan still recognises known bytes
as a duplicate rather than parsing them again.

It exists because the HTTP API is deliberately read-only for now (see
``docs/technical/architecture.md``): importing is an operator action performed on
the machine that holds the data, not an unauthenticated upload endpoint.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from tempfile import TemporaryDirectory

from gpx_view.application.analyze import AnalyzeOutcome, AnalyzeStatus
from gpx_view.application.archive import (
    ArchiveCompatibility,
    ArchiveError,
    ArchiveInspection,
    ArchiveManifest,
    describe_omissions,
)
from gpx_view.application.diagnostics import CheckStatus, Diagnose
from gpx_view.application.import_tracks import ImportOutcome, ImportRequest, ImportStatus
from gpx_view.application.reprocess import ReprocessOutcome, ReprocessStatus
from gpx_view.config import Settings, get_settings
from gpx_view.domain import InputChannel, ProcessingProfile
from gpx_view.infrastructure.archive import (
    ARCHIVE_SUFFIX,
    FilesystemArchiveBuilder,
    FilesystemArchiveExtractor,
)
from gpx_view.infrastructure.assembly import TrackServices, build_services
from gpx_view.infrastructure.database.migrations import SCHEMA_VERSION
from gpx_view.infrastructure.diagnostics import observe
from gpx_view.infrastructure.filesystem import read_bounded, scan_import_directory
from gpx_view.infrastructure.private_data import create_private_directory
from gpx_view.main import create_app

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DISABLED = 2
EXIT_DEGRADED = 2
"""Something needs attention but nothing is broken.

The same number as ``EXIT_DISABLED`` and a different word, because they are
different statements about different commands: a scan with no import directory
is switched off, and a deployment with no backup is working and unprotected.
"""


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="gpx-view", description="Import tracks into the local GPX-View archive."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    import_command = commands.add_parser("import", help="import one or more files")
    import_command.add_argument("paths", nargs="+", type=Path, help="files to import")

    commands.add_parser("scan", help="import new files from the configured import directory")

    reprocess_command = commands.add_parser(
        "reprocess",
        help="normalize a source the archive already holds, using the current importer",
    )
    selection = reprocess_command.add_mutually_exclusive_group(required=True)
    selection.add_argument("sha256", nargs="?", help="content hash of the raw import")
    selection.add_argument(
        "--failed",
        action="store_true",
        help="reprocess every source whose newest processing attempt failed",
    )
    selection.add_argument(
        "--outdated",
        action="store_true",
        help="reprocess every source the installed processing outdates",
    )

    analyze_command = commands.add_parser(
        "analyze",
        help="derive the metrics of tracks the archive already holds",
    )
    analyze_selection = analyze_command.add_mutually_exclusive_group(required=True)
    analyze_selection.add_argument("track_id", nargs="?", type=int, help="identity of a track")
    analyze_selection.add_argument(
        "--outdated",
        action="store_true",
        help="analyse every track whose metrics the installed analysis outdates",
    )
    analyze_selection.add_argument(
        "--all",
        action="store_true",
        dest="every",
        help="analyse every current track, whatever its metrics currently say",
    )

    status_command = commands.add_parser(
        "processing-status",
        help="report what happened to one source and whether it is still current",
    )
    status_command.add_argument("sha256", help="content hash of the raw import")

    export_command = commands.add_parser(
        "export", help="write a source or a track out of the archive"
    )
    exports = export_command.add_subparsers(dest="export_kind", required=True)

    raw_export = exports.add_parser(
        "raw", help="the original bytes of one import, byte-identical to what arrived"
    )
    raw_export.add_argument("sha256", help="content hash of the raw import")
    _add_output_argument(raw_export)

    document_export = exports.add_parser(
        "track", help="one current track as a generated GPX 1.1 document"
    )
    document_export.add_argument("track_id", type=int, help="identity of a track")
    _add_output_argument(document_export)

    backup_command = commands.add_parser(
        "backup", help="write the whole archive into one portable file"
    )
    backups = backup_command.add_subparsers(dest="backup_action", required=True)
    backup_create = backups.add_parser("create", help="create a new backup")
    backup_create.add_argument(
        "--output",
        type=Path,
        default=None,
        help="file to write; a timestamped name in the backup directory when omitted",
    )
    backup_create.add_argument(
        "--no-verify",
        action="store_true",
        help="skip reading the finished archive back; faster, and proves less",
    )
    backups.add_parser("list", help="list the backups in the backup directory")

    restore_command = commands.add_parser(
        "restore", help="put a backup back, after proving it can be put back"
    )
    restore_command.add_argument("archive", type=Path, help="the archive to restore")
    restore_command.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen and change nothing",
    )
    restore_command.add_argument(
        "--replace",
        action="store_true",
        help="allow replacing data that is already in the data directory",
    )
    restore_command.add_argument(
        "--into",
        type=Path,
        default=None,
        help="restore into this directory instead of the configured data directory",
    )

    commands.add_parser(
        "doctor", help="report what is wrong with this deployment, changing nothing"
    )

    openapi_command = commands.add_parser(
        "openapi", help="write the HTTP schema the frontend types are generated from"
    )
    openapi_command.add_argument(
        "--output",
        type=Path,
        default=None,
        help="file to write; standard output when omitted",
    )
    return parser


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    """Give an export command its destination, defaulting to standard output.

    Standard output rather than a file the command invents: an export composes
    into a pipe, and a command that silently creates files in the working
    directory is a command that eventually creates one somewhere surprising.
    """
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="file to write; standard output when omitted",
    )


@dataclass(frozen=True, slots=True)
class _Attempt:
    """What happened to one path on the command line.

    A path that could not be read has no outcome and no content hash. Inventing
    one so that every row has the same shape would be a lie in the summary the
    operator reads.

    Attributes:
        label: The file name, for display.
        outcome: What the import use case reported, or ``None`` if the bytes
            never reached it.
        detail: Why the file could not be read, when that is what happened.
    """

    label: str
    outcome: ImportOutcome | None = None
    detail: str = ""


def _report(attempts: Sequence[_Attempt]) -> int:
    """Print a compact summary and return the process exit code.

    The summary names files and outcomes. It never prints coordinates, and it
    identifies stored content by a hash prefix rather than by the whole digest.
    """
    failed = 0
    for attempt in attempts:
        outcome = attempt.outcome
        if outcome is None:
            sys.stdout.write(f"{'unreadable':<9} {attempt.detail}  {attempt.label}\n")
            failed += 1
            continue
        detail = f" {outcome.error_code.value}" if outcome.error_code else ""
        tracks = f" tracks={len(outcome.track_ids)}" if outcome.track_ids else ""
        sys.stdout.write(
            f"{outcome.status.value:<9} {outcome.sha256[:12]}{tracks}{detail}  {attempt.label}\n"
        )
        failed += outcome.status is ImportStatus.FAILED
    return EXIT_FAILED if failed else EXIT_OK


def _import_paths(services: TrackServices, paths: Sequence[Path]) -> int:
    """Import the given files, continuing past the ones that fail.

    Every path is attempted. An operator who imports a directory's worth of files
    wants the readable ones to arrive, and stopping at the first failure would
    drop the rest without the summary even mentioning them.

    Reading is bounded by the same limit the import use case enforces, through
    the same reader the import directory scan uses.
    """
    max_bytes = services.import_tracks.limits.max_bytes
    attempts = []
    for path in paths:
        try:
            content = read_bounded(path, max_bytes)
        except OSError as error:
            attempts.append(_Attempt(path.name, detail=error.strerror or "cannot be read"))
            continue
        attempts.append(
            _Attempt(
                path.name,
                services.import_tracks(
                    ImportRequest(
                        content=content,
                        original_filename=path.name,
                        input_channel=InputChannel.LOCAL_FILE,
                    )
                ),
            )
        )
    return _report(attempts)


def _report_reprocessed(outcomes: Sequence[ReprocessOutcome]) -> int:
    """Print what each reprocessing attempt did, and return the exit code."""
    if not outcomes:
        sys.stdout.write("nothing to reprocess\n")
        return EXIT_OK
    failed = 0
    for outcome in outcomes:
        detail = f" {outcome.error_code.value}" if outcome.error_code else ""
        tracks = f" tracks={len(outcome.track_ids)}" if outcome.track_ids else ""
        sys.stdout.write(f"{outcome.status.value:<15} {outcome.sha256[:12]}{tracks}{detail}\n")
        failed += outcome.status is not ReprocessStatus.REPROCESSED
    return EXIT_FAILED if failed else EXIT_OK


def _reprocess(
    services: TrackServices, sha256: str | None, *, failed_only: bool, outdated_only: bool
) -> int:
    """Regenerate normalized data for one source, or for a named batch.

    Reprocessing is deliberately an explicit operator action. Doing it on start-up
    would turn every deployment into a full re-parse of the archive, and doing it
    during a scan would undo the point of recognising a duplicate.

    Which sources a batch covers is decided by the use case, not here. A command
    line that assembled its own selection would be a second opinion on what
    "outdated" means, and the status command would eventually disagree with it.

    Every source is attempted on its own. One failure does not stop the rest, and
    the exit code still reports that the run was not clean.
    """
    if failed_only:
        hashes: tuple[str | None, ...] = services.reprocess.failed_sources()
    elif outdated_only:
        hashes = services.reprocess.outdated_sources()
    else:
        hashes = (sha256,)
    return _report_reprocessed(
        [services.reprocess(digest) for digest in hashes if digest is not None]
    )


def _report_analyzed(outcomes: Sequence[AnalyzeOutcome]) -> int:
    """Print what each analysis attempt did, and return the exit code."""
    if not outcomes:
        sys.stdout.write("nothing to analyze\n")
        return EXIT_OK
    failed = 0
    for outcome in outcomes:
        detail = f" {outcome.error_code.value}" if outcome.error_code else ""
        sys.stdout.write(f"{outcome.status.value:<13} track={outcome.track_id}{detail}\n")
        failed += outcome.status is not AnalyzeStatus.ANALYZED
    return EXIT_FAILED if failed else EXIT_OK


def _analyze(
    services: TrackServices, track_id: int | None, *, outdated_only: bool, everything: bool
) -> int:
    """Derive metrics for one track, or for a named batch.

    Which tracks a batch covers is decided by the use case, not here. A command
    line that assembled its own selection would be a second opinion on what
    "outdated" means, and the status view would eventually disagree with it.

    Every track is attempted on its own. One failure does not stop the rest, and
    the exit code still reports that the run was not clean.
    """
    if outdated_only:
        identities: tuple[int | None, ...] = services.analyze.outdated_tracks()
    elif everything:
        identities = services.analyze.analyzable_tracks()
    else:
        identities = (track_id,)
    return _report_analyzed(
        [services.analyze(identity) for identity in identities if identity is not None]
    )


def _processing_status(services: TrackServices, sha256: str) -> int:
    """Print what happened to one source, and whether it is still current.

    Plain lines, no colour and no table drawing: this is read in a terminal and
    piped into a grep. It names hashes, run identities, versions and error codes,
    and never a path, a filename or a coordinate.
    """
    report = services.processing_status(sha256)
    if report is None:
        sys.stdout.write(f"unknown source {sha256[:12]}\n")
        return EXIT_FAILED

    attempt = f"{_or_none(report.latest_attempt_id)}{_suffix(report.latest_attempt_status)}"
    lines = [
        f"raw import:     {report.raw_import_sha256}",
        f"current run:    {_or_none(report.current_run_id)}",
        f"latest attempt: {attempt}{_suffix(report.latest_error_code)}",
        f"tracks:         {report.track_count}",
        *_components(report.current_profile, report.installed_profile),
        f"outdated:       {'yes' if report.is_outdated else 'no'}",
        # The second lifecycle, in the same view. A source can be perfectly
        # normalized and still carry no usable metrics, and an operator should
        # not have to know the two are separate to notice that one is behind.
        f"analysis:       {report.analysed_track_count}/{report.track_count} tracks, "
        f"profile {report.analysis_profile.metric_schema_version}",
        f"analysis outdated: {'yes' if report.outdated_analysis_count else 'no'}"
        f"{_suffix(report.latest_analysis_error_code)}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def _components(
    current: ProcessingProfile | None, installed: ProcessingProfile | None
) -> list[str]:
    """Render each processing component as "what produced this -> what is installed".

    Naming the components separately is what makes an outdated verdict
    actionable: an operator can see whether the parser, the normalized model or
    the classification rules moved on.
    """
    return [
        f"{label + ':':<15} {_or_none(stored)} -> installed {_or_none(available)}"
        for label, stored, available in (
            (
                "importer",
                None if current is None else current.importer_version,
                None if installed is None else installed.importer_version,
            ),
            (
                "normalization",
                None if current is None else str(current.normalization_schema_version),
                None if installed is None else str(installed.normalization_schema_version),
            ),
            (
                "classifier",
                None if current is None else current.classifier_version,
                None if installed is None else installed.classifier_version,
            ),
        )
    ]


def _or_none(value: object) -> str:
    """Render an optional value without pretending an absence is a zero."""
    return "none" if value is None else str(value)


def _suffix(value: object) -> str:
    """Render an optional detail as a trailing word, or as nothing."""
    return "" if value is None else f" {value}"


def _scan(services: TrackServices) -> int:
    """Import every new file from the configured import directory."""
    directory = services.settings.import_dir
    if directory is None:
        sys.stdout.write(
            "no import directory configured; set GPX_VIEW_IMPORT_DIR to enable scanning\n"
        )
        return EXIT_DISABLED
    return _report(
        [
            _Attempt(label, outcome)
            for label, outcome in scan_import_directory(directory, services.import_tracks)
        ]
    )


def _export_raw(services: TrackServices, sha256: str, destination: Path | None) -> int:
    """Write the original bytes of one import, exactly as they arrived.

    The store verifies the bytes against the hash they are filed under before
    they leave it, so this either produces the original or fails with a named
    reason. It never produces "probably the original".
    """
    export = services.export_raw(sha256)
    if export is None:
        sys.stderr.write(f"unknown source {sha256[:12]}\n")
        return EXIT_FAILED
    return _write_bytes(export.content, destination)


def _export_document(services: TrackServices, track_id: int, destination: Path | None) -> int:
    """Write one current track as a generated exchange document.

    Not the file that was imported. This is the archive's current normalized
    generation rendered into GPX, which is why `export raw` exists beside it.
    """
    export = services.export_document(track_id)
    if export is None:
        sys.stderr.write(f"unknown track {track_id}\n")
        return EXIT_FAILED
    return _write_bytes(export.content, destination)


def _write_bytes(content: bytes, destination: Path | None) -> int:
    """Write bytes to a file, or to standard output when none was named.

    Written through the buffer rather than through the text stream: this is a
    document with its own declared encoding, and letting a terminal's locale
    re-encode it would corrupt exactly the files somebody exported to keep.
    """
    if destination is None:
        sys.stdout.buffer.write(content)
    else:
        destination.write_bytes(content)
    return EXIT_OK


def _backup_create(services: TrackServices, destination: Path | None, *, verify: bool) -> int:
    """Write one archive, and read it back to prove it is one.

    Verification re-runs the *restore* validation against the finished file:
    manifest, every checksum, and the database's own integrity check. Proving a
    backup with the code that would restore it is the only proof worth having --
    a bespoke check would be a second opinion, and the day the two disagreed the
    backup would already be the thing at stake.
    """
    settings = services.settings
    directory = destination.parent if destination else settings.backup_storage_dir
    create_private_directory(directory)
    target = destination or directory / _backup_name(services)
    builder = FilesystemArchiveBuilder(
        destination=target,
        database_path=settings.database_path,
        raw_root=settings.raw_storage_dir,
    )
    try:
        manifest = services.create_archive(builder)
        if verify:
            _verify_archive(target, settings)
    except ArchiveError as error:
        sys.stderr.write(f"backup failed: {error.code.value} {error.detail}\n")
        return EXIT_FAILED
    _report_manifest(manifest, target)
    return EXIT_OK


def _backup_name(services: TrackServices) -> str:
    """Return a timestamped archive name, ordered the way a listing reads.

    The instant comes from the same clock the manifest is stamped from, so the
    name and the content cannot disagree about when the backup was taken.
    """
    stamp = services.clock.now().astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    return f"gpx-view-{stamp}{ARCHIVE_SUFFIX}"


def _verify_archive(path: Path, settings: Settings) -> None:
    """Read a finished archive back through the restore validation.

    Staged into a temporary directory beside the archive rather than into the
    data directory: verifying a backup must not put anything near the live
    deployment, and the archive's own filesystem is the one known to have had
    room for it.
    """
    with TemporaryDirectory(dir=path.parent, prefix=".verify-") as scratch:
        root = Path(scratch)
        extractor = FilesystemArchiveExtractor(
            source=path,
            data_dir=root,
            database_path=root / settings.database_path.name,
            raw_root=root / settings.raw_storage_dir.name,
        )
        try:
            extractor.verify(extractor.manifest())
        finally:
            extractor.abandon()


def _report_manifest(manifest: ArchiveManifest, target: Path) -> None:
    """Print what was written, in the terms an operator checks it against."""
    sys.stdout.write(
        "\n".join(
            [
                f"created:   {target}",
                f"size:      {target.stat().st_size} bytes",
                f"taken at:  {manifest.created_at.isoformat()}",
                f"release:   {manifest.gpx_view_version}",
                f"schema:    {manifest.schema_version}",
                f"sources:   {manifest.counts.raw_imports}",
                f"tracks:    {manifest.counts.tracks}",
                f"overrides: {manifest.counts.classification_overrides}",
                f"notes:     {manifest.counts.user_metadata}",
                f"omits:     {describe_omissions(manifest.omissions)}",
            ]
        )
        + "\n"
    )


def _backup_list(services: TrackServices) -> int:
    """List the archives in the backup directory, newest first."""
    directory = services.settings.backup_storage_dir
    archives = sorted(directory.glob(f"*{ARCHIVE_SUFFIX}")) if directory.is_dir() else []
    if not archives:
        sys.stdout.write(f"no backups in {directory}\n")
        return EXIT_OK
    for path in reversed(archives):
        sys.stdout.write(f"{path.stat().st_size:>14} {path.name}\n")
    return EXIT_OK


def _restore(
    services: TrackServices, archive: Path, *, dry_run: bool, replace: bool, into: Path | None
) -> int:
    """Inspect or restore one archive, in that order and never the other way."""
    settings = services.settings
    data_dir = into or settings.data_dir
    extractor = FilesystemArchiveExtractor(
        source=archive,
        data_dir=data_dir,
        database_path=data_dir / settings.database_path.name,
        raw_root=data_dir / settings.raw_storage_dir.name,
    )
    try:
        if dry_run:
            return _report_inspection(services.restore_archive.inspect(extractor), data_dir)
        outcome = services.restore_archive(extractor, replace=replace)
    except ArchiveError as error:
        sys.stderr.write(f"restore failed: {error.code.value} {error.detail}\n")
        return EXIT_FAILED
    sys.stdout.write(
        f"restored {outcome.manifest.counts.tracks} track(s) from "
        f"{outcome.manifest.counts.raw_imports} source(s) into {data_dir}\n"
        f"compatibility: {outcome.compatibility.value}\n"
        f"replaced existing data: {'yes' if outcome.replaced_existing_data else 'no'}\n"
    )
    if outcome.compatibility is ArchiveCompatibility.MIGRATION_REQUIRED:
        sys.stdout.write("the database will be migrated the next time the archive starts\n")
    return EXIT_OK


def _report_inspection(inspection: ArchiveInspection, data_dir: Path) -> int:
    """Print what a restore would do, having changed nothing.

    Returns a non-zero code when the archive could not be restored, so a dry-run
    is usable as a check in a script rather than only as something to read.
    """
    manifest = inspection.manifest
    sys.stdout.write(
        "\n".join(
            [
                f"archive:       {manifest.format_name} v{manifest.format_version}",
                f"taken at:      {manifest.created_at.isoformat()}",
                f"written by:    GPX-View {manifest.gpx_view_version}",
                f"schema:        {manifest.schema_version}",
                f"compatibility: {inspection.compatibility.value}",
                f"sources:       {manifest.counts.raw_imports}",
                f"tracks:        {manifest.counts.tracks}",
                f"overrides:     {manifest.counts.classification_overrides}",
                f"notes:         {manifest.counts.user_metadata}",
                f"omits:         {describe_omissions(manifest.omissions)}",
                f"target:        {data_dir}",
                f"target holds data: {'yes' if inspection.target_holds_data else 'no'}",
                f"needs:         {inspection.required_bytes} bytes",
            ]
        )
        + "\n"
    )
    if inspection.target_holds_data:
        sys.stdout.write("restoring would replace it; pass --replace to allow that\n")
    return EXIT_OK if inspection.restorable else EXIT_FAILED


def _doctor(services: TrackServices) -> int:
    """Report the state of this deployment, and return a code a script can read.

    Deliberately not run through ``prepare_storage``: a diagnostic that migrated
    the database before looking at it would report a schema it had just written,
    and would be a change to the very thing somebody ran it to understand.
    """
    report = Diagnose()(observe(services, SCHEMA_VERSION))
    for check in report.checks:
        sys.stdout.write(f"{check.status.value:<8} {check.name:<20} {check.detail}\n")
    sys.stdout.write(f"\n{report.status.value}\n")
    return _DOCTOR_EXIT_CODES[report.status]


_DOCTOR_EXIT_CODES = {
    CheckStatus.OK: EXIT_OK,
    CheckStatus.WARNING: EXIT_DEGRADED,
    CheckStatus.ERROR: EXIT_FAILED,
}
"""What ``doctor`` returns, so a cron job can tell the three apart.

Degraded is deliberately its own code rather than a failure. A pending migration
and an unreadable database both need somebody, and only one of them needs them
tonight.
"""


def _openapi(destination: Path | None) -> int:
    """Write the HTTP schema, byte-for-byte reproducibly.

    The backend is the API authority and the browser's types are generated from
    it, so this has to be deterministic: sorted keys, one fixed indentation, a
    trailing newline. A schema that reorders itself between two runs would make
    every regeneration a diff and every drift check useless.

    Nothing is composed against a data directory: building the schema reads the
    routes and the response models, and writing a file to disk to describe them
    would be a side effect a documentation command has no business having.
    """
    schema = json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True) + "\n"
    if destination is None:
        sys.stdout.write(schema)
    else:
        destination.write_text(schema, encoding="utf-8")
    return EXIT_OK


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    """Run one command and return the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    arguments = _parser().parse_args(argv)

    # The schema describes the code rather than an archive, so it is answered
    # before anything opens a database.
    if arguments.command == "openapi":
        return _openapi(arguments.output)

    services = build_services(settings or get_settings())

    # Restore runs before anything creates a database. Migrating first would put
    # an empty archive in the destination and make every restore report that it
    # was about to replace something -- the protection would fire on the case it
    # exists to allow.
    # Diagnosing runs before anything migrates, for the same reason restoring
    # does: it would otherwise report a state it had just created.
    if arguments.command == "doctor":
        return _doctor(services)

    if arguments.command == "restore":
        return _restore(
            services,
            arguments.archive,
            dry_run=arguments.dry_run,
            replace=arguments.replace,
            into=arguments.into,
        )

    services.prepare_storage()

    if arguments.command == "backup":
        if arguments.backup_action == "create":
            return _backup_create(services, arguments.output, verify=not arguments.no_verify)
        return _backup_list(services)
    if arguments.command == "import":
        return _import_paths(services, arguments.paths)
    if arguments.command == "reprocess":
        return _reprocess(
            services,
            arguments.sha256,
            failed_only=arguments.failed,
            outdated_only=arguments.outdated,
        )
    if arguments.command == "analyze":
        return _analyze(
            services,
            arguments.track_id,
            outdated_only=arguments.outdated,
            everything=arguments.every,
        )
    if arguments.command == "processing-status":
        return _processing_status(services, arguments.sha256)
    if arguments.command == "export":
        if arguments.export_kind == "raw":
            return _export_raw(services, arguments.sha256, arguments.output)
        return _export_document(services, arguments.track_id, arguments.output)
    return _scan(services)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
