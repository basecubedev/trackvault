"""The import port and the safety limits every importer works under.

Imported files are untrusted input. The limits below bound what a single document
may cost before anything is stored, and the port is the whole contract an adapter
has to satisfy: recognise your own format, and produce normalized track
candidates. No factory, no manager, no registry hierarchy.
"""

from dataclasses import dataclass, fields
from typing import Protocol, runtime_checkable

from trackvault.domain import ImportedTrack

DEFAULT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TRACKS = 100
DEFAULT_MAX_SEGMENTS_PER_TRACK = 1000
DEFAULT_MAX_POINTS = 500_000


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """What a single import may cost at most.

    The defaults comfortably cover an ordinary long GPS recording: 500 000
    positions is roughly a fortnight of continuous one-second logging, and 16 MiB
    of XML holds more than that. They exist to bound a hostile or broken file, not
    to ration normal use, and they are configurable through ``Settings``.

    Attributes:
        max_bytes: Largest accepted input size. Checked before anything is read.
        max_tracks: Most tracks one document may contain.
        max_segments_per_track: Most segments one track may contain.
        max_points: Most positions one document may contain in total.

    Raises:
        ValueError: If any limit is not positive. A zero limit would disable
            imports rather than protect them.
    """

    max_bytes: int = DEFAULT_MAX_BYTES
    max_tracks: int = DEFAULT_MAX_TRACKS
    max_segments_per_track: int = DEFAULT_MAX_SEGMENTS_PER_TRACK
    max_points: int = DEFAULT_MAX_POINTS

    def __post_init__(self) -> None:
        """Reject a limit that would make importing impossible."""
        for field in fields(self):
            if getattr(self, field.name) < 1:
                raise ValueError(f"{field.name} must be positive")


@runtime_checkable
class TrackImporter(Protocol):
    """What one exchange format adapter has to offer.

    An adapter recognises its own format from the content, and turns that content
    into normalized track candidates plus neutral evidence. It never decides a
    track kind, and its own format types never leave it.
    """

    @property
    def format_id(self) -> str:
        """Return the lower-case format identifier, for example ``gpx``."""
        ...

    @property
    def importer_version(self) -> str:
        """Return this adapter's version, recorded with every processing run."""
        ...

    @property
    def media_type(self) -> str:
        """Return the media type recorded as a hint on an accepted raw import."""
        ...

    @property
    def file_suffixes(self) -> tuple[str, ...]:
        """Return the lower-case file name suffixes this format is exchanged under.

        Used only to choose which files of a directory are offered at all. What
        the offered content *is* stays the decision of :meth:`detects`.
        """
        ...

    def detects(self, content: bytes) -> bool:
        """Report whether this adapter recognises the content as its own format.

        The decision is made from the bytes. A filename is display metadata and
        must not influence it.
        """
        ...

    def import_tracks(self, content: bytes, limits: ImportLimits) -> tuple[ImportedTrack, ...]:
        """Normalize the content into track candidates.

        One document may yield any number of candidates, including none.

        Raises:
            TrackImportError: If the content cannot be read safely or exceeds a
                limit.
        """
        ...
