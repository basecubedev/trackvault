"""What an installed map package is, and what states it can be in.

> A map package is a replaceable external dataset, not source evidence.

That sentence decides the whole model, and it is the one place the maps
capability deliberately does *not* copy the raw import. A GPX file is the only
copy of somebody's afternoon: it is immutable, never overwritten, and a corrupt
artifact is preserved because it is evidence of a problem. A map package came
from a public server that still has it. Corruption there is a reason to
discard and reinstall, not to fail closed forever.

What is copied from that model is the integrity rule, because it is about
honesty rather than about evidence:

```
database row  +  managed file that hashes to what the row says   ->  INSTALLED
either one on its own                                            ->  INVALID
```

A map that renders from a file nobody verified is a map that can be quietly
wrong, and "quietly wrong" is the failure mode a self-hosted archive has no way
to notice.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trackvault.domain.maps.attribution import MapAttribution
from trackvault.domain.maps.bounds import MapBounds
from trackvault.domain.maps.identity import MapRegionId

SHA256_LENGTH = 64
MAX_ZOOM_LEVEL = 24


class MapPackageFormat(StrEnum):
    """The container an installed package is stored in."""

    MBTILES = "mbtiles"


class MapInstallState(StrEnum):
    """What a region's package amounts to right now.

    ``INVALID`` is a first-class answer rather than a variant of missing: a
    package whose file vanished and a package that was never installed call for
    different words in front of a reader, and only one of them is a surprise.
    """

    NOT_INSTALLED = "not_installed"
    INSTALLING = "installing"
    INSTALLED = "installed"
    UPDATING = "updating"
    INVALID = "invalid"
    FAILED = "failed"


class MapJobState(StrEnum):
    """Where an installation has got to.

    ``INTERRUPTED`` is what a job in flight becomes when the process stops. It
    is deliberately not ``FAILED``: nothing went wrong with it, and telling a
    reader their download failed when the container was restarted underneath it
    would be a small lie that costs trust.
    """

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Report whether nothing further will happen to this job."""
        return self in _TERMINAL_JOB_STATES


_TERMINAL_JOB_STATES = frozenset(
    {
        MapJobState.COMPLETED,
        MapJobState.FAILED,
        MapJobState.INTERRUPTED,
        MapJobState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class MapTileSchema:
    """Which vector tile vocabulary a package's tiles speak.

    A style is written against a schema, not against "vector tiles". A package
    that validates as a container and then carries different layer names renders
    as an empty map, which looks exactly like a broken installation -- so the
    schema is checked at install time and recorded, and a mismatch is a named
    refusal rather than a blank screen later.

    Attributes:
        name: The schema's identifier, lower case.
        version: The schema version the package declares.
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        """Refuse a schema that names nothing."""
        if not self.name.strip() or not self.version.strip():
            raise ValueError("a tile schema has a name and a version")


@dataclass(frozen=True, slots=True)
class MapPackage:
    """One installed regional map, and everything needed to explain it later.

    The field set answers four questions a reader or an operator will actually
    ask about a file that arrived from the internet and now sits in their data
    directory: where did this come from, which dataset is it, what may I do with
    it, and how old is it.

    Attributes:
        region_id: Which region this is, provider-scoped.
        region_name: The provider's display name for it.
        provider: The catalog slug the package came from.
        format: The container the bytes are in.
        tile_schema: The vector tile vocabulary the tiles speak.
        content_sha256: The digest of the managed file. Also the package's
            delivery identity, which is what makes a tile URL immutable.
        size_bytes: How large the managed file is.
        bounds: The rectangle the package holds data for.
        min_zoom: Lowest zoom level with tiles.
        max_zoom: Highest zoom level with tiles.
        attribution: What has to be shown while this package renders.
        dataset_version: The version string the package declares, if any.
        dataset_timestamp: When the provider last built it, if it said.
        downloaded_at: When this deployment fetched it.
        source_url: The public address it was fetched from. A URL, never a
            local path -- nothing in this model can name a place on disk.
    """

    region_id: MapRegionId
    region_name: str
    provider: str
    format: MapPackageFormat
    tile_schema: MapTileSchema
    content_sha256: str
    size_bytes: int
    bounds: MapBounds
    min_zoom: int
    max_zoom: int
    attribution: MapAttribution
    downloaded_at: datetime
    source_url: str
    dataset_version: str | None = None
    dataset_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        """Refuse a package that could not have been produced by an install."""
        if len(self.content_sha256) != SHA256_LENGTH or not all(
            character in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("a package content hash is 64 lower-case hexadecimal digits")
        if self.size_bytes <= 0:
            raise ValueError("an installed package holds bytes")
        if not 0 <= self.min_zoom <= self.max_zoom <= MAX_ZOOM_LEVEL:
            raise ValueError(f"package zoom levels must be ordered and within 0..{MAX_ZOOM_LEVEL}")
        if not self.region_name.strip():
            raise ValueError("a package states the region it covers")
        if self.provider != self.region_id.provider:
            raise ValueError("a package's provider and its region identity must agree")
        if self.downloaded_at.tzinfo is None:
            raise ValueError("a package download time is an unambiguous instant")
        if self.dataset_timestamp is not None and self.dataset_timestamp.tzinfo is None:
            raise ValueError("a package dataset time is an unambiguous instant")

    @property
    def delivery_id(self) -> str:
        """Return the identity this package is served under.

        The content hash. A tile URL built on it can be cached for a year
        without a stale-map problem, because a different map is a different URL
        rather than the same URL with different bytes behind it.
        """
        return self.content_sha256
