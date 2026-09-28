"""Stock photo search (Pexels) and importing a chosen result as a recipe photo.

Search proxies the Pexels API so the key stays on the server. Imports never trust a URL
from the client: the photo is looked up again by id, and only its ``images.pexels.com``
rendition is downloaded, streamed under the photo size cap, before the normal photo
pipeline re-encodes it. Rate limit and cache are in-process, which is valid because the
backend runs a single worker.
"""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2
from sqlalchemy.orm import Session

from jyj.config import get_settings
from jyj.models import Recipe
from jyj.services import photos
from jyj.services.errors import InvalidError, NotFoundError, ServiceError
from jyj.services.rate_limit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

Provider = Literal["pexels"]
PROVIDER: Provider = "pexels"

API_BASE = "https://api.pexels.com/v1"
IMAGE_HOST = "images.pexels.com"
LINK_HOSTS = frozenset({"www.pexels.com", "pexels.com"})
QUERY_MAX = 100
PAGE_MAX = 50
PER_PAGE_MAX = 40
PER_PAGE_DEFAULT = 24
ALT_MAX = 200
NAME_MAX = 200
CACHE_TTL_SECONDS = 600.0
CACHE_MAX_ENTRIES = 200
MAX_REDIRECTS = 3
DOWNLOAD_RENDITIONS = ("large2x", "original")
USER_AGENT = "jyj-recipes"


class PhotoSearchUnavailable(ServiceError):
    status = 503


class PhotoSearchFailed(ServiceError):
    status = 502


class PhotoSearchRateLimited(ServiceError):
    status = 429


@dataclass(frozen=True, slots=True)
class PhotoResult:
    id: int
    alt: str
    width: int
    height: int
    photographer: str
    photographer_url: str | None
    page_url: str | None
    thumb_url: str
    preview_url: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SearchPage:
    query: str
    page: int
    results: list[PhotoResult]
    has_more: bool


def is_configured() -> bool:
    key = get_settings().pexels_api_key
    return key is not None and bool(key.get_secret_value().strip())


def is_image_url(url: object) -> bool:
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
        and parts.hostname == IMAGE_HOST
        and parts.netloc == IMAGE_HOST
        and port is None
    )


