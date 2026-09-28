"""Stock photo search and importing a chosen result as a recipe photo.

Two providers sit behind one ``Provider`` protocol. Openverse needs no key and is the default;
Pexels is used when ``PEXELS_API_KEY`` is set (``PHOTO_SEARCH_PROVIDER`` can force either).
Imports never trust a URL from the client: the photo is looked up again by id at its provider
and only the URL that answer names is downloaded, streamed under the photo size cap, before the
normal photo pipeline re-encodes it. Openverse images live on arbitrary third-party hosts, so
those downloads go through ``safe_fetch`` (https only, public addresses only, re-checked on
every redirect and at connect time). Openverse thumbnails are proxied through the backend so
the browser only ever talks to us. Rate limits and caches are in-process, which is valid
because the backend runs a single worker.
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx2
from sqlalchemy.orm import Session

from jyj.config import get_settings
from jyj.models import Recipe
from jyj.services import photos, safe_fetch
from jyj.services.errors import InvalidError, NotFoundError, ServiceError
from jyj.services.rate_limit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

ProviderName = Literal["pexels", "openverse"]
PROVIDERS: tuple[ProviderName, ...] = ("pexels", "openverse")

QUERY_MAX = 100
PAGE_MAX = 50
PER_PAGE_DEFAULT = 24
ALT_MAX = 200
NAME_MAX = 200
CACHE_TTL_SECONDS = 600.0
CACHE_MAX_ENTRIES = 200
MAX_REDIRECTS = 3
USER_AGENT = "jyj-recipes/1.0 (household recipe app; https://github.com/PanchoBubble/jyj-recipes)"

PEXELS_API = "https://api.pexels.com/v1"
PEXELS_IMAGE_HOST = "images.pexels.com"
PEXELS_LINK_HOSTS = frozenset({"www.pexels.com", "pexels.com"})
PEXELS_PER_PAGE_MAX = 40
PEXELS_RENDITIONS = ("large2x", "original")

OPENVERSE_API = "https://api.openverse.org/v1"
OPENVERSE_HOST = "api.openverse.org"
# Anonymous clients may ask for at most 20 per page and 20 API calls a minute per IP.
OPENVERSE_PER_PAGE_MAX = 20
OPENVERSE_CALLS_PER_MINUTE = 20
# No-derivatives licenses are left out: the photo pipeline crops and re-encodes.
OPENVERSE_LICENSES = "by,by-sa,by-nc,by-nc-sa,cc0,pdm"
LICENSE_HOST = "creativecommons.org"
THUMB_MAX_BYTES = 512 * 1024
THUMB_CACHE_MAX_BYTES = 32 * 1024 * 1024
THUMB_TYPES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


class PhotoSearchUnavailable(ServiceError):
    status = 503


class PhotoSearchFailed(ServiceError):
    status = 502


class PhotoSearchRateLimited(ServiceError):
    status = 429


PhotoId = int | str


@dataclass(frozen=True, slots=True)
class PhotoResult:
    provider: ProviderName
    id: PhotoId
    alt: str
    width: int
    height: int
    photographer: str
    photographer_url: str | None
    page_url: str | None
    thumb_url: str
    preview_url: str
    title: str | None = None
    license: str | None = None
    license_url: str | None = None
    # Server-side only: where an import downloads from, best first.
    download_urls: tuple[str, ...] = field(default=(), compare=False)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        del out["download_urls"]
        return out


@dataclass(frozen=True, slots=True)
class SearchPage:
    provider: ProviderName
    query: str
    page: int
    results: list[PhotoResult]
    has_more: bool


def _https_link(url: object, hosts: frozenset[str] | None = None) -> str | None:
    if not isinstance(url, str) or len(url) > 2000:
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    if hosts is not None and parts.netloc not in hosts:
        return None
    return url


def _text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def credit(photo: PhotoResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "provider": photo.provider,
        "photographer": photo.photographer,
        "photographer_url": photo.photographer_url,
        "page_url": photo.page_url,
    }
    if photo.provider == "openverse":
        out.update(title=photo.title, license=photo.license, license_url=photo.license_url)
    return out


# --- HTTP plumbing shared by providers ---------------------------------------------------


class _Http:
    def __init__(self, timeout: float, transport: httpx2.BaseTransport | None) -> None:
        self.timeout = timeout
        self.transport = transport

    def client(self, transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
        return httpx2.Client(
            timeout=self.timeout,
            follow_redirects=False,
            transport=transport or self.transport,
            headers={"User-Agent": USER_AGENT},
        )

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        missing_ok: bool = True,
    ) -> dict[str, Any]:
        try:
            with self.client() as client:
                response = client.get(url, params=params, headers=headers)
        except httpx2.HTTPError as exc:
            logger.warning("photo search request failed: %s", type(exc).__name__)
            raise PhotoSearchFailed("photo search is unreachable; try again later") from None
        if response.status_code == 404 and not missing_ok:
            raise NotFoundError("that photo is no longer available")
        if response.status_code == 429:
            logger.warning("photo search is throttled upstream")
            raise PhotoSearchRateLimited(
                "photo search is busy; try again in a minute", retry_after=60
            )
        if response.status_code != 200:
            logger.warning("photo search answered %s", response.status_code)
            raise PhotoSearchFailed("photo search failed; try again later")
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        return body

    def fetch(
        self,
        url: str,
        *,
        allowed: Callable[[str], bool],
        max_bytes: int,
        transport: httpx2.BaseTransport | None = None,
        error: str = "the photo could not be downloaded",
    ) -> tuple[bytes, str]:
        """GET ``url`` following at most ``MAX_REDIRECTS`` hops, each one checked by ``allowed``.

        Returns the body and its declared content type; the body is capped while streaming.
        """
        try:
            with self.client(transport) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    if not allowed(url):
                        raise PhotoSearchFailed(error)
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            url = str(response.url.join(location))
                            continue
                        if response.status_code != 200:
                            logger.warning("photo download answered %s", response.status_code)
                            raise PhotoSearchFailed(error)
                        declared = response.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > max_bytes:
                            raise photos.PhotoTooLarge(_too_large(max_bytes))
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data += chunk
                            if len(data) > max_bytes:
                                raise photos.PhotoTooLarge(_too_large(max_bytes))
                        return bytes(data), response.headers.get("content-type", "")
                raise PhotoSearchFailed(error)
        except safe_fetch.UnsafeDestination:
            logger.warning("photo download blocked by the outbound guard")
            raise PhotoSearchFailed(error) from None
        except httpx2.HTTPError as exc:
            logger.warning("photo download failed: %s", type(exc).__name__)
            raise PhotoSearchFailed(error) from None


def _too_large(limit: int) -> str:
    if limit < 1024 * 1024:
        return f"photo must be at most {limit // 1024} KiB"
    return f"photo must be at most {limit // (1024 * 1024)} MiB"


# --- providers ---------------------------------------------------------------------------


class Provider(Protocol):
    name: ProviderName
    per_page_max: int

    @property
    def available(self) -> bool: ...

    def parse_id(self, raw: object) -> PhotoId: ...

    def search(self, query: str, page: int, per_page: int) -> SearchPage: ...

    def get(self, photo_id: PhotoId) -> PhotoResult: ...

    def allowed_download(self, url: str) -> bool: ...

    def download_transport(self) -> httpx2.BaseTransport | None: ...


def is_pexels_image_url(url: object) -> bool:
    """Only https URLs on the Pexels image CDN, with no credentials or unusual port."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == PEXELS_IMAGE_HOST
        and parts.netloc == PEXELS_IMAGE_HOST
        and port is None
    )


