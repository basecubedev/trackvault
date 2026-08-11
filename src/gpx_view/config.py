"""Application settings.

This module is the single source of truth for runtime configuration. No other
module may read environment variables or invent its own configuration mechanism,
and no business rule belongs here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_FILENAME = "gpx-view.sqlite3"
RAW_STORAGE_DIRECTORY = "raw"


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

    @property
    def database_path(self) -> Path:
        """Return the SQLite file, always inside the data directory."""
        return self.data_dir / DATABASE_FILENAME

    @property
    def raw_storage_dir(self) -> Path:
        """Return the managed raw import directory, inside the data directory."""
        return self.data_dir / RAW_STORAGE_DIRECTORY


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Returns:
        The cached settings object. Tests that need different values should call
        ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()
