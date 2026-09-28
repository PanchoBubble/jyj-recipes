"""Outbound fetches to hosts we do not control, guarded against SSRF.

A URL is only fetched over https on the default port, without credentials, and only when every
address its host resolves to is public. The check runs twice: ``check_url`` before each request
(including every redirect hop) and again inside the transport's network backend, which resolves
the host once per connection and connects to that validated address. The second check is what
defeats DNS rebinding: the address that passed is the address the socket uses, while TLS still
verifies the certificate against the hostname.
"""

import ipaddress
import socket
import ssl
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

import httpcore2
import httpx2
from httpcore2._backends.base import SOCKET_OPTION, NetworkBackend, NetworkStream
from httpcore2._backends.sync import SyncBackend

Resolver = Callable[[str, int], list[str]]

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Not all of these are covered by ``is_global`` on every Python version.
_BLOCKED_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
_NAT64 = (ipaddress.IPv6Network("64:ff9b::/96"), ipaddress.IPv6Network("64:ff9b:1::/48"))


class UnsafeDestination(Exception):
    """The URL or the address it resolves to is not allowed for an outbound fetch."""


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise UnsafeDestination("the host could not be resolved") from exc
    return [str(info[4][0]) for info in infos]


def is_public_ip(address: str | IPAddress) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0] if isinstance(address, str) else address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and any(ip in net for net in _NAT64):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return is_public_ip(embedded)
        if ip.teredo:
            return False
        return ip.is_global and not ip.is_multicast
    if any(ip in net for net in _BLOCKED_V4):
        return False
    return ip.is_global and not ip.is_multicast


def resolve_public(host: str, port: int, resolver: Resolver) -> list[str]:
    """Resolve ``host``; every address must be public, else nothing is returned."""
    host = host.strip("[]")
    addresses = resolver(host, port)
    if not addresses:
        raise UnsafeDestination("the host could not be resolved")
    if not all(is_public_ip(a) for a in addresses):
        raise UnsafeDestination("the host resolves to a private address")
    return addresses


def check_url(url: str, resolver: Resolver = system_resolver) -> str:
    """Return the URL if it is https to a public host on port 443; raise otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeDestination("the URL is malformed") from exc
    if parts.scheme != "https":
        raise UnsafeDestination("only https URLs are allowed")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise UnsafeDestination("the URL has no host or carries credentials")
    if port not in (None, 443):
        raise UnsafeDestination("only the default https port is allowed")
    resolve_public(parts.hostname, 443, resolver)
    return url


def is_safe_url(url: object, resolver: Resolver = system_resolver) -> bool:
    if not isinstance(url, str):
        return False
    try:
        check_url(url, resolver)
    except UnsafeDestination:
        return False
    return True


class GuardedBackend(NetworkBackend):
    """Resolves once per connection and connects only to a validated public address."""

    def __init__(
        self, resolver: Resolver = system_resolver, inner: NetworkBackend | None = None
    ) -> None:
        self.resolver = resolver
        self.inner = inner or SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> NetworkStream:
        if port != 443:
            raise UnsafeDestination("only the default https port is allowed")
        address = resolve_public(host, port, self.resolver)[0]
        return self.inner.connect_tcp(address, port, timeout, local_address, socket_options)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> NetworkStream:
        raise UnsafeDestination("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


class GuardedTransport(httpx2.HTTPTransport):
    """An httpx transport whose connections go through ``GuardedBackend``; never uses proxies."""

    def __init__(
        self, resolver: Resolver = system_resolver, inner: NetworkBackend | None = None
    ) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore2.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=GuardedBackend(resolver, inner),
        )

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme != "https":
            raise UnsafeDestination("only https URLs are allowed")
        return super().handle_request(request)


def guarded_transport(resolver: Resolver = system_resolver) -> httpx2.BaseTransport:
    return GuardedTransport(resolver)
