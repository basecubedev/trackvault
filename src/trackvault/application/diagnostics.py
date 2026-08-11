"""What is wrong with this deployment, answered without changing it.

A self-hosted archive is run by somebody who is not watching logs. When
something is off -- a schema behind the build, an artifact that lost its file, an
import directory nobody mounted -- the symptom is usually "a number looks wrong",
which is the hardest thing to act on. This turns it into a list.

**It is read-only, and that is a contract rather than an intention.** Diagnosing
performs no import, no migration, no repair and no network request. An operator
runs it precisely when they are not sure what state things are in, and a
diagnostic that changed that state would be the worst possible tool for the job.
Every repair this reports is something a person then chooses to run.

The split here is deliberate: infrastructure *observes* -- files exist, hashes
match, a schema says 9 -- and this module *judges*. Whether a schema older than
the build is a warning or an error is a business decision with one owner, and
keeping it out of the code that reads the disk is what makes it testable without
a disk.
"""

from dataclasses import dataclass
from enum import StrEnum

from trackvault.domain import ProcessingProfile
from trackvault.domain.analysis import AnalysisProfile


class CheckStatus(StrEnum):
    """How worried to be about one observation.

    Three levels, and the middle one carries its weight: most of what goes wrong
    with an archive is *degraded* rather than broken -- a pending migration, a
    missing backup, an unmounted import folder -- and collapsing those into
    "error" trains an operator to ignore the output.

    Attributes:
        OK: Nothing to do.
        WARNING: Working, but not the way it should be. Somebody should act,
            and nothing is being lost meanwhile.
        ERROR: Something is broken or unreadable. Data may be at stake.
    """

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


