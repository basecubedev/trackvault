"""The single canonical import use case.

Every input path -- a command-line import, the server import directory, and any
future upload or API channel -- runs through :class:`ImportTracks`. There is
never a second parsing or persistence path per channel, because "the import bug
is fixed in one place" is only true if there is one place.

The pipeline:

```
bytes
  -> size limit
  -> content hash
  -> exact duplicate check, against metadata *and* the managed bytes
  -> managed raw storage
  -> normalization
```

An exact duplicate is only a no-op while the archive can still produce the bytes
it says it holds. A database row and a hash-valid managed artifact are two facts,
and recognising the first says nothing about the second.

The last step -- detect, normalize, classify, record a run, become the current
generation -- is :class:`~trackvault.application.normalization.NormalizeRawImport`,
which reprocessing runs too. There is one implementation of it because there is
one pipeline.

Import failures are returned, not raised: one unreadable file must not stop the
files after it. The raw import is kept even when processing fails, together with
a failed run naming the reason, so the file can be reprocessed once an importer
learns to read it.
"""

import hashlib
import logging
from dataclasses import dataclass
from enum import StrEnum

from trackvault.application.analyze import AnalyzeTrack
from trackvault.application.errors import ImportErrorCode, TrackImportError
from trackvault.application.importing import ImportLimits
from trackvault.application.normalization import NormalizeRawImport
from trackvault.application.ports import Clock, RawArtifactState, RawImportStore, TrackRepository
from trackvault.domain import InputChannel, RawImport

logger = logging.getLogger(__name__)

_PATH_LIKE = ("/", "\\")
_RESERVED_FILENAMES = frozenset({".", ".."})

# What a managed copy that is neither healthy nor simply absent is called. Both
# are fail-closed: the archive holds something it cannot vouch for, and that is
# worth an operator's attention rather than an overwrite.
_INTEGRITY_FAILURES = {
    RawArtifactState.CORRUPT: ImportErrorCode.RAW_STORAGE_CORRUPT,
    RawArtifactState.UNREADABLE: ImportErrorCode.RAW_STORAGE_FAILED,
}


class ImportStatus(StrEnum):
    """How one import attempt ended.

    Four outcomes, because an operator has to be able to tell three different
    things apart: nothing was needed, something was repaired, and something is
    wrong. Hiding a repair inside ``DUPLICATE`` would make the archive silently
    fix itself, and hiding an integrity failure there would make it silently not.

    Attributes:
        IMPORTED: New bytes were stored and normalized.
        DUPLICATE: The archive already holds these bytes, and holds them
            correctly. Nothing to do.
        REPAIRED: The archive knew these bytes but had lost its managed copy, and
            the copy was restored from the bytes offered again. The normalized
            data was never in question and is unchanged.
        FAILED: The attempt did not complete. ``error_code`` says why.
    """

    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    REPAIRED = "repaired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImportRequest:
    """Bytes offered for import, with the little we know about their origin.

    Attributes:
        content: The complete bytes of the candidate file.
        original_filename: The name the bytes arrived under, for display only. A
            value carrying a path component is discarded rather than trusted.
        input_channel: Which input path the bytes arrived through.
    """

    content: bytes
    original_filename: str | None = None
    input_channel: InputChannel = InputChannel.LOCAL_FILE


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """What one import attempt produced.

    Attributes:
        status: Whether the import stored something, recognised a duplicate, or
            failed.
        sha256: Content hash of the offered bytes. Always known once the bytes
            were read.
        track_ids: The tracks the import produced, or the tracks the duplicate
            already had.
        error_code: Why a failed import failed -- or, for bytes the archive
            already holds, why they never became tracks. A duplicate of a file
            that could not be read is still not in the archive's tracks, and
            "nothing to do" would hide that from whoever offered it again.
    """

    status: ImportStatus
    sha256: str
    track_ids: tuple[int, ...] = ()
    error_code: ImportErrorCode | None = None


