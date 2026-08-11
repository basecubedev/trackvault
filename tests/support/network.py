"""The suite's network boundary: this machine, and nowhere else.

A test that reaches a real host is not a test, it is a probe of somebody else's
uptime. It passes on a laptop with a connection, fails in a locked-down CI
runner, and -- worst of all -- gives a *different answer* in each place, which
is how a contract comes to be written around whichever answer the author
happened to see.

So every outbound connection is refused unless it stays on this machine.
Loopback stays open deliberately: the transfer tests run a real HTTP server on
``127.0.0.1`` and open a real socket at it, which is the only way to find out
whether the streaming, hashing, ceiling and redirect rules actually hold.

The refusal is a `RuntimeError` and **not** an `OSError`, which matters more
than it looks. Adapters translate `OSError` into "the provider is unavailable",
so a network guard that raised one would be swallowed by exactly the code it is
watching, and an accidental outbound request would surface as a plausible-looking
`503` instead of a failed test.
"""

import ipaddress
import socket

import pytest

_Resolved = list[
    tuple[
        socket.AddressFamily,
        socket.SocketKind,
        int,
        str,
        tuple[str, int] | tuple[str, int, int, int],
    ]
]

_LOCAL_NAMES = frozenset({"", "localhost", "localhost.localdomain", "ip6-localhost"})
"""Names that never leave the machine, whatever a resolver would say about them."""


class ExternalNetworkError(RuntimeError):
    """A test tried to reach a host outside this machine."""


def forbid_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every connection and every lookup that leaves this machine.

    Both halves are guarded, because either one alone leaves a gap. Blocking
    only the connection lets a test spend a DNS timeout before failing; blocking
    only the lookup lets a test that hard-codes an address through.
    """
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> None:
        _require_local_address(address)
        connect(self, address)  # type: ignore[arg-type]

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        _require_local_address(address)
        return connect_ex(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *arguments: int, **keywords: int
    ) -> _Resolved:
        _require_local_host(host)
        return getaddrinfo(host, port, *arguments, **keywords)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


def _require_local_address(address: object) -> None:
    """Refuse a socket address that names a host somewhere else.

    A non-tuple address belongs to a Unix domain socket, which names a path on
    this machine and can therefore never leave it.
    """
    if isinstance(address, tuple) and address:
        _require_local_host(address[0])


def _require_local_host(host: object) -> None:
    """Refuse a host that is not this machine.

    Raises:
        ExternalNetworkError: For anything that is not a loopback address or a
            name for one. A wildcard (``None``) is a bind, not a destination.
    """
    if host is None:
        return
    name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
    if isinstance(name, str):
        if name in _LOCAL_NAMES:
            return
        try:
            if ipaddress.ip_address(name.partition("%")[0]).is_loopback:
                return
        except ValueError:
            pass
    raise ExternalNetworkError(f"the test suite tried to reach {name!r}; it runs without a network")
