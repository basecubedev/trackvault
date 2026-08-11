"""Unit tests for the central settings module."""

from pathlib import Path

import pytest

from gpx_view.config import Settings, get_settings


@pytest.mark.unit
def test_settings_have_local_defaults() -> None:
    """Without environment overrides the application binds locally on port 8080."""
    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8080
    assert settings.data_dir == Path("data")


@pytest.mark.unit
def test_settings_read_the_gpx_view_environment_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Configuration comes from ``GPX_VIEW_*`` environment variables."""
    monkeypatch.setenv("GPX_VIEW_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("GPX_VIEW_PORT", "9000")
    monkeypatch.setenv("GPX_VIEW_DATA_DIR", str(tmp_path))

    settings = Settings()

    assert settings.host == "0.0.0.0"  # noqa: S104
    assert settings.port == 9000
    assert settings.data_dir == tmp_path


@pytest.mark.unit
def test_get_settings_returns_a_cached_instance() -> None:
    """Settings are resolved once per process."""
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