_SEVERITY = {CheckStatus.OK: 0, CheckStatus.WARNING: 1, CheckStatus.ERROR: 2}


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """One thing that was looked at, and what was found.

    Attributes:
        name: Short stable identifier, so a check can be grepped for across
            releases.
        status: How worried to be.
        detail: One line an operator reads. Never a coordinate, a track title or
            a personal filename -- a diagnostic that is safe to paste into an
            issue is a diagnostic people actually paste.
    """

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Everything that was looked at, and the worst of it.

    Attributes:
        checks: Every check, in a stable order.
    """

    checks: tuple[DiagnosticCheck, ...]

    @property
    def status(self) -> CheckStatus:
        """Return the worst status any check reported."""
        return max(
            (check.status for check in self.checks),
            key=_SEVERITY.__getitem__,
            default=CheckStatus.OK,
        )

    @property
    def healthy(self) -> bool:
        """Report whether nothing at all needs attention."""
        return self.status is CheckStatus.OK


@dataclass(frozen=True, slots=True)
class DeploymentObservation:
    """What infrastructure saw, before anybody decided what it means.

    Every field is a fact rather than a verdict. ``database_schema_version`` is
    a number; whether that number is a problem is decided in :class:`Diagnose`,
    which is what lets the rules be tested without a file system.

    Attributes:
        release: Which release this build is.
        installed_schema_version: The schema version this build applies.
        data_directory_present: Whether the data directory exists.
        data_directory_writable: Whether this process may write into it.
        data_directory_creatable: Whether it could be created if it is absent --
            that is, whether the parent directory is writable.
        database_present: Whether the database file exists.
        database_schema_version: What the database says its schema is, or
            ``None`` when there is no readable database.
        database_intact: What the database's own integrity check said, or
            ``None`` when it could not be asked.
        raw_artifacts_expected: How many raw imports the database knows about.
        raw_artifacts_missing: How many of those have no managed file.
        raw_artifacts_corrupt: How many have a file whose bytes are not the ones
            their digest names.
        import_directory_configured: Whether scanning is switched on at all.
        import_directory_readable: Whether it can be listed, or ``None`` when
            none is configured.
        backup_directory_present: Whether the backup directory exists.
        backup_count: How many archives are in it.
        latest_backup_age_days: How old the newest one is, or ``None``.
        web_assets_present: Whether the built browser application is there.
        processing_profiles: What this build normalizes with.
        analysis_profile: What this build derives metrics with.
        containerized: Whether this looks like a container, for the advice to
            match the deployment.
        restore_in_progress: Whether a restore started and did not finish. The
            deployment may currently be a database and a raw storage belonging
            to two different archives, which is the one state where every other
            number on this report is describing half of something.
        maps_enabled: Whether map installation is permitted.
        installed_map_count: How many map packages have a row and a managed file
            that hashes to what the row says. Nothing else is an installation.
        invalid_map_count: How much of the map area is not an installation --
            rows whose file is missing or wrong, plus managed files no row
            claims. Disjoint from ``installed_map_count`` by construction.
        unclaimed_map_file_count: How many of those are files nothing claims.
            Held apart because the two faults call for opposite actions: a row
            without its file is reinstalled by a person, a file without its row
            is cleared by the next start.
    """

    release: str
    installed_schema_version: int
    data_directory_present: bool
    data_directory_writable: bool
    data_directory_creatable: bool
    database_present: bool
    database_schema_version: int | None
    database_intact: bool | None
    raw_artifacts_expected: int
    raw_artifacts_missing: int
    raw_artifacts_corrupt: int
    import_directory_configured: bool
    import_directory_readable: bool | None
    backup_directory_present: bool
    backup_count: int
    latest_backup_age_days: float | None
    web_assets_present: bool
    processing_profiles: tuple[ProcessingProfile, ...]
    analysis_profile: AnalysisProfile
    containerized: bool
    maps_enabled: bool
    installed_map_count: int
    invalid_map_count: int
    unclaimed_map_file_count: int = 0
    restore_in_progress: bool = False


class Diagnose:
    """Turn what was observed into what somebody should do about it."""

    def __call__(self, observation: DeploymentObservation) -> DiagnosticReport:
        """Return every check, in a stable order, worst status available on top."""
        return DiagnosticReport(
            checks=(
                _release(observation),
                _data_directory(observation),
                _restore(observation),
                _database(observation),
                _database_integrity(observation),
                _schema(observation),
                _raw_storage(observation),
                _import_directory(observation),
                _backups(observation),
                _web_assets(observation),
                _maps(observation),
                _processing(observation),
                _analysis(observation),
            )
        )


def _release(observation: DeploymentObservation) -> DiagnosticCheck:
    """State which build is running, and where."""
    where = "in a container" if observation.containerized else "on the host"
    return DiagnosticCheck("release", CheckStatus.OK, f"TrackVault {observation.release}, {where}")


def _data_directory(observation: DeploymentObservation) -> DiagnosticCheck:
    """Everything persistent lives here, so nothing else matters if it is wrong.

    An absent directory is not a fault on its own -- it is what a deployment
    looks like before it has ever started, and the archive creates it. What
    decides the severity is whether it *could* be created: a data directory
    whose parent this user cannot write to will never appear, and that is worth
    saying now rather than at the first import.
    """
    if not observation.data_directory_present:
        if observation.data_directory_creatable:
            return DiagnosticCheck(
                "data_directory",
                CheckStatus.WARNING,
                "not created yet; the archive creates it the first time it starts",
            )
        return DiagnosticCheck(
            "data_directory",
            CheckStatus.ERROR,
            "the data directory does not exist and cannot be created by this user",
        )
    if not observation.data_directory_writable:
        return DiagnosticCheck(
            "data_directory",
            CheckStatus.ERROR,
            "the data directory is not writable by this user; check the mount's ownership",
        )
    return DiagnosticCheck("data_directory", CheckStatus.OK, "present and writable")


def _restore(observation: DeploymentObservation) -> DiagnosticCheck:
    """The one fault that makes every other line on this report untrustworthy.

    A publication that stopped halfway can leave a database from one archive
    beside a raw storage from another, so the track counts and the integrity
    figures below would each be describing a different deployment. It is an
    error rather than a warning for that reason: nothing is being lost, but
    nothing can be believed either.

    Ordinarily nobody sees this. Starting the archive resolves an interrupted
    restore before it serves anything, so a marker survives only until the next
    start -- which is exactly the window in which somebody runs `doctor`,
    wondering why the container will not come up.
    """
    if observation.restore_in_progress:
        return DiagnosticCheck(
            "restore",
            CheckStatus.ERROR,
            "a restore did not finish; starting the archive completes or undoes it",
        )
    return DiagnosticCheck("restore", CheckStatus.OK, "no restore is pending")


def _database(observation: DeploymentObservation) -> DiagnosticCheck:
    """An absent database is a fresh install, not a fault."""
    if not observation.database_present:
        return DiagnosticCheck(
            "database",
            CheckStatus.WARNING,
            "no database yet; it is created the first time the archive starts",
        )
    if observation.database_schema_version is None:
        return DiagnosticCheck(
            "database", CheckStatus.ERROR, "the database file exists but cannot be read"
        )
    return DiagnosticCheck("database", CheckStatus.OK, "present and readable")


def _database_integrity(observation: DeploymentObservation) -> DiagnosticCheck:
    """The check that reads every page, because a broken index reads as bad data."""
    if observation.database_intact is None:
        return DiagnosticCheck(
            "database_integrity", CheckStatus.WARNING, "no database to check yet"
        )
    if not observation.database_intact:
        return DiagnosticCheck(
            "database_integrity",
            CheckStatus.ERROR,
            "the database failed its own integrity check; restore the newest backup",
        )
    return DiagnosticCheck("database_integrity", CheckStatus.OK, "passed")


def _schema(observation: DeploymentObservation) -> DiagnosticCheck:
    """Older migrates, newer refuses -- the same rule the database itself applies."""
    stored = observation.database_schema_version
    installed = observation.installed_schema_version
    if stored is None:
        return DiagnosticCheck(
            "schema", CheckStatus.WARNING, f"none stored; build expects {installed}"
        )
    if stored > installed:
        return DiagnosticCheck(
            "schema",
            CheckStatus.ERROR,
            f"database schema {stored} is newer than this build ({installed}); "
            "a downgrade is not supported",
        )
    if stored < installed:
        return DiagnosticCheck(
            "schema",
            CheckStatus.WARNING,
            f"database schema {stored} is behind this build ({installed}); "
            "it migrates at the next start",
        )
    return DiagnosticCheck("schema", CheckStatus.OK, f"version {stored}")


def _raw_storage(observation: DeploymentObservation) -> DiagnosticCheck:
    """The one thing in the archive nobody can recreate.

    Corrupt beats missing: a missing artifact repairs itself the next time the
    same bytes are offered, and a corrupt one is evidence of a problem that is
    never overwritten.
    """
    if observation.raw_artifacts_corrupt:
        return DiagnosticCheck(
            "raw_storage",
            CheckStatus.ERROR,
            f"{observation.raw_artifacts_corrupt} of {observation.raw_artifacts_expected} "
            "stored originals do not match their content hash",
        )
    if observation.raw_artifacts_missing:
        return DiagnosticCheck(
            "raw_storage",
            CheckStatus.WARNING,
            f"{observation.raw_artifacts_missing} of {observation.raw_artifacts_expected} "
            "stored originals are missing; importing the same file again restores them",
        )
    return DiagnosticCheck(
        "raw_storage",
        CheckStatus.OK,
        f"{observation.raw_artifacts_expected} original(s), all matching their hash",
    )


def _import_directory(observation: DeploymentObservation) -> DiagnosticCheck:
    """The check that would have caught the folder nobody mounted."""
    if not observation.import_directory_configured:
        return DiagnosticCheck(
            "import_directory",
            CheckStatus.WARNING,
            "TRACKVAULT_IMPORT_DIR is not set, so `trackvault scan` is disabled",
        )
    if not observation.import_directory_readable:
        return DiagnosticCheck(
            "import_directory",
            CheckStatus.ERROR,
            "the configured import directory cannot be read; check that it is mounted",
        )
    return DiagnosticCheck("import_directory", CheckStatus.OK, "configured and readable")


def _backups(observation: DeploymentObservation) -> DiagnosticCheck:
    """An archive with no backup is one disk away from being no archive.

    No threshold is invented for "too old". Stating the age lets the person who
    knows how often they ride decide, and a number this code guessed would be
    wrong for most of them.
    """
    if not observation.backup_directory_present or not observation.backup_count:
        return DiagnosticCheck(
            "backups",
            CheckStatus.WARNING,
            "no backups found; `trackvault backup create` writes one",
        )
    age = observation.latest_backup_age_days
    when = "age unknown" if age is None else f"newest is {age:.1f} day(s) old"
    return DiagnosticCheck("backups", CheckStatus.OK, f"{observation.backup_count} found, {when}")


def _web_assets(observation: DeploymentObservation) -> DiagnosticCheck:
    """A deployment without the page is a valid one; it is just API-only."""
    if not observation.web_assets_present:
        return DiagnosticCheck(
            "web_assets",
            CheckStatus.WARNING,
            "no built browser application found; the archive answers the API only",
        )
    return DiagnosticCheck("web_assets", CheckStatus.OK, "present")


def _maps(observation: DeploymentObservation) -> DiagnosticCheck:
    """A package is a row and a file that hashes to it; either alone is invalid.

    The two ways of being invalid get different sentences because they get
    different actions. A row whose file is gone or damaged is reinstalled by
    somebody; a file no row claims is debris the next start clears, and telling
    an operator to reinstall it would send them looking for a region that is
    not missing.
    """
    unclaimed = observation.unclaimed_map_file_count
    unprovable = observation.invalid_map_count - unclaimed
    if unprovable:
        detail = f"{unprovable} installed map(s) cannot be proved; reinstall them"
        if unclaimed:
            detail += f", and {unclaimed} stored file(s) belong to no installation"
        return DiagnosticCheck("maps", CheckStatus.WARNING, detail)
    if unclaimed:
        return DiagnosticCheck(
            "maps",
            CheckStatus.WARNING,
            f"{unclaimed} stored map file(s) belong to no installation; "
            "the next start removes them",
        )
    if not observation.maps_enabled:
        return DiagnosticCheck(
            "maps", CheckStatus.OK, "map installation is switched off for this deployment"
        )
    return DiagnosticCheck("maps", CheckStatus.OK, f"{observation.installed_map_count} installed")


def _processing(observation: DeploymentObservation) -> DiagnosticCheck:
    """Which rules turn a source into tracks, so a changed number is explainable."""
    versions = ", ".join(
        f"{profile.importer}@{profile.importer_version}"
        for profile in observation.processing_profiles
    )
    return DiagnosticCheck("processing_profile", CheckStatus.OK, versions or "none installed")


def _analysis(observation: DeploymentObservation) -> DiagnosticCheck:
    """Which algorithms produced the metrics a reader is looking at."""
    profile = observation.analysis_profile
    return DiagnosticCheck(
        "analysis_profile",
        CheckStatus.OK,
        f"distance@{profile.distance_algorithm_version}, "
        f"movement@{profile.movement_algorithm_version}, "
        f"elevation@{profile.elevation_algorithm_version}, "
        f"metrics@{profile.metric_schema_version}",
    )