def parse_pexels_photo(raw: object) -> PhotoResult | None:
    """Map one Pexels photo object; anything malformed or off-CDN is dropped."""
    if not isinstance(raw, dict):
        return None
    src = raw.get("src")
    photo_id = raw.get("id")
    if not isinstance(src, dict) or not isinstance(photo_id, int) or photo_id < 1:
        return None
    thumb, preview = src.get("medium"), src.get("large")
    if not (is_pexels_image_url(thumb) and is_pexels_image_url(preview)):
        return None
    download = next((src[k] for k in PEXELS_RENDITIONS if is_pexels_image_url(src.get(k))), None)
    return PhotoResult(
        provider="pexels",
        id=photo_id,
        alt=_text(raw.get("alt"), ALT_MAX),
        width=_int(raw.get("width")),
        height=_int(raw.get("height")),
        photographer=_text(raw.get("photographer"), NAME_MAX) or "Unknown photographer",
        photographer_url=_https_link(raw.get("photographer_url"), PEXELS_LINK_HOSTS),
        page_url=_https_link(raw.get("url"), PEXELS_LINK_HOSTS),
        thumb_url=thumb,
        preview_url=preview,
        download_urls=(download,) if download else (),
    )


class PexelsProvider:
    name: ProviderName = "pexels"
    per_page_max = PEXELS_PER_PAGE_MAX

    def __init__(self, http: _Http, api_key: str | None) -> None:
        self.http = http
        self._api_key = api_key.strip() if api_key else None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def parse_id(self, raw: object) -> int:
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= 2**53:
            raise InvalidError("that isn't a Pexels photo id")
        return raw

    def _get(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        headers = {"Authorization": self._api_key or ""}
        return self.http.get_json(PEXELS_API + path, params, headers, **kw)

    def search(self, query: str, page: int, per_page: int) -> SearchPage:
        body = self._get(
            "/search",
            {"query": query, "page": page, "per_page": per_page, "orientation": "landscape"},
        )
        raw_photos = body.get("photos")
        if not isinstance(raw_photos, list):
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        results = [p for p in map(parse_pexels_photo, raw_photos) if p is not None]
        return SearchPage(self.name, query, page, results, has_more=bool(body.get("next_page")))

    def get(self, photo_id: PhotoId) -> PhotoResult:
        body = self._get(f"/photos/{int(photo_id)}", missing_ok=False)
        photo = parse_pexels_photo(body)
        if photo is None or photo.id != photo_id:
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        return photo

    def allowed_download(self, url: str) -> bool:
        return is_pexels_image_url(url)

    def download_transport(self) -> httpx2.BaseTransport | None:
        return None


def license_label(code: object, version: object) -> str | None:
    if not isinstance(code, str) or not code:
        return None
    code = code.strip().lower()
    version = version.strip() if isinstance(version, str) else ""
    if code == "pdm":
        return "Public Domain Mark" + (f" {version}" if version else "")
    if code == "cc0":
        return "CC0" + (f" {version}" if version else "")
    if not all(part in {"by", "sa", "nc", "nd"} for part in code.split("-")):
        return None
    return f"CC {code.upper()}" + (f" {version}" if version else "")


def _openverse_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        return None
    return raw if str(parsed) == raw else None


def thumb_proxy_url(provider: ProviderName, photo_id: PhotoId) -> str:
    return f"/api/v1/images/thumb?provider={provider}&id={photo_id}"


def parse_openverse_image(raw: object) -> PhotoResult | None:
    """Map one Openverse image; malformed, mature or unlicensed entries are dropped."""
    if not isinstance(raw, dict) or raw.get("mature") is True:
        return None
    photo_id = _openverse_id(raw.get("id"))
    license = license_label(raw.get("license"), raw.get("license_version"))
    if photo_id is None or license is None:
        return None
    title = _text(raw.get("title"), ALT_MAX)
    thumb = thumb_proxy_url("openverse", photo_id)
    downloads = [u for u in (raw.get("url"),) if _https_link(u)]
    downloads.append(f"{OPENVERSE_API}/images/{photo_id}/thumb/?full_size=true")
    return PhotoResult(
        provider="openverse",
        id=photo_id,
        alt=title,
        width=_int(raw.get("width")),
        height=_int(raw.get("height")),
        photographer=_text(raw.get("creator"), NAME_MAX) or "Unknown creator",
        photographer_url=_https_link(raw.get("creator_url")),
        page_url=_https_link(raw.get("foreign_landing_url")),
        thumb_url=thumb,
        preview_url=thumb,
        title=title or None,
        license=license,
        license_url=_https_link(raw.get("license_url"), frozenset({LICENSE_HOST})),
        download_urls=tuple(downloads),
    )


class OpenverseProvider:
    name: ProviderName = "openverse"
    per_page_max = OPENVERSE_PER_PAGE_MAX

    def __init__(
        self,
        http: _Http,
        *,
        resolver: safe_fetch.Resolver = safe_fetch.system_resolver,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.http = http
        self.resolver = resolver
        self.limiter = SlidingWindowLimiter(
            limit=OPENVERSE_CALLS_PER_MINUTE, window=60.0, clock=clock
        )

    @property
    def available(self) -> bool:
        return True

    def parse_id(self, raw: object) -> str:
        photo_id = _openverse_id(raw)
        if photo_id is None:
            raise InvalidError("that isn't an Openverse image id")
        return photo_id

    def _get(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        # Shared by the whole household (one IP upstream), so it is not per user.
        retry_after = self.limiter.acquire("api")
        if retry_after > 0:
            seconds = max(1, round(retry_after))
            raise PhotoSearchRateLimited(
                f"photo search is busy; try again in {seconds} s", retry_after=seconds
            )
        return self.http.get_json(OPENVERSE_API + path, params, **kw)

    def search(self, query: str, page: int, per_page: int) -> SearchPage:
        body = self._get(
            "/images/",
            {
                "q": query,
                "page": page,
                "page_size": per_page,
                "license": OPENVERSE_LICENSES,
                "category": "photograph",
                "mature": "false",
            },
        )
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        results = [p for p in map(parse_openverse_image, raw_results) if p is not None]
        page_count = _int(body.get("page_count"))
        return SearchPage(self.name, query, page, results, has_more=page < page_count)

    def get(self, photo_id: PhotoId) -> PhotoResult:
        body = self._get(f"/images/{self.parse_id(photo_id)}/", missing_ok=False)
        photo = parse_openverse_image(body)
        if photo is None or photo.id != photo_id:
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        return photo

    def allowed_download(self, url: str) -> bool:
        return safe_fetch.is_safe_url(url, self.resolver)

    def download_transport(self) -> httpx2.BaseTransport | None:
        if self.http.transport is not None:
            return None
        return safe_fetch.guarded_transport(self.resolver)

    def thumbnail_url(self, photo_id: PhotoId) -> str:
        return f"{OPENVERSE_API}/images/{self.parse_id(photo_id)}/thumb/"


# --- caches ------------------------------------------------------------------------------


class _TTLCache:
    def __init__(self, ttl: float, max_entries: int, clock: Callable[[], float]) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._items: OrderedDict[tuple, tuple[float, SearchPage]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> SearchPage | None:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if hit[0] <= self.clock():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return hit[1]

    def put(self, key: tuple, value: SearchPage) -> None:
        with self._lock:
            self._items[key] = (self.clock() + self.ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)


class _BytesCache:
    """LRU bounded by total size; thumbnails never change for an id."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple, tuple[bytes, str]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    def get(self, key: tuple) -> tuple[bytes, str] | None:
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
            return hit

    def put(self, key: tuple, value: tuple[bytes, str]) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._size -= len(old[0])
            self._items[key] = value
            self._size += len(value[0])
            while self._size > self.max_bytes and self._items:
                _, (data, _) = self._items.popitem(last=False)
                self._size -= len(data)


def sniff_thumb_type(data: bytes) -> str | None:
    for magic, content_type in THUMB_TYPES.items():
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# --- facade ------------------------------------------------------------------------------


def choose_provider(setting: str, pexels_available: bool) -> ProviderName:
    if setting == "pexels":
        return "pexels"
    if setting == "openverse":
        return "openverse"
    return "pexels" if pexels_available else "openverse"


class ImageSearch:
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout: float,
        rate_limit_per_minute: int,
        max_download_bytes: int,
        provider: str = "auto",
        transport: httpx2.BaseTransport | None = None,
        resolver: safe_fetch.Resolver = safe_fetch.system_resolver,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        http = _Http(timeout, transport)
        self.http = http
        self.max_download_bytes = max_download_bytes
        self.providers: dict[ProviderName, Provider] = {
            "pexels": PexelsProvider(http, api_key),
            "openverse": OpenverseProvider(http, resolver=resolver, clock=clock),
        }
        self.default: ProviderName = choose_provider(provider, self.providers["pexels"].available)
        self.limiter = SlidingWindowLimiter(limit=rate_limit_per_minute, window=60.0, clock=clock)
        self.cache = _TTLCache(CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES, clock)
        self.thumbs = _BytesCache(THUMB_CACHE_MAX_BYTES)

    @property
    def configured(self) -> bool:
        return self.providers[self.default].available

    def provider(self, name: str | None = None) -> Provider:
        if name is None:
            name = self.default
        if name not in self.providers:
            raise InvalidError("unknown photo provider")
        provider = self.providers[name]  # type: ignore[index]
        if not provider.available:
            raise PhotoSearchUnavailable("photo search isn't configured")
        return provider

    def search(
        self, user_id: int, query: str, page: int = 1, per_page: int = PER_PAGE_DEFAULT
    ) -> SearchPage:
        query = " ".join(query.split())[:QUERY_MAX]
        if not query:
            raise InvalidError("type something to search for")
        provider = self.provider()
        page = min(max(page, 1), PAGE_MAX)
        per_page = min(max(per_page, 1), provider.per_page_max)
        key = (provider.name, query.casefold(), page, per_page)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        self._throttle(user_id)
        result = provider.search(query, page, per_page)
        self.cache.put(key, result)
        return result

    def lookup(self, user_id: int, provider_name: str, photo_id: object) -> PhotoResult:
        provider = self.provider(provider_name)
        parsed = provider.parse_id(photo_id)
        self._throttle(user_id)
        return provider.get(parsed)

    def download(
        self, user_id: int, provider_name: str, photo_id: object
    ) -> tuple[PhotoResult, bytes]:
        """Fetch the photo's metadata again by id and download what that answer names."""
        photo = self.lookup(user_id, provider_name, photo_id)
        provider = self.provider(provider_name)
        if not photo.download_urls:
            raise PhotoSearchFailed("that photo has no downloadable image")
        failure = PhotoSearchFailed("the photo could not be downloaded")
        for url in photo.download_urls:
            try:
                data, _ = self.http.fetch(
                    url,
                    allowed=provider.allowed_download,
                    max_bytes=self.max_download_bytes,
                    transport=provider.download_transport(),
                )
            except PhotoSearchFailed as exc:
                failure = exc
                continue
            return photo, data
        raise failure

    def thumbnail(self, provider_name: str, photo_id: object) -> tuple[bytes, str]:
        """Thumbnail bytes and content type for a provider whose thumbnails are proxied."""
        provider = self.provider(provider_name)
        if not isinstance(provider, OpenverseProvider):
            raise InvalidError("thumbnails for this provider load directly")
        parsed = provider.parse_id(photo_id)
        key = (provider.name, parsed)
        cached = self.thumbs.get(key)
        if cached is not None:
            return cached
        data, declared = self.http.fetch(
            provider.thumbnail_url(parsed),
            allowed=_is_openverse_thumb,
            max_bytes=THUMB_MAX_BYTES,
            transport=provider.download_transport(),
            error="the thumbnail could not be loaded",
        )
        sniffed = sniff_thumb_type(data)
        if sniffed is None or not declared.split(";")[0].strip().lower().startswith("image/"):
            raise PhotoSearchFailed("the thumbnail could not be loaded")
        self.thumbs.put(key, (data, sniffed))
        return data, sniffed

    def _throttle(self, user_id: int) -> None:
        retry_after = self.limiter.acquire(str(user_id))
        if retry_after > 0:
            seconds = max(1, round(retry_after))
            raise PhotoSearchRateLimited(
                f"too many photo searches; try again in {seconds} s", retry_after=seconds
            )


def _is_openverse_thumb(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.netloc == OPENVERSE_HOST
        and port is None
        and parts.path.startswith("/v1/images/")
    )


_transport: httpx2.BaseTransport | None = None
_resolver: safe_fetch.Resolver = safe_fetch.system_resolver


@lru_cache
def get_image_search() -> ImageSearch:
    settings = get_settings()
    key = settings.pexels_api_key
    return ImageSearch(
        api_key=key.get_secret_value() if key else None,
        timeout=settings.photo_search_timeout_seconds,
        rate_limit_per_minute=settings.photo_search_rate_limit_per_minute,
        max_download_bytes=settings.photo_max_bytes,
        provider=settings.photo_search_provider,
        transport=_transport,
        resolver=_resolver,
    )


def search(user_id: int, query: str, page: int = 1, per_page: int = PER_PAGE_DEFAULT) -> SearchPage:
    return get_image_search().search(user_id, query, page, per_page)


def thumbnail(provider: str, photo_id: object) -> tuple[bytes, str]:
    return get_image_search().thumbnail(provider, photo_id)


def set_recipe_photo_from_search(
    db: Session, user_id: int, recipe_id: int, provider: str, photo_id: object
) -> Recipe:
    if provider not in PROVIDERS:
        raise InvalidError("unknown photo provider")
    if db.get(Recipe, recipe_id) is None:
        raise NotFoundError(f"recipe {recipe_id} not found")
    photo, data = get_image_search().download(user_id, provider, photo_id)
    return photos.set_recipe_photo(db, recipe_id, data, credit=credit(photo))
