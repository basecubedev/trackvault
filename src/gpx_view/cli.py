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
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gpx_view.application.import_tracks import ImportOutcome, ImportRequest, ImportStatus
from gpx_view.application.reprocess import ReprocessOutcome, ReprocessStatus
from gpx_view.config import Settings, get_settings
from gpx_view.domain import InputChannel, ProcessingProfile
from gpx_view.infrastructure.assembly import TrackServices, build_services
from gpx_view.infrastructure.filesystem import read_bounded, scan_import_directory

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DISABLED = 2


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

    status_command = commands.add_parser(
        "processing-status",
        help="report what happened to one source and whether it is still current",
    )
    status_command.add_argument("sha256", help="content hash of the raw import")
    return parser


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


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    """Run one command and return the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    arguments = _parser().parse_args(argv)

    services = build_services(settings or get_settings())
    services.prepare_storage()

    if arguments.command == "import":
        return _import_paths(services, arguments.paths)
    if arguments.command == "reprocess":
        return _reprocess(
            services,
            arguments.sha256,
            failed_only=arguments.failed,
            outdated_only=arguments.outdated,
        )
    if arguments.command == "processing-status":
        return _processing_status(services, arguments.sha256)
    return _scan(services)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
