"""Application settings.

This module is the single source of truth for runtime configuration. No other
module may read environment variables or invent its own configuration mechanism,
and no business rule belongs here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read from ``GPX_VIEW_*`` environment variables.

    Attributes:
        host: Interface the HTTP server binds to.
        port: TCP port the HTTP server listens on.
        data_dir: Directory for local application data. The path is reserved for
            later persistence work; nothing is written there yet.
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Returns:
        The cached settings object. Tests that need different values should call
        ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()