class ImportTracks:
    """Imports one candidate file through the whole pipeline."""

    def __init__(
        self,
        *,
        raw_store: RawImportStore,
        repository: TrackRepository,
        clock: Clock,
        normalize: NormalizeRawImport,
        analyze: AnalyzeTrack | None = None,
    ) -> None:
        """Wire the use case to its ports.

        ``analyze`` is optional and best effort. Wiring it here rather than into
        each input path is what keeps a scanned sync folder, a command-line
        import and a future API import producing the same thing: a track that is
        immediately worth a statistic. Leaving it out gives an archive whose
        metrics are derived only on demand, which is a deployment choice rather
        than a different pipeline.
        """
        self._raw_store = raw_store
        self._repository = repository
        self._clock = clock
        self._normalize = normalize
        self._analyze = analyze

    @property
    def limits(self) -> ImportLimits:
        """Return the limits this use case enforces.

        Input paths read bounded amounts of data, and they take the bound from
        here so that no caller can bound differently from what is enforced.
        """
        return self._normalize.limits

    @property
    def file_suffixes(self) -> frozenset[str]:
        """Return the file name suffixes an installed adapter can read.

        A directory scan offers only files named like this, and takes the set
        from here so that installing an adapter is also what makes its files
        discoverable. The suffix never decides what content *is*.
        """
        return self._normalize.file_suffixes

    def __call__(self, request: ImportRequest) -> ImportOutcome:
        """Run one import attempt and report what happened."""
        content = request.content
        sha256 = hashlib.sha256(content).hexdigest()

        if len(content) > self.limits.max_bytes:
            # Nothing is stored: an oversized file is refused before it can cost
            # anything, and there is no source evidence worth keeping either.
            return self._failed(sha256, ImportErrorCode.IMPORT_TOO_LARGE)

        if self._repository.find_raw_import(sha256) is not None:
            return self._already_known(sha256, content)

        importer = self._normalize.importer_for(content)
        try:
            self._raw_store.store(content, sha256)
        except TrackImportError as error:
            return self._failed(sha256, error.code)

        raw_import = RawImport(
            sha256=sha256,
            size_bytes=len(content),
            original_filename=_display_filename(request.original_filename),
            received_at=self._clock.now(),
            media_type=None if importer is None else importer.media_type,
            input_channel=request.input_channel,
        )

        result = self._normalize(raw_import, content)
        if not result.succeeded:
            return ImportOutcome(ImportStatus.FAILED, sha256, (), result.error_code)
        self._derive_metrics(result.track_ids)
        return ImportOutcome(ImportStatus.IMPORTED, sha256, result.track_ids)

    def _derive_metrics(self, track_ids: tuple[int, ...]) -> None:
        """Derive the metrics of what was just imported, if that is wired.

        A separate lifecycle with its own status, deliberately: the import has
        already succeeded by the time this runs, and nothing here can change
        that. An analysis that fails leaves a track that is complete, readable
        and reachable by ``analyze --outdated``.
        """
        if self._analyze is not None:
            self._analyze.best_effort(track_ids)

    def _already_known(self, sha256: str, content: bytes) -> ImportOutcome:
        """Answer an offer of bytes the archive has a record of.

        A record is not the bytes. Recognising the hash proves the archive *once*
        held them, and answering ``DUPLICATE`` on that alone means an archive
        that has lost an artifact keeps saying "already imported" to the only
        offer of those bytes it will ever get again.

        Whether the normalized data is still *current* is a third question again,
        and this is deliberately not where it is asked: a directory scan must stay
        cheap and must not re-parse a file it already failed on. Reprocessing is
        the explicit action for that.
        """
        state = self._raw_store.integrity(sha256)
        if state is RawArtifactState.HEALTHY:
            existing = self._repository.track_ids_for(sha256)
            logger.info("import.duplicate raw_import=%s tracks=%d", sha256[:12], len(existing))
            return ImportOutcome(
                ImportStatus.DUPLICATE, sha256, existing, self._never_imported(sha256, existing)
            )

        if state is RawArtifactState.MISSING:
            # The same source evidence was offered again, and it hashes to the
            # digest the raw import is filed under -- the same proof the first
            # import needed. Putting it back is recovery, not a guess.
            try:
                self._raw_store.store(content, sha256)
            except TrackImportError as error:
                return self._failed(sha256, error.code)
            existing = self._repository.track_ids_for(sha256)
            logger.info("import.repaired raw_import=%s tracks=%d", sha256[:12], len(existing))
            return ImportOutcome(
                ImportStatus.REPAIRED, sha256, existing, self._never_imported(sha256, existing)
            )

        # A corrupt or unreadable artifact is evidence of a problem. Overwriting
        # it with the bytes it should have had would destroy the only trace of
        # whatever damaged it, so the import fails and says which it was.
        return self._failed(sha256, _INTEGRITY_FAILURES[state])

    def _never_imported(self, sha256: str, existing: tuple[int, ...]) -> ImportErrorCode | None:
        """Return why known bytes never became tracks, or ``None`` if they did.

        Read from the newest attempt, which is already on record: nothing is
        parsed and nothing is recorded to answer this. A source with a current
        generation was imported, however many tracks it held and whatever a
        later reprocessing attempt made of it. A stored reason this build does
        not know is not repeated -- what comes out of the database is validated
        before it is believed.
        """
        if existing:
            return None
        snapshot = self._repository.processing_snapshot(sha256)
        if snapshot is None or snapshot.current_run is not None or snapshot.latest_run is None:
            return None
        stored = snapshot.latest_run.error_code
        try:
            return None if stored is None else ImportErrorCode(stored)
        except ValueError:
            return None

    @staticmethod
    def _failed(sha256: str, code: ImportErrorCode) -> ImportOutcome:
        """Report a failure that left nothing stored."""
        logger.info("import.failed raw_import=%s reason=%s", sha256[:12], code.value)
        return ImportOutcome(ImportStatus.FAILED, sha256, (), code)


def _display_filename(filename: str | None) -> str | None:
    """Return a filename safe to record as display metadata, or ``None``.

    A value carrying a path component is discarded instead of sanitised: it is
    never needed, and quietly rewriting it would suggest it meant something.
    """
    if filename is None:
        return None
    candidate = filename.strip()
    if not candidate or candidate in _RESERVED_FILENAMES:
        return None
    if any(separator in candidate for separator in _PATH_LIKE):
        return None
    return candidate
