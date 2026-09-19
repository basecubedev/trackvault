"""Shared test fixtures.

Every fixture here keeps the suite off the real data directory: applications are
composed against ``tmp_path``, so no test writes into ``./data``, ``/data`` or a
developer's home directory.

It also keeps the suite off the *built frontend*. Whether ``web/dist`` happens
to exist depends on whether somebody ran a build, and a test that behaves
differently on two machines for that reason is a flaky test waiting for a bad
afternoon. The browser assets are pointed somewhere that does not exist by
default; the tests that are about serving them say so explicitly.

And it keeps the suite off the *network*, for the same reason and more sharply:
a test whose answer depends on whether a provider happens to be reachable has
two answers, and the one that gets written down is whichever the author saw. See
``support.network``.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support.network import forbid_external_network
from trackvault.config import Settings
from trackvault.main import create_app


@pytest.fixture(autouse=True)
def _without_browser_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Compose against an archive that ships no built browser application."""
    monkeypatch.setenv("TRACKVAULT_WEB_DIR", str(tmp_path / "no-such-build"))


@pytest.fixture(autouse=True)
def _without_automatic_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose applications that do not read an import directory on their own.

    A developer's ``.env`` may point ``TRACKVAULT_IMPORT_DIR`` at a folder of
    real recordings, and an application an unrelated test composes would start
    reading it in the background. The tests about the automatic import switch it
    on explicitly.
    """
    monkeypatch.setenv("TRACKVAULT_IMPORT_SCAN_ENABLED", "false")


@pytest.fixture(autouse=True)
def _without_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every connection that would leave this machine.

    Autouse and suite-wide rather than opt-in per test. A test that needs the
    guard is exactly the test whose author did not know it needed it.
    """
    forbid_external_network(monkeypatch)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a test client backed by a freshly composed application."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
