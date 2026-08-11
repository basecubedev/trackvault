"""Reading and writing the manifest, strictly.

The manifest is the archive's own account of itself, and a restore trusts it
before it trusts anything else in the container. Everything decoded here is
therefore untrusted input in the strongest sense: it may have been written by a
newer build, an older one, a different program, or somebody who would like this
one to extract a file over ``/etc``.

So decoding **fails closed**. A missing key, a value of the wrong type, a
negative size, a digest that is not a digest -- each is a refusal with a stable
code, not a default. The alternative is a manifest that half-parses and a restore
that verifies half an archive.

Encoding is deterministic: sorted keys, one fixed indentation, a trailing
newline. Two archives of identical material then differ only where they should,
which is what makes a manifest worth diffing.
"""

import json
from datetime import datetime
from typing import Any

from gpx_view.application.archive import (
    ArchiveContents,
    ArchiveCounts,
    ArchivedFile,
    ArchiveError,
    ArchiveErrorCode,
    ArchiveManifest,
    ArchiveOmission,
)

SHA256_HEX_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


def encode_manifest(manifest: ArchiveManifest) -> bytes:
    """Render a manifest as the bytes that go into the container."""
    document = {
        "format_name": manifest.format_name,
        "format_version": manifest.format_version,
        "created_at": manifest.created_at.isoformat(),
        "gpx_view_version": manifest.gpx_view_version,
        "schema_version": manifest.schema_version,
        "counts": {
            "raw_imports": manifest.counts.raw_imports,
            "tracks": manifest.counts.tracks,
            "classification_overrides": manifest.counts.classification_overrides,
            "user_metadata": manifest.counts.user_metadata,
        },
        "contents": {
            "database": _encode_file(manifest.contents.database),
            "raw_imports": [_encode_file(item) for item in manifest.contents.raw_imports],
        },
        "omissions": [
            {"kind": omission.kind, "reason": omission.reason} for omission in manifest.omissions
        ],
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _encode_file(item: ArchivedFile) -> dict[str, Any]:
    """Render one member's description."""
    return {"name": item.name, "size_bytes": item.size_bytes, "sha256": item.sha256}


def decode_manifest(content: bytes) -> ArchiveManifest:
    """Parse a manifest, refusing anything this build cannot fully interpret.

    Raises:
        ArchiveError: ``archive_manifest_invalid`` for anything malformed. The
            detail stays structural -- it names the field, never the value, so a
            diagnostic cannot echo an attacker's string back into a log.
    """
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveError(
            ArchiveErrorCode.MANIFEST_INVALID, "the manifest is not readable JSON"
        ) from error
    if not isinstance(document, dict):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, "the manifest is not an object")
    contents = _decode_contents(_object(document, "contents"))
    try:
        return ArchiveManifest(
            format_name=_text(document, "format_name"),
            format_version=_whole_number(document, "format_version"),
            created_at=_instant(document, "created_at"),
            gpx_view_version=_text(document, "gpx_view_version"),
            schema_version=_whole_number(document, "schema_version"),
            contents=contents,
            counts=_decode_counts(_object(document, "counts")),
            omissions=_decode_omissions(document.get("omissions", [])),
        )
    except ValueError as error:
        raise ArchiveError(
            ArchiveErrorCode.MANIFEST_INVALID, "the manifest states an impossible value"
        ) from error


def _decode_contents(document: dict[str, Any]) -> ArchiveContents:
    """Parse the member list, refusing a name that could become a path."""
    raw = document.get("raw_imports", [])
    if not isinstance(raw, list):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, "raw_imports is not a list")
    return ArchiveContents(
        database=_decode_file(_object(document, "database")),
        raw_imports=tuple(_decode_file(_as_object(item, "raw_imports")) for item in raw),
    )


def _decode_file(document: dict[str, Any]) -> ArchivedFile:
    """Parse one member's description, checking the digest is one."""
    digest = _text(document, "sha256")
    if len(digest) != SHA256_HEX_LENGTH or not _HEX_DIGITS.issuperset(digest):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, "a member digest is not a sha256")
    size = _whole_number(document, "size_bytes")
    if size < 0:
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, "a member size is negative")
    return ArchivedFile(name=_text(document, "name"), size_bytes=size, sha256=digest)


def _decode_counts(document: dict[str, Any]) -> ArchiveCounts:
    """Parse what the archive says it holds."""
    return ArchiveCounts(
        raw_imports=_whole_number(document, "raw_imports"),
        tracks=_whole_number(document, "tracks"),
        classification_overrides=_whole_number(document, "classification_overrides"),
        user_metadata=_whole_number(document, "user_metadata"),
    )


def _decode_omissions(value: object) -> tuple[ArchiveOmission, ...]:
    """Parse what the archive says it left out.

    An archive that states no omissions is describing itself as complete, which
    is a claim rather than a silence -- so an absent list decodes to an empty
    tuple and never to "the usual ones".
    """
    if not isinstance(value, list):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, "omissions is not a list")
    return tuple(
        ArchiveOmission(
            kind=_text(_as_object(item, "omissions"), "kind"),
            reason=_text(_as_object(item, "omissions"), "reason"),
        )
        for item in value
    )


def _object(document: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a nested object, or refuse the manifest."""
    return _as_object(document.get(key), key)


def _as_object(value: object, key: str) -> dict[str, Any]:
    """Refuse a value that should have been an object."""
    if not isinstance(value, dict):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, f"{key} is not an object")
    return value


def _text(document: dict[str, Any], key: str) -> str:
    """Return a string field, or refuse the manifest."""
    value = document.get(key)
    if not isinstance(value, str):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, f"{key} is not a string")
    return value


def _whole_number(document: dict[str, Any], key: str) -> int:
    """Return an integer field, refusing a bool and refusing a float.

    ``bool`` is a subclass of ``int`` in Python, and a manifest whose
    ``schema_version`` decoded as ``True`` would compare as version 1.
    """
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, f"{key} is not a whole number")
    return value


def _instant(document: dict[str, Any], key: str) -> datetime:
    """Return a timezone-aware instant, or refuse the manifest."""
    try:
        parsed = datetime.fromisoformat(_text(document, key))
    except ValueError as error:
        raise ArchiveError(
            ArchiveErrorCode.MANIFEST_INVALID, f"{key} is not a readable instant"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArchiveError(ArchiveErrorCode.MANIFEST_INVALID, f"{key} carries no timezone")
    return parsed
