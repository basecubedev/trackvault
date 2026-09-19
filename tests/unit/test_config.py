"""Unit tests for the central settings module."""

from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trackvault.config import Settings, get_settings


@pytest.mark.unit
def test_settings_have_local_defaults() -> None:
    """Without environment overrides the application binds locally on port 8080."""
    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8080
    assert settings.data_dir == Path("data")


@pytest.mark.unit
def test_settings_read_the_trackvault_environment_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Configuration comes from ``TRACKVAULT_*`` environment variables."""
    monkeypatch.setenv("TRACKVAULT_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("TRACKVAULT_PORT", "9000")
    monkeypatch.setenv("TRACKVAULT_DATA_DIR", str(tmp_path))

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
    monkeypatch.setenv("TRACKVAULT_TIMEZONE", "Europe/Berlin")

    settings = Settings()

    assert settings.aggregation_timezone == ZoneInfo("Europe/Berlin")


@pytest.mark.unit
@pytest.mark.parametrize("value", ["Europe/Nowhere", "CET+1", "", "utc/utc"])
def test_an_unknown_timezone_fails_at_startup(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Falling back to UTC would shift every monthly total and say nothing.

    A configuration error is loud, immediate and fixable; a silent fallback is
    a wrong number nobody has a reason to question.
    """
    monkeypatch.setenv("TRACKVAULT_TIMEZONE", value)

    with pytest.raises(ValidationError, match="TRACKVAULT_TIMEZONE"):
        Settings()


@pytest.mark.unit
def test_the_automatic_import_is_on_and_scans_every_quarter_hour_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuring an import directory is enough to have it read.

    A quarter of an hour is often enough that a ride synced after getting home
    is there by the time somebody looks, and rare enough that a folder of old
    files costs nothing noticeable. A file has to have been left alone for five
    minutes first: a recorder that writes a point a minute into a synced folder
    must not have its first half imported as a track of its own.
    """
    monkeypatch.delenv("TRACKVAULT_IMPORT_SCAN_ENABLED")

    settings = Settings()

    assert settings.import_scan_enabled is True
    assert settings.import_scan_interval == timedelta(minutes=15)
    assert settings.import_settle_time == timedelta(minutes=5)


@pytest.mark.unit
def test_the_automatic_import_is_configured_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch, the interval and the settle time are ordinary settings."""
    monkeypatch.setenv("TRACKVAULT_IMPORT_SCAN_ENABLED", "false")
    monkeypatch.setenv("TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("TRACKVAULT_IMPORT_SETTLE_MINUTES", "20")

    settings = Settings()

    assert settings.import_scan_enabled is False
    assert settings.import_scan_interval == timedelta(minutes=5)
    assert settings.import_settle_time == timedelta(minutes=20)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0", "-1", "10081", "10000000000"])
def test_a_scan_interval_outside_a_minute_to_a_week_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Zero would read the folder as fast as it can; centuries would never read it.

    An interval past what a timer can wait for would not wait at all, so it is
    refused where it is configured. Scanning less than once a week is what
    switching the automatic import off and running ``trackvault scan`` is for.
    """
    monkeypatch.setenv("TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES", value)

    with pytest.raises(ValidationError, match="import_scan_interval_minutes"):
        Settings()


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0", "1441"])
def test_a_settle_time_outside_a_minute_to_a_day_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Without a settle time a half-copied file is a candidate like any other."""
    monkeypatch.setenv("TRACKVAULT_IMPORT_SETTLE_MINUTES", value)

    with pytest.raises(ValidationError, match="import_settle_minutes"):
        Settings()
