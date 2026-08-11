"""SQLite persistence.

This package is the only place that imports ``sqlite3``. Domain and application
speak to it through the ``TrackRepository`` port.
"""

from gpx_view.infrastructure.database.migrations import SCHEMA_VERSION
from gpx_view.infrastructure.database.store import SqliteTrackStore

__all__ = ["SCHEMA_VERSION", "SqliteTrackStore"]