def _link(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc not in LINK_HOSTS:
        return None
    return url


def _text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def parse_photo(raw: object) -> PhotoResult | None:
    """Map one Pexels photo object; anything malformed or off-CDN is dropped."""
    if not isinstance(raw, dict):
        return None
    src = raw.get("src")
    photo_id = raw.get("id")
    if not isinstance(src, dict) or not isinstance(photo_id, int) or photo_id < 1:
        return None
    thumb, preview = src.get("medium"), src.get("large")
    if not (is_image_url(thumb) and is_image_url(preview)):
        return None
    width, height = raw.get("width"), raw.get("height")
    return PhotoResult(
        id=photo_id,
        alt=_text(raw.get("alt"), ALT_MAX),
        width=width if isinstance(width, int) else 0,
        height=height if isinstance(height, int) else 0,
        photographer=_text(raw.get("photographer"), NAME_MAX) or "Unknown photographer",
        photographer_url=_link(raw.get("photographer_url")),
        page_url=_link(raw.get("url")),
        thumb_url=thumb,
        preview_url=preview,
    )


def credit(photo: PhotoResult) -> dict[str, Any]:
    return {
        "provider": PROVIDER,
        "photographer": photo.photographer,
        "photographer_url": photo.photographer_url,
        "page_url": photo.page_url,
    }


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


class ImageSearch:
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout: float,
        rate_limit_per_minute: int,
        max_download_bytes: int,
        transport: httpx2.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self.transport = transport
        self.limiter = SlidingWindowLimiter(limit=rate_limit_per_minute, window=60.0, clock=clock)
        self.cache = _TTLCache(CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES, clock)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def search(
        self, user_id: int, query: str, page: int = 1, per_page: int = PER_PAGE_DEFAULT
    ) -> SearchPage:
        query = " ".join(query.split())[:QUERY_MAX]
        if not query:
            raise InvalidError("type something to search for")
        page = min(max(page, 1), PAGE_MAX)
        per_page = min(max(per_page, 1), PER_PAGE_MAX)
        self._require_key()
        key = (query.casefold(), page, per_page)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        self._throttle(user_id)
        body = self._api_get(
            "/search",
            {"query": query, "page": page, "per_page": per_page, "orientation": "landscape"},
        )
        raw_photos = body.get("photos")
        if not isinstance(raw_photos, list):
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        results = [p for p in map(parse_photo, raw_photos) if p is not None]
        result = SearchPage(query, page, results, has_more=bool(body.get("next_page")))
        self.cache.put(key, result)
        return result

    def lookup(self, user_id: int, photo_id: int) -> PhotoResult:
        return self._lookup(user_id, photo_id)[0]

    def download(self, user_id: int, photo_id: int) -> tuple[PhotoResult, bytes]:
        """Fetch the photo's metadata again by id and download its CDN rendition."""
        photo, src = self._lookup(user_id, photo_id)
        url = next((src[k] for k in DOWNLOAD_RENDITIONS if is_image_url(src.get(k))), None)
        if url is None:
            raise PhotoSearchFailed("that photo has no downloadable image")
        return photo, self._fetch_image(url)

    def _lookup(self, user_id: int, photo_id: int) -> tuple[PhotoResult, dict[str, Any]]:
        self._require_key()
        self._throttle(user_id)
        body = self._api_get(f"/photos/{int(photo_id)}", None, missing_ok=False)
        photo = parse_photo(body)
        if photo is None or photo.id != photo_id:
            raise PhotoSearchFailed("photo search returned an unexpected answer")
        return photo, body["src"]

    def _require_key(self) -> None:
        if not self.configured:
            raise PhotoSearchUnavailable("photo search isn't configured")

    def _throttle(self, user_id: int) -> None:
        retry_after = self.limiter.acquire(str(user_id))
        if retry_after > 0:
            seconds = max(1, round(retry_after))
            raise PhotoSearchRateLimited(
                f"too many photo searches; try again in {seconds} s", retry_after=seconds
            )

    def _client(self) -> httpx2.Client:
        return httpx2.Client(
            timeout=self.timeout,
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": USER_AGENT},
        )

    def _api_get(
        self,
        path: str,
        params: dict[str, Any] | None,
        *,
        missing_ok: bool = True,
    ) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.get(
                    API_BASE + path,
                    params=params,
                    headers={"Authorization": self._api_key or ""},
                )
        except httpx2.HTTPError as exc:
            logger.warning("photo search request failed: %s", type(exc).__name__)
            raise PhotoSearchFailed("photo search is unreachable; try again later") from None
        if response.status_code == 404 and not missing_ok:
            raise NotFoundError("that photo is no longer available")
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

    def _fetch_image(self, url: str) -> bytes:
        limit = self.max_download_bytes
        try:
            with self._client() as client:
                for _ in range(MAX_REDIRECTS + 1):
                    if not is_image_url(url):
                        raise PhotoSearchFailed("the photo is not hosted on the Pexels CDN")
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location", "")
                            url = str(response.url.join(location))
                            continue
                        if response.status_code != 200:
                            logger.warning("photo download answered %s", response.status_code)
                            raise PhotoSearchFailed("the photo could not be downloaded")
                        declared = response.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > limit:
                            raise photos.PhotoTooLarge(_too_large(limit))
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data += chunk
                            if len(data) > limit:
                                raise photos.PhotoTooLarge(_too_large(limit))
                        return bytes(data)
                raise PhotoSearchFailed("the photo could not be downloaded")
        except httpx2.HTTPError as exc:
            logger.warning("photo download failed: %s", type(exc).__name__)
            raise PhotoSearchFailed("the photo could not be downloaded") from None


def _too_large(limit: int) -> str:
    return f"photo must be at most {limit // (1024 * 1024)} MiB"


_transport: httpx2.BaseTransport | None = None


@lru_cache
def get_image_search() -> ImageSearch:
    settings = get_settings()
    key = settings.pexels_api_key
    return ImageSearch(
        api_key=key.get_secret_value() if key else None,
        timeout=settings.pexels_timeout_seconds,
        rate_limit_per_minute=settings.photo_search_rate_limit_per_minute,
        max_download_bytes=settings.photo_max_bytes,
        transport=_transport,
    )


def search(user_id: int, query: str, page: int = 1, per_page: int = PER_PAGE_DEFAULT) -> SearchPage:
    return get_image_search().search(user_id, query, page, per_page)


def set_recipe_photo_from_search(
    db: Session, user_id: int, recipe_id: int, provider: Provider, photo_id: int
) -> Recipe:
    if provider != PROVIDER:
        raise InvalidError("unknown photo provider")
    if db.get(Recipe, recipe_id) is None:
        raise NotFoundError(f"recipe {recipe_id} not found")
    photo, data = get_image_search().download(user_id, photo_id)
    return photos.set_recipe_photo(db, recipe_id, data, credit=credit(photo))
