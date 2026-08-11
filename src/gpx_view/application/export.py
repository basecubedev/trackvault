"""Getting data back out, in the two shapes that mean different things.

```
raw export        the bytes that arrived      evidence, byte-identical
document export   a generated exchange copy   what this build believes today
```

Those are not two spellings of one feature, and the distinction is the whole
reason this module exists. A raw export answers "give me my file back" and must
be identical to what was imported, forever, whatever the importer has learned
since. A document export answers "give me this track in something another
application reads", and it is produced from the current normalized generation by
the algorithms this build installs -- so it legitimately changes when they do.

Neither knows an exchange format. Writing one is a port
(:class:`TrackDocumentWriter`) that an infrastructure adapter implements, which
is the same boundary the importers sit on: a future FIT or GeoJSON writer is
another adapter and no change here.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gpx_view.application.errors import ImportErrorCode, TrackImportError
from gpx_view.application.ports import RawImportStore, TrackRepository
from gpx_view.domain import Activity, TrackSegment

MAX_SUGGESTED_NAME_LENGTH = 80
"""How long a filename derived from a user's title may get.

A title is bounded already, but a filename is handed to a file system that may
have a shorter opinion, and a name that fails to save is worse than a shortened
one.
"""

_SAFE_NAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ."
)


@dataclass(frozen=True, slots=True)
class ExchangeDocument:
    """One track, reduced to what an exchange format can honestly carry.

    Deliberately smaller than a stored track. A classification is a verdict this
    project reached with its own evidence rules and its own confidence, and no
    exchange format has a field that means it -- so it is not here, and a writer
    cannot invent a place for it.

    Attributes:
        title: The name to write, already resolved to what should be displayed:
            the user's correction if they made one, otherwise the source's own.
            ``None`` when nothing named the track, which stays an absence.
        activity: What the track was. ``UNKNOWN`` is written as nothing at all --
            "we do not know" is not a value another application should adopt.
        segments: The geometry, with its boundaries intact. A paused recording is
            several segments and joining them would invent a continuity nobody
            recorded.
    """

    title: str | None
    activity: Activity
    segments: tuple[TrackSegment, ...]


@runtime_checkable
class TrackDocumentWriter(Protocol):
    """What one exchange format writer has to offer.

    The mirror of :class:`~gpx_view.application.importing.TrackImporter`, and
    deliberately just as small: state your media type and your extension, and
    turn a document into bytes.

    Implementations live in infrastructure. Nothing here knows what they produce.
    """

    @property
    def media_type(self) -> str:
        """Return the media type of the documents this writer produces."""
        ...

    @property
    def file_extension(self) -> str:
        """Return the filename suffix, leading dot included."""
        ...

    def write(self, document: ExchangeDocument) -> bytes:
        """Render one track as a document in this writer's format."""
        ...


@dataclass(frozen=True, slots=True)
class RawSourceExport:
    """The original bytes of one import, and how to offer them.

    Attributes:
        sha256: The content hash the bytes are filed under, and their identity.
        content: The bytes exactly as they arrived.
        original_filename: The name they arrived under, or ``None``. Display
            metadata: it never decided where anything was stored and it does not
            decide where anything is written now.
        media_type: What the archive recognised the content as, if anything.
    """

    sha256: str
    content: bytes
    original_filename: str | None
    media_type: str | None

    @property
    def suggested_filename(self) -> str:
        """Return a name to offer the file under.

        The original name when there was one, because getting your own file back
        under its own name is the point. Otherwise the content hash, which is
        the only other thing that is true about these bytes -- no extension is
        invented, because the archive stores a media type hint rather than a
        promise.
        """
        if self.original_filename:
            return self.original_filename
        return f"{self.sha256[:12]}.source"


@dataclass(frozen=True, slots=True)
class DocumentExport:
    """A generated exchange document, and what it is.

    Attributes:
        track_id: The track this was produced from.
        content: The rendered document.
        media_type: What it is, for a ``Content-Type`` or a file manager.
        suggested_filename: A name to offer it under, derived from the displayed
            title and reduced to characters a file system will accept.
    """

    track_id: int
    content: bytes
    media_type: str
    suggested_filename: str


class ExportRawSource:
    """Hand back the original bytes of one import, unchanged.

    The archive's central promise, made readable. The store verifies the bytes
    against the hash they are filed under before returning them, so an export
    cannot quietly hand out a corrupted artifact -- a caller gets the original or
    a named error, never something in between.
    """

    def __init__(self, repository: TrackRepository, raw_store: RawImportStore) -> None:
        """Wire the use case to the metadata and the managed bytes."""
        self._repository = repository
        self._raw_store = raw_store

    def __call__(self, sha256: str) -> RawSourceExport | None:
        """Return the original import, or ``None`` if the archive never held it.

        ``None`` is an answer rather than a failure: a hash nobody imported is
        not an error condition, it is an absence. A hash the archive *does* know
        whose artifact is damaged is a different thing entirely, and it raises.

        Raises:
            TrackImportError: ``raw_storage_missing`` if the metadata exists but
                the managed copy is gone, ``raw_storage_corrupt`` if its bytes
                are not the ones the digest names.
        """
        raw_import = self._repository.find_raw_import(sha256)
        if raw_import is None:
            return None
        return RawSourceExport(
            sha256=raw_import.sha256,
            content=self._raw_store.read(raw_import.sha256),
            original_filename=raw_import.original_filename,
            media_type=raw_import.media_type,
        )


class ExportTrackDocument:
    """Render one current track as an exchange document.

    What comes out is **not** the file that was imported. It is this archive's
    current normalized generation, written into an exchange format, and that
    distinction is deliberate: it means an importer upgrade improves the export,
    and it means the export is never mistaken for the evidence.
    """

    def __init__(self, repository: TrackRepository, writer: TrackDocumentWriter) -> None:
        """Wire the use case to the archive and to one format writer."""
        self._repository = repository
        self._writer = writer

    def __call__(self, track_id: int) -> DocumentExport | None:
        """Return the rendered document, or ``None`` for a track that is not current.

        A track that a later processing run stopped producing is history, and
        history is a separate question -- the same rule every other read surface
        applies.

        Raises:
            TrackImportError: ``track_not_found`` if the track exists without
                geometry, which is data damage rather than an absence.
        """
        summary = self._repository.get_track(track_id)
        if summary is None:
            return None
        segments = self._repository.get_geometry(track_id)
        if segments is None:
            raise TrackImportError(
                ImportErrorCode.TRACK_NOT_FOUND, "the track holds no readable geometry"
            )
        content = self._writer.write(
            ExchangeDocument(
                title=summary.display_title,
                activity=summary.activity,
                segments=segments,
            )
        )
        return DocumentExport(
            track_id=track_id,
            content=content,
            media_type=self._writer.media_type,
            suggested_filename=suggested_filename(
                summary.display_title, track_id, self._writer.file_extension
            ),
        )


def suggested_filename(title: str | None, track_id: int, extension: str) -> str:
    """Derive a file name from a title, or fall back to the track's identity.

    A title is somebody's own text and may hold anything -- a slash, a null byte,
    a leading dot, the whole of a sentence. Rather than escaping the dangerous
    characters, this keeps only the harmless ones: an allow list cannot be
    incomplete in the direction that matters, and the worst outcome is a duller
    name than the title deserved.
    """
    kept = "".join(character for character in (title or "") if character in _SAFE_NAME_CHARACTERS)
    cleaned = "-".join(kept.split())[:MAX_SUGGESTED_NAME_LENGTH].strip("-.")
    return f"{cleaned or f'track-{track_id}'}{extension}"
