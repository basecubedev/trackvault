"""Stable error codes for the offline map capability.

Separate from `ImportErrorCode` on purpose. The two capabilities fail for
unrelated reasons, and one enum holding both would mean every caller of either
has to read past the other's vocabulary to find out what can happen.

Codes are part of the public contract: they reach HTTP responses and the stored
state of a failed job, so they are lower case, underscore separated, and not
renamed once released. An error carries a code and a short structural detail --
never a URL with a query, never a file system path.
"""

from enum import StrEnum


class MapErrorCode(StrEnum):
    """Why a map operation could not be completed.

    Attributes:
        MAP_REGION_UNKNOWN: The catalog holds no such region.
        MAP_PACKAGE_UNAVAILABLE: The region exists but the provider publishes no
            package for it. A normal answer for a country whose data is only
            offered per state.
        MAP_PROVIDER_UNAVAILABLE: The provider could not be reached. Installed
            maps are unaffected, which is what the message has to say.
        MAP_CATALOG_INVALID: The provider answered with something this build
            cannot read as a catalog.
        MAP_DOWNLOAD_FAILED: The transfer did not complete.
        MAP_DOWNLOAD_TOO_LARGE: The response exceeded the configured ceiling,
            either by declaring it or by reaching it mid-stream.
        MAP_DOWNLOAD_REDIRECT_REFUSED: A redirect pointed outside the hosts the
            provider declares. Following it would turn a region name into an
            arbitrary request this server makes on a caller's behalf.
        MAP_INSUFFICIENT_DISK_SPACE: There is not enough room for the new
            package beside the one already installed.
        MAP_PACKAGE_INVALID: The downloaded file is not a package this build can
            read -- wrong container, missing tables, impossible metadata.
        MAP_PACKAGE_SCHEMA_UNSUPPORTED: A readable package whose tiles speak a
            vocabulary the installed styles are not written against. Named
            separately because it is the provider changing something, not a
            broken download.
        MAP_PACKAGE_LICENCE_MISSING: The package states no licence or no author.
            Refused rather than installed under an assumption, because the
            attribution shown to a reader has to come from the package.
        MAP_PACKAGE_EMPTY: A valid container holding no tiles.
        MAP_PACKAGE_NOT_INSTALLED: The operation needs a package that is not
            installed.
        MAP_MUTATION_IN_PROGRESS: This region is already being installed,
            updated or removed.
        MAP_STORAGE_FAILED: The managed copy could not be written, published or
            removed.
        MAP_JOB_NOT_FOUND: No such installation job.
        MAP_TILE_READ_FAILED: A tile could not be read from an installed
            package. The track overlay is unaffected.
    """

    MAP_REGION_UNKNOWN = "map_region_unknown"
    MAP_PACKAGE_UNAVAILABLE = "map_package_unavailable"
    MAP_PROVIDER_UNAVAILABLE = "map_provider_unavailable"
    MAP_CATALOG_INVALID = "map_catalog_invalid"
    MAP_DOWNLOAD_FAILED = "map_download_failed"
    MAP_DOWNLOAD_TOO_LARGE = "map_download_too_large"
    MAP_DOWNLOAD_REDIRECT_REFUSED = "map_download_redirect_refused"
    MAP_INSUFFICIENT_DISK_SPACE = "map_insufficient_disk_space"
    MAP_PACKAGE_INVALID = "map_package_invalid"
    MAP_PACKAGE_SCHEMA_UNSUPPORTED = "map_package_schema_unsupported"
    MAP_PACKAGE_LICENCE_MISSING = "map_package_licence_missing"
    MAP_PACKAGE_EMPTY = "map_package_empty"
    MAP_PACKAGE_NOT_INSTALLED = "map_package_not_installed"
    MAP_MUTATION_IN_PROGRESS = "map_mutation_in_progress"
    MAP_STORAGE_FAILED = "map_storage_failed"
    MAP_JOB_NOT_FOUND = "map_job_not_found"
    MAP_TILE_READ_FAILED = "map_tile_read_failed"


class MapOperationError(Exception):
    """A map operation could not be completed, for a named and stable reason.

    Attributes:
        code: The stable error code.
        detail: A short structural hint such as ``"needs 812 MB, 300 MB free"``.
            Never a URL with a query string and never a path.
    """

    def __init__(self, code: MapErrorCode, detail: str = "") -> None:
        """Build the error from its code and an optional structural hint."""
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)
