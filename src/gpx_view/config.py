"""Application settings.

This module is the single source of truth for runtime configuration. No other
module may read environment variables or invent its own configuration mechanism,
and no business rule belongs here.
"""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gpx_view.application.maps import DEFAULT_MAX_DOWNLOAD_BYTES

DATABASE_FILENAME = "gpx-view.sqlite3"
RAW_STORAGE_DIRECTORY = "raw"
MAP_STORAGE_DIRECTORY = "maps"

DEFAULT_TIMEZONE = "UTC"
"""Which zone month and year boundaries are drawn in, until one is configured.

UTC rather than the host's zone, deliberately. A container inherits whatever
zone its image or its host happens to have, so a default of "local time" would
make the same archive report different monthly totals on two machines, and
change them when a host is reconfigured. UTC is wrong for most people and
*visibly* wrong, which is what makes it get configured; a silently plausible
default is what does not.
"""


class Settings(BaseSettings):
    """Runtime configuration read from ``GPX_VIEW_*`` environment variables.

    Attributes:
        host: Interface the HTTP server binds to.
        port: TCP port the HTTP server listens on.
        data_dir: Directory that holds *all* persistent application data: the
            database, its journal files and the managed raw imports. Backing up
            this directory backs up the whole archive.
        import_dir: Directory the server scans for new files, for example a phone
            auto-sync target. Unset disables the feature. The directory is input
            only: nothing in it is written, renamed or deleted.
        import_max_bytes: Largest accepted input file.
        import_max_tracks: Most tracks one document may contain.
        import_max_segments_per_track: Most segments one track may contain.
        import_max_points: Most positions one document may contain in total.
        timezone: IANA zone that month and year boundaries are drawn in, for
            example ``Europe/Berlin``. It decides which month a late-evening
            activity counts towards, and nothing else -- instants are stored in
            UTC and stay there.
        web_dir: Directory holding the built browser application. When it holds
            no ``index.html`` the server answers the API only, which is what a
            development run and an API-only deployment both want.
        map_max_download_bytes: Largest map package this deployment will fetch.
            A ceiling rather than a preference: an unbounded stream to a file is
            a way to fill a disk with one request, and the default admits every
            country package the provider actually publishes.
        maps_enabled: Whether offline map packages may be installed at all.
            An operator who never wants an outbound request, not even a
            deliberate one, turns the whole capability off and the manager says
            so instead of failing at the provider.
    """

    model_config = SettingsConfigDict(
        env_prefix="GPX_VIEW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = Path("data")
    import_dir: Path | None = None

    import_max_bytes: int = 16 * 1024 * 1024
    import_max_tracks: int = 100
    import_max_segments_per_track: int = 1000
    import_max_points: int = 500_000

    timezone: str = DEFAULT_TIMEZONE

    web_dir: Path = Path("web/dist")

    map_max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    maps_enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        """Reject a zone this system cannot resolve, at start-up.

        Falling back to UTC would be the worst of both worlds: every monthly
        total would silently shift by an hour or two, and nothing would say why.
        A configuration error is loud, immediate and fixable.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                f"GPX_VIEW_TIMEZONE must be a known IANA time zone, got {value!r}"
            ) from error
        return value

    @property
    def aggregation_timezone(self) -> ZoneInfo:
        """Return the zone month and year boundaries are drawn in."""
        return ZoneInfo(self.timezone)

    @property
    def database_path(self) -> Path:
        """Return the SQLite file, always inside the data directory."""
        return self.data_dir / DATABASE_FILENAME

    @property
    def raw_storage_dir(self) -> Path:
        """Return the managed raw import directory, inside the data directory."""
        return self.data_dir / RAW_STORAGE_DIRECTORY

    @property
    def map_storage_dir(self) -> Path:
        """Return the managed map package directory, inside the data directory.

        Inside the data directory on purpose. Regional maps are large and
        replaceable, but they are still deployment state, and a second
        configurable root would be a second thing to mount, back up and get
        wrong. What a backup may skip is documented rather than made
        structural.
        """
        return self.data_dir / MAP_STORAGE_DIRECTORY


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Returns:
        The cached settings object. Tests that need different values should call
        ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()
