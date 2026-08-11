"""What was offered for import, as immutable source evidence.

A raw import records the *source*: the bytes, their content hash, and the
circumstances under which they arrived. It deliberately records nothing about how
those bytes were later interpreted -- importer names, importer versions and
normalization schema versions are properties of a processing run, not of a file.
See :mod:`gpx_view.domain.processing`.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

SHA256_HEX_LENGTH = 64
SHORT_HASH_LENGTH = 12

_HEX_DIGITS = frozenset("0123456789abcdef")
_PATH_SEPARATORS = ("/", "\\")
_RESERVED_FILENAMES = frozenset({".", ".."})


class InputChannel(StrEnum):
    """How bytes reached the application.

    The channel is recorded metadata, never business authority: the same bytes
    arriving through a different channel are still the same raw import.
    """

    LOCAL_FILE = "local_file"
    IMPORT_DIRECTORY = "import_directory"


@dataclass(frozen=True, slots=True)
class RawImport:
    """One immutable import artifact.

    Identity is the content hash. Two files with identical bytes are the same raw
    import regardless of their filename or the channel they arrived through, and
    that is precisely what makes exact-duplicate detection content-based.

    Attributes:
        sha256: Lower-case hex SHA-256 digest of the stored bytes, and the
            identity of this raw import.
        size_bytes: Size of the stored bytes.
        original_filename: The filename the bytes arrived under, without any path
            component. Display metadata only -- it never authorises a storage
            location. ``None`` when the channel knows no filename.
        received_at: The timezone-aware instant the bytes were accepted.
        media_type: A media or format hint derived from the content, if one could
            be recognised. A hint, not a guarantee.
        input_channel: Which input path the bytes arrived through.

    Raises:
        ValueError: If the hash is not a SHA-256 digest, the size is negative, the
            filename carries a path component, or the timestamp is naive.
    """

    sha256: str
    size_bytes: int
    original_filename: str | None
    received_at: datetime
    media_type: str | None = None
    input_channel: InputChannel = InputChannel.LOCAL_FILE

    def __post_init__(self) -> None:
        """Reject values that would make the record ambiguous or unsafe."""
        if len(self.sha256) != SHA256_HEX_LENGTH or not _HEX_DIGITS.issuperset(self.sha256):
            raise ValueError("sha256 must be a lower-case hex SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if self.original_filename is not None:
            self._validate_filename(self.original_filename)
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")

    @staticmethod
    def _validate_filename(filename: str) -> None:
        """Reject a filename that carries a path component."""
        if not filename.strip():
            raise ValueError("original_filename must not be blank")
        if filename in _RESERVED_FILENAMES or any(sep in filename for sep in _PATH_SEPARATORS):
            raise ValueError("original_filename is display metadata and must carry no path")

    @property
    def short_sha256(self) -> str:
        """Return a log-safe hash prefix that identifies this import."""
        return self.sha256[:SHORT_HASH_LENGTH]
