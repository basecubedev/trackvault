"""Stable error codes for the import and query paths.

Codes are part of the public contract: they appear in HTTP responses, in the
status of a failed processing run, and in operator logs. They are therefore
lower case, underscore separated, and not renamed once released.

An error carries a code and a short, structural detail. It never carries
coordinates, raw markup, a file system path or any other part of the payload --
diagnostics must not become a side channel for personal movement data.
"""

from enum import StrEnum


class ImportErrorCode(StrEnum):
    """Why an import or a lookup could not be completed.

    Attributes:
        UNSUPPORTED_FORMAT: No importer recognised the content.
        INVALID_GPX: The content claims to be GPX but cannot be read as GPX. The
            format is named on purpose: "not GPX at all" and "broken GPX" are
            different problems for whoever has to fix the file.
        UNSAFE_XML: The document uses a document type definition or entity
            construct that this application refuses to process.
        IMPORT_TOO_LARGE: The input exceeds the configured byte limit.
        TOO_MANY_TRACKS: The document holds more tracks than the limit allows.
        TOO_MANY_TRACK_SEGMENTS: One track holds more segments than allowed.
        TOO_MANY_TRACK_POINTS: The document holds more positions than allowed.
        INVALID_COORDINATE: A position is missing or cannot exist.
        INVALID_TIMESTAMP: A time value is unreadable or has no timezone, which
            would make the instant depend on who reads it.
        RAW_STORAGE_FAILED: The original bytes could not be stored or read, so
            the import was abandoned rather than continued without its source
            evidence.
        RAW_STORAGE_MISSING: The archive knows the raw import but its managed
            copy is gone. Recoverable: offering the same bytes again restores it,
            because they hash to the digest the raw import is filed under.
        RAW_STORAGE_CORRUPT: A managed artifact exists under a digest its bytes
            do not have. Not recoverable automatically -- it is evidence of disk
            corruption or tampering, and overwriting it would destroy that
            evidence.
        PERSISTENCE_FAILED: The normalized result could not be stored.
        TRACK_NOT_FOUND: The requested track does not exist.
        ANALYSIS_FAILED: Metrics could not be derived from a track's geometry.
            The track itself is unaffected: it keeps its geometry and whatever
            metrics an earlier run had already produced.
    """

    UNSUPPORTED_FORMAT = "unsupported_format"
    INVALID_GPX = "invalid_gpx"
    UNSAFE_XML = "unsafe_xml"
    IMPORT_TOO_LARGE = "import_too_large"
    TOO_MANY_TRACKS = "too_many_tracks"
    TOO_MANY_TRACK_SEGMENTS = "too_many_track_segments"
    TOO_MANY_TRACK_POINTS = "too_many_track_points"
    INVALID_COORDINATE = "invalid_coordinate"
    INVALID_TIMESTAMP = "invalid_timestamp"
    RAW_STORAGE_FAILED = "raw_storage_failed"
    RAW_STORAGE_MISSING = "raw_storage_missing"
    RAW_STORAGE_CORRUPT = "raw_storage_corrupt"
    PERSISTENCE_FAILED = "persistence_failed"
    TRACK_NOT_FOUND = "track_not_found"
    ANALYSIS_FAILED = "analysis_failed"


class TrackImportError(Exception):
    """An import could not be completed, for a named and stable reason.

    Attributes:
        code: The stable error code.
        detail: A short structural hint such as ``"track 2, point 41"``. Never a
            payload excerpt, a coordinate or a path.
    """

    def __init__(self, code: ImportErrorCode, detail: str = "") -> None:
        """Build the error from its code and an optional structural hint."""
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
