"""Unit tests for the central settings module."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

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


@pytest.mark.unit
def test_the_aggregation_timezone_defaults_to_utc() -> None:
    """A container inherits whatever zone its host has, so nothing is inherited.

    Defaulting to local time would make the same archive report different
    monthly totals on two machines. UTC is wrong for most people and *visibly*
    wrong, which is what gets it configured.
    """
    settings = Settings()

    assert settings.timezone == "UTC"
    assert settings.aggregation_timezone == ZoneInfo("UTC")


@pytest.mark.unit
def test_an_iana_timezone_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The zone month boundaries are drawn in is explicit configuration."""
    monkeypatch.setenv("GPX_VIEW_TIMEZONE", "Europe/Berlin")

    settings = Settings()

    assert settings.aggregation_timezone == ZoneInfo("Europe/Berlin")


@pytest.mark.unit
@pytest.mark.parametrize("value", ["Europe/Nowhere", "CET+1", "", "utc/utc"])
def test_an_unknown_timezone_fails_at_startup(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Falling back to UTC would shift every monthly total and say nothing.

    A configuration error is loud, immediate and fixable; a silent fallback is
    a wrong number nobody has a reason to question.
    """
    monkeypatch.setenv("GPX_VIEW_TIMEZONE", value)

    with pytest.raises(ValidationError, match="GPX_VIEW_TIMEZONE"):
        Settings()
