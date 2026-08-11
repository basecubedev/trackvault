"""No test reaches a host outside this machine.

The rule this file makes enforceable: *a contract test states what TrackVault
does, never what somebody else's server was doing that afternoon.* A suite that
can reach the internet has tests whose answer changes with the weather, and the
failure is not that they break -- it is that they pass differently. A refusal
that only appears when the network is up gets written down as the contract, and
the deployment nobody could reach it from never told anyone.

The guard itself lives in ``support.network`` and is applied to every test by an
autouse fixture. What is asserted here is that it works: that it refuses, that
it refuses lookups as well as connections, and that it leaves loopback alone --
because the transfer tests run a real server there and a guard that broke them
would simply be switched off.
"""

import socket
import threading
from collections.abc import Iterator

import pytest

from support.network import ExternalNetworkError

pytestmark = pytest.mark.contract

EXTERNAL_ADDRESS = ("203.0.113.7", 80)
"""An address in the range RFC 5737 reserves for documentation.

Chosen so that a failure of the guard cannot become a *successful* connection to
anything real, and so no lookup is needed to attempt one.
"""

EXTERNAL_HOST = "download.geofabrik.de"
"""The provider this archive would talk to, named here and reached nowhere."""


@pytest.fixture
def loopback_server() -> Iterator[int]:
    """Run a socket on loopback that accepts one connection, and return its port."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    accepted = threading.Thread(target=lambda: listener.accept()[0].close(), daemon=True)
    accepted.start()
    try:
        yield port
    finally:
        accepted.join(timeout=5)
        listener.close()


def test_a_test_cannot_open_a_connection_to_another_machine() -> None:
    """The blunt statement, and the one every other test in the suite rests on."""
    with pytest.raises(ExternalNetworkError), socket.socket() as opened:
        opened.connect(EXTERNAL_ADDRESS)


def test_a_test_cannot_look_up_a_host_either() -> None:
    """Blocking only the connection would spend a DNS timeout before failing.

    A test that takes ten seconds to refuse is a test somebody eventually marks
    as slow rather than as wrong.
    """
    with pytest.raises(ExternalNetworkError):
        socket.getaddrinfo(EXTERNAL_HOST, 443)


def test_the_map_provider_is_not_reachable_from_a_test() -> None:
    """Named explicitly, because it is the one host this archive would call."""
    with pytest.raises(ExternalNetworkError), socket.socket() as opened:
        opened.connect((EXTERNAL_HOST, 443))


def test_the_refusal_is_not_something_an_adapter_would_swallow() -> None:
    """An `OSError` here would surface as "the provider is unavailable".

    Every transfer adapter translates `OSError` into a named provider failure,
    so a guard raising one would turn an accidental outbound request into a
    plausible `503` and a test that passes for the wrong reason.
    """
    assert not issubclass(ExternalNetworkError, OSError)


def test_loopback_is_still_open(loopback_server: int) -> None:
    """The transfer tests open a real socket at a real server on this machine.

    That is where the streaming, hashing, ceiling and redirect rules are proved,
    and a guard that broke it would be a guard somebody switches off.
    """
    with socket.socket() as opened:
        opened.connect(("127.0.0.1", loopback_server))

    assert socket.getaddrinfo("127.0.0.1", loopback_server), "loopback still resolves"
