import httpcore2
import httpx2
import pytest

from jyj.services import safe_fetch
from jyj.services.safe_fetch import (
    GuardedBackend,
    GuardedTransport,
    UnsafeDestination,
    check_url,
    is_public_ip,
    is_safe_url,
)

PUBLIC = "151.101.1.1"


def resolver(table: dict[str, list[str]]) -> safe_fetch.Resolver:
    def resolve(host: str, port: int) -> list[str]:
        return table.get(host, [PUBLIC])

    return resolve


class RecordingBackend(httpcore2.NetworkBackend):
    """Stands in for the socket layer: records where a connection would go, then fails."""

    def __init__(self) -> None:
        self.connects: list[tuple[str, int]] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        raise httpcore2.ConnectError("test backend does not connect")


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.10",
        "127.0.0.1",
        "127.8.8.8",
        "0.0.0.0",  # noqa: S104
        "169.254.169.254",
        "100.64.0.1",
        "100.127.255.254",
        "192.0.0.8",
        "198.18.0.1",
        "224.0.0.1",
        "239.255.255.250",
        "255.255.255.255",
        "::",
        "::1",
        "fe80::1",
        "fe80::1%en0",
        "fc00::1",
        "fd12:3456::1",
        "ff02::1",
        "ff0e::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a00:1",
        "2002:a00:1::1",
        "2001:db8::1",
        "not-an-ip",
        "",
    ],
)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert not is_public_ip(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", PUBLIC, "2606:4700:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808"]
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_ip(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://live.staticflickr.com/a.jpg",
        "ftp://live.staticflickr.com/a.jpg",
        "file:///etc/passwd",
        "https://user:pw@live.staticflickr.com/a.jpg",
        "https://user@live.staticflickr.com/a.jpg",
        "https://live.staticflickr.com:8443/a.jpg",
        "https://live.staticflickr.com:80/a.jpg",
        "https:///a.jpg",
        "https://[::1/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://[::1]/a.jpg",
        "https://[::ffff:127.0.0.1]/a.jpg",
        "https://169.254.169.254/latest/meta-data/",
        "https://internal.example/a.jpg",
        "https://mixed.example/a.jpg",
    ],
)
def test_check_url_rejects(url: str) -> None:
    resolve = resolver(
        {
            "127.0.0.1": ["127.0.0.1"],
            "::1": ["::1"],
            "::ffff:127.0.0.1": ["::ffff:127.0.0.1"],
            "169.254.169.254": ["169.254.169.254"],
            "internal.example": ["10.0.0.5"],
            "mixed.example": [PUBLIC, "192.168.0.2"],
        }
    )
    with pytest.raises(UnsafeDestination):
        check_url(url, resolve)
    assert not is_safe_url(url, resolve)


def test_check_url_accepts_public_https() -> None:
    url = "https://live.staticflickr.com/65535/1_b.jpg"
    assert check_url(url, resolver({})) == url
    assert is_safe_url("https://live.staticflickr.com:443/a.jpg", resolver({}))


def test_check_url_rejects_unresolvable_hosts() -> None:
    def fail(host: str, port: int) -> list[str]:
        raise UnsafeDestination("the host could not be resolved")

    assert not is_safe_url("https://nowhere.invalid/a.jpg", fail)
    assert not is_safe_url("https://empty.example/a.jpg", lambda h, p: [])
    assert not is_safe_url(None)


def test_backend_connects_to_the_validated_address_not_the_hostname() -> None:
    inner = RecordingBackend()
    backend = GuardedBackend(resolver({"cdn.example": [PUBLIC, "8.8.8.8"]}), inner)
    with pytest.raises(httpcore2.ConnectError):
        backend.connect_tcp("cdn.example", 443)
    assert inner.connects == [(PUBLIC, 443)]


def test_backend_blocks_dns_rebinding() -> None:
    answers = iter([[PUBLIC], ["127.0.0.1"]])

    def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    inner = RecordingBackend()
    backend = GuardedBackend(rebinding, inner)
    check_url("https://rebind.example/a.jpg", backend.resolver)
    with pytest.raises(UnsafeDestination):
        backend.connect_tcp("rebind.example", 443)
    assert inner.connects == []


def test_backend_blocks_other_ports_and_unix_sockets() -> None:
    inner = RecordingBackend()
    backend = GuardedBackend(resolver({}), inner)
    with pytest.raises(UnsafeDestination):
        backend.connect_tcp("live.staticflickr.com", 80)
    with pytest.raises(UnsafeDestination):
        backend.connect_unix_socket("/var/run/docker.sock")
    assert inner.connects == []


def test_transport_rejects_plain_http_before_connecting() -> None:
    inner = RecordingBackend()
    with (
        httpx2.Client(transport=GuardedTransport(resolver({}), inner)) as client,
        pytest.raises(UnsafeDestination),
    ):
        client.get("http://live.staticflickr.com/a.jpg")
    assert inner.connects == []


def test_transport_rejects_private_hosts_at_connect_time() -> None:
    inner = RecordingBackend()
    resolve = resolver({"internal.example": ["10.0.0.5"]})
    with (
        httpx2.Client(transport=GuardedTransport(resolve, inner)) as client,
        pytest.raises(UnsafeDestination),
    ):
        client.get("https://internal.example/a.jpg")
    assert inner.connects == []


def test_transport_pins_the_resolved_address() -> None:
    inner = RecordingBackend()
    with (
        httpx2.Client(transport=GuardedTransport(resolver({}), inner)) as client,
        pytest.raises(httpx2.ConnectError),
    ):
        client.get("https://live.staticflickr.com/a.jpg")
    assert inner.connects == [(PUBLIC, 443)]


def test_transport_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    inner = RecordingBackend()
    with (
        httpx2.Client(transport=GuardedTransport(resolver({}), inner)) as client,
        pytest.raises(httpx2.ConnectError),
    ):
        client.get("https://live.staticflickr.com/a.jpg")
    assert inner.connects == [(PUBLIC, 443)]
