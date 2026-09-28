import io
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.config import get_settings
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import Recipe, User
from jyj.services import auth as auth_service
from jyj.services import image_search
from jyj.services.errors import InvalidError, NotFoundError
from jyj.services.image_search import (
    ImageSearch,
    PhotoSearchFailed,
    PhotoSearchRateLimited,
    PhotoSearchUnavailable,
)
from jyj.services.photos import PhotoTooLarge

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1"
KEY = "test-pexels-key"
CDN = "https://images.pexels.com/photos"


def pexels_photo(photo_id: int = 101, **overrides) -> dict:
    photo = {
        "id": photo_id,
        "width": 4000,
        "height": 3000,
        "url": f"https://www.pexels.com/photo/tortilla-{photo_id}/",
        "photographer": "Ana Cook",
        "photographer_url": "https://www.pexels.com/@ana",
        "alt": "Spanish tortilla on a plate",
        "src": {
            "original": f"{CDN}/{photo_id}/original.jpeg",
            "large2x": f"{CDN}/{photo_id}/large2x.jpeg",
            "large": f"{CDN}/{photo_id}/large.jpeg",
            "medium": f"{CDN}/{photo_id}/medium.jpeg",
            "small": f"{CDN}/{photo_id}/small.jpeg",
            "tiny": f"{CDN}/{photo_id}/tiny.jpeg",
        },
    }
    photo.update(overrides)
    return photo


def jpeg(size: tuple[int, int] = (800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "orange").save(buf, "JPEG")
    return buf.getvalue()


class FakePexels:
    """Routes MockTransport requests; records them so tests can assert on what was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.search_body: dict = {"photos": [pexels_photo(101), pexels_photo(102)]}
        self.photos: dict[int, dict] = {101: pexels_photo(101)}
        self.images: dict[str, httpx2.Response] = {}
        self.api_status = 200
        self.openverse = None

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "api.pexels.com":
            if request.headers.get("authorization") != KEY:
                return httpx2.Response(401, json={"error": "bad key"})
            if self.api_status != 200:
                return httpx2.Response(self.api_status, text="upstream says no")
            if url.path == "/v1/search":
                return httpx2.Response(200, json=self.search_body)
            photo_id = int(url.path.rsplit("/", 1)[-1])
            if photo_id not in self.photos:
                return httpx2.Response(404, json={"error": "not found"})
            return httpx2.Response(200, json=self.photos[photo_id])
        if url.host == "api.openverse.org" and self.openverse is not None:
            return self.openverse.handler(request)
        response = self.images.get(str(url))
        if response is not None:
            return response
        return httpx2.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})

    def api_calls(self) -> list[httpx2.Request]:
        return [r for r in self.requests if r.url.host == "api.pexels.com"]


@pytest.fixture
def fake() -> FakePexels:
    return FakePexels()


def make_search(fake: FakePexels, key: str | None = KEY, **kwargs) -> ImageSearch:
    kwargs.setdefault("rate_limit_per_minute", 30)
    return ImageSearch(
        api_key=key,
        timeout=5,
        max_download_bytes=kwargs.pop("max_download_bytes", 2 * 1024 * 1024),
        transport=httpx2.MockTransport(fake.handler),
        **kwargs,
    )


# --- service -----------------------------------------------------------------------------


def test_search_maps_results_and_sends_key(fake: FakePexels) -> None:
    fake.search_body["next_page"] = "https://api.pexels.com/v1/search?page=2"
    page = make_search(fake).search(1, "  tortilla   de patatas ", page=2, per_page=6)
    assert page.query == "tortilla de patatas"
    assert page.page == 2
    assert page.has_more is True
    first = page.results[0].as_dict()
    assert first == {
        "id": 101,
        "alt": "Spanish tortilla on a plate",
        "width": 4000,
        "height": 3000,
        "photographer": "Ana Cook",
        "photographer_url": "https://www.pexels.com/@ana",
        "page_url": "https://www.pexels.com/photo/tortilla-101/",
        "thumb_url": f"{CDN}/101/medium.jpeg",
        "preview_url": f"{CDN}/101/large.jpeg",
        "provider": "pexels",
        "title": None,
        "license": None,
        "license_url": None,
    }
    assert page.provider == "pexels"
    (request,) = fake.requests
    assert request.headers["authorization"] == KEY
    assert request.url.params["query"] == "tortilla de patatas"
    assert request.url.params["page"] == "2"
    assert request.url.params["per_page"] == "6"


def test_search_drops_off_cdn_and_malformed_results(fake: FakePexels) -> None:
    evil = pexels_photo(103)
    evil["src"]["medium"] = "https://evil.example/tortilla.jpeg"
    http = pexels_photo(104)
    http["src"]["large"] = "http://images.pexels.com/photos/104/large.jpeg"
    sneaky_link = pexels_photo(105, photographer_url="javascript:alert(1)", url="https://x.io/")
    fake.search_body = {"photos": [evil, http, {"id": "x"}, "junk", sneaky_link]}
    results = make_search(fake).search(1, "tortilla").results
    assert [r.id for r in results] == [105]
    assert results[0].photographer_url is None
    assert results[0].page_url is None


def test_search_caches_per_query(fake: FakePexels) -> None:
    search = make_search(fake)
    search.search(1, "Tortilla")
    search.search(2, "tortilla ")
    assert len(fake.requests) == 1
    search.search(1, "tortilla", page=2)
    assert len(fake.requests) == 2


def test_cache_expires(fake: FakePexels) -> None:
    now = [0.0]
    search = make_search(fake, clock=lambda: now[0])
    search.search(1, "tortilla")
    now[0] += image_search.CACHE_TTL_SECONDS + 1
    search.search(1, "tortilla")
    assert len(fake.requests) == 2


def test_rate_limit_is_per_user(fake: FakePexels) -> None:
    search = make_search(fake, rate_limit_per_minute=2)
    search.search(1, "a")
    search.search(1, "b")
    with pytest.raises(PhotoSearchRateLimited) as exc:
        search.search(1, "c")
    assert exc.value.status == 429
    assert exc.value.extensions["retry_after"] >= 1
    search.search(1, "a")  # cached answers are not counted
    search.search(2, "c")


def test_forced_pexels_without_key_is_unavailable(fake: FakePexels) -> None:
    for key in (None, "", "   "):
        search = make_search(fake, key=key, provider="pexels")
        assert not search.configured
        with pytest.raises(PhotoSearchUnavailable, match="isn't configured"):
            search.search(1, "tortilla")
        with pytest.raises(PhotoSearchUnavailable):
            search.download(1, "pexels", 101)
    assert fake.requests == []


def test_auto_without_key_uses_openverse(fake: FakePexels) -> None:
    for key in (None, "", "   "):
        search = make_search(fake, key=key)
        assert search.default == "openverse"
        assert search.configured
        with pytest.raises(PhotoSearchUnavailable):
            search.download(1, "pexels", 101)


def test_blank_query_is_invalid(fake: FakePexels) -> None:
    with pytest.raises(InvalidError):
        make_search(fake).search(1, "   ")


def test_upstream_throttle_is_429(fake: FakePexels) -> None:
    fake.api_status = 429
    with pytest.raises(PhotoSearchRateLimited) as exc:
        make_search(fake).search(1, "tortilla")
    assert exc.value.status == 429
    assert "upstream says no" not in exc.value.detail


@pytest.mark.parametrize("status", [401, 500, 503])
def test_upstream_errors_are_sanitized(fake: FakePexels, status: int) -> None:
    fake.api_status = status
    with pytest.raises(PhotoSearchFailed) as exc:
        make_search(fake).search(1, "tortilla")
    assert exc.value.status == 502
    assert "upstream says no" not in exc.value.detail
    assert KEY not in exc.value.detail


def test_network_errors_are_sanitized() -> None:
    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}", request=request)

    search = ImageSearch(
        api_key=KEY,
        timeout=1,
        rate_limit_per_minute=5,
        max_download_bytes=1024,
        transport=httpx2.MockTransport(boom),
    )
    with pytest.raises(PhotoSearchFailed, match="unreachable"):
        search.search(1, "tortilla")


def test_non_json_answer_is_rejected(fake: FakePexels) -> None:
    fake.search_body = {"unexpected": True}
    with pytest.raises(PhotoSearchFailed):
        make_search(fake).search(1, "tortilla")


def test_key_is_never_logged(fake: FakePexels, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    fake.api_status = 500
    with pytest.raises(PhotoSearchFailed):
        make_search(fake).search(1, "tortilla")
    make_search(FakePexels()).search(1, "tortilla")
    assert KEY not in caplog.text


def test_download_refetches_metadata_and_uses_large2x(fake: FakePexels) -> None:
    photo, data = make_search(fake).download(1, "pexels", 101)
    assert photo.id == 101
    assert data.startswith(b"\xff\xd8\xff")
    assert [str(r.url) for r in fake.requests] == [
        "https://api.pexels.com/v1/photos/101",
        f"{CDN}/101/large2x.jpeg",
    ]
    image_request = fake.requests[1]
    assert "authorization" not in image_request.headers


def test_download_falls_back_to_original(fake: FakePexels) -> None:
    fake.photos[101]["src"]["large2x"] = "https://evil.example/large2x.jpeg"
    make_search(fake).download(1, "pexels", 101)
    assert str(fake.requests[-1].url) == f"{CDN}/101/original.jpeg"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/photo.jpeg",
        "http://images.pexels.com/photos/101/a.jpeg",
        "https://images.pexels.com.evil.example/a.jpeg",
        "https://user@images.pexels.com/a.jpeg",
        "https://images.pexels.com:8443/a.jpeg",
        "ftp://images.pexels.com/a.jpeg",
    ],
)
def test_download_rejects_non_pexels_hosts(fake: FakePexels, url: str) -> None:
    fake.photos[101]["src"].update(large2x=url, original=url)
    with pytest.raises(PhotoSearchFailed):
        make_search(fake).download(1, "pexels", 101)
    assert all(r.url.host in {"api.pexels.com"} for r in fake.requests)


def test_download_rejects_redirect_to_another_host(fake: FakePexels) -> None:
    fake.images[f"{CDN}/101/large2x.jpeg"] = httpx2.Response(
        302, headers={"location": "https://evil.example/steal.jpeg"}
    )
    with pytest.raises(PhotoSearchFailed, match="could not be downloaded"):
        make_search(fake).download(1, "pexels", 101)
    assert "evil.example" not in {r.url.host for r in fake.requests}


def test_download_follows_redirect_within_cdn(fake: FakePexels) -> None:
    fake.images[f"{CDN}/101/large2x.jpeg"] = httpx2.Response(
        301, headers={"location": "/photos/101/moved.jpeg"}
    )
    _, data = make_search(fake).download(1, "pexels", 101)
    assert data.startswith(b"\xff\xd8\xff")
    assert str(fake.requests[-1].url) == f"{CDN}/101/moved.jpeg"


def test_download_stops_redirect_loops(fake: FakePexels) -> None:
    fake.images[f"{CDN}/101/large2x.jpeg"] = httpx2.Response(
        302, headers={"location": f"{CDN}/101/large2x.jpeg"}
    )
    with pytest.raises(PhotoSearchFailed):
        make_search(fake).download(1, "pexels", 101)
    assert len(fake.requests) == 1 + image_search.MAX_REDIRECTS + 1


def test_download_caps_size_while_streaming(fake: FakePexels) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(64):
            yield b"\xff" * 64 * 1024

    fake.images[f"{CDN}/101/large2x.jpeg"] = httpx2.Response(200, content=chunks())
    with pytest.raises(PhotoTooLarge):
        make_search(fake, max_download_bytes=1024 * 1024).download(1, "pexels", 101)


def test_download_rejects_declared_oversize(fake: FakePexels) -> None:
    fake.images[f"{CDN}/101/large2x.jpeg"] = httpx2.Response(
        200, content=b"x" * 10, headers={"content-length": str(50 * 1024 * 1024)}
    )
    with pytest.raises(PhotoTooLarge):
        make_search(fake).download(1, "pexels", 101)


def test_download_of_unknown_photo_is_not_found(fake: FakePexels) -> None:
    with pytest.raises(NotFoundError):
        make_search(fake).download(1, "pexels", 999)


def test_download_rejects_mismatched_id(fake: FakePexels) -> None:
    fake.photos[101] = pexels_photo(555)
    with pytest.raises(PhotoSearchFailed):
        make_search(fake).download(1, "pexels", 101)


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakePexels
) -> Iterator[Callable[[str | None], None]]:
    monkeypatch.setenv("PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setattr(image_search, "_transport", httpx2.MockTransport(fake.handler))

    def apply(key: str | None) -> None:
        if key is None:
            monkeypatch.setenv("PEXELS_API_KEY", "")
        else:
            monkeypatch.setenv("PEXELS_API_KEY", key)
        get_settings.cache_clear()
        image_search.get_image_search.cache_clear()

    apply(KEY)
    yield apply
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()


@pytest.fixture
def session_factory(db_connection: Connection) -> Callable[[], Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def user(db: Session) -> User:
    user = auth_service.create_user(db, "cook", "Cook", "correct horse battery")
    db.commit()
    return user


@pytest.fixture
def anon(session_factory: Callable[[], Session], configure) -> Iterator[TestClient]:
    app = create_app()

    def test_db() -> Iterator[Session]:
        yield from session_scope(session_factory)

    app.dependency_overrides[get_db] = test_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def client(anon: TestClient, db: Session, user: User) -> TestClient:
    issued = auth_service.create_session(db, user, "pytest")
    db.commit()
    anon.cookies.set(SESSION_COOKIE, issued.token)
    anon.headers.update(CSRF)
    return anon


@pytest.fixture
def recipe(client: TestClient) -> dict:
    response = client.post(f"{API}/recipes", json={"name": "Tortilla"})
    assert response.status_code == 201, response.text
    return response.json()


def assert_problem(response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    return response.json()


def from_search(client: TestClient, recipe_id: int, photo_id: int = 101, **kwargs):
    return client.post(
        f"{API}/recipes/{recipe_id}/photo/from-search",
        json={"provider": "pexels", "photo_id": photo_id},
        **kwargs,
    )


def test_search_endpoint(client: TestClient, fake: FakePexels) -> None:
    response = client.get(f"{API}/images/search", params={"q": "tortilla"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "pexels"
    assert body["query"] == "tortilla"
    assert body["has_more"] is False
    assert [r["id"] for r in body["results"]] == [101, 102]
    assert KEY not in response.text


def test_search_endpoint_needs_login(anon: TestClient, fake: FakePexels) -> None:
    assert_problem(anon.get(f"{API}/images/search", params={"q": "tortilla"}), 401)
    assert fake.requests == []


def test_search_endpoint_validates_query(client: TestClient) -> None:
    assert_problem(client.get(f"{API}/images/search"), 422)
    assert_problem(client.get(f"{API}/images/search", params={"q": "x" * 101}), 422)
    assert_problem(client.get(f"{API}/images/search", params={"q": "a", "page": 0}), 422)


def test_search_endpoint_forced_pexels_without_key_is_503(
    client: TestClient, configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOTO_SEARCH_PROVIDER", "pexels")
    configure(None)
    body = assert_problem(client.get(f"{API}/images/search", params={"q": "tortilla"}), 503)
    assert body["detail"] == "photo search isn't configured"


def test_search_endpoint_upstream_error_is_502(client: TestClient, fake: FakePexels) -> None:
    fake.api_status = 500
    response = client.get(f"{API}/images/search", params={"q": "tortilla"})
    body = assert_problem(response, 502)
    assert "upstream" not in response.text
    assert body["detail"] == "photo search failed; try again later"


def test_search_endpoint_rate_limit_is_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOTO_SEARCH_RATE_LIMIT_PER_MINUTE", "1")
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()
    assert client.get(f"{API}/images/search", params={"q": "a"}).status_code == 200
    body = assert_problem(client.get(f"{API}/images/search", params={"q": "b"}), 429)
    assert body["retry_after"] >= 1


def test_import_stores_photo_and_credit(
    client: TestClient, recipe: dict, fake: FakePexels, db: Session
) -> None:
    response = from_search(client, recipe["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["photo_url"].startswith("/media/")
    assert body["photo_credit"] == {
        "provider": "pexels",
        "photographer": "Ana Cook",
        "photographer_url": "https://www.pexels.com/@ana",
        "page_url": "https://www.pexels.com/photo/tortilla-101/",
        "title": None,
        "license": None,
        "license_url": None,
    }
    fetched = client.get(f"{API}/recipes/{recipe['id']}").json()
    assert fetched["photo_credit"] == body["photo_credit"]
    listed = client.get(f"{API}/recipes").json()["items"][0]
    assert listed["photo_credit"]["photographer"] == "Ana Cook"
    stored = Path(get_settings().photos_dir) / body["photo_url"].removeprefix("/media/")
    with Image.open(stored) as img:
        assert img.format == "WEBP"


def test_import_ignores_client_supplied_urls(client: TestClient, recipe: dict) -> None:
    response = client.post(
        f"{API}/recipes/{recipe['id']}/photo/from-search",
        json={"provider": "pexels", "photo_id": 101, "url": "https://evil.example/x.jpeg"},
    )
    assert_problem(response, 422)


def test_import_rejects_unknown_provider(client: TestClient, recipe: dict) -> None:
    response = client.post(
        f"{API}/recipes/{recipe['id']}/photo/from-search",
        json={"provider": "unsplash", "photo_id": 101},
    )
    assert_problem(response, 422)


def test_import_rejects_non_pexels_host(client: TestClient, recipe: dict, fake: FakePexels) -> None:
    fake.photos[101]["src"].update(
        large2x="https://evil.example/a.jpeg", original="https://evil.example/b.jpeg"
    )
    assert_problem(from_search(client, recipe["id"]), 502)
    assert client.get(f"{API}/recipes/{recipe['id']}").json()["photo_url"] is None


def test_import_rejects_oversize(
    client: TestClient, recipe: dict, fake: FakePexels, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOTO_MAX_BYTES", str(1024))
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()
    assert_problem(from_search(client, recipe["id"]), 413)


def test_import_unknown_photo_or_recipe_is_404(client: TestClient, recipe: dict) -> None:
    assert_problem(from_search(client, recipe["id"], photo_id=999), 404)
    assert_problem(from_search(client, 999_999), 404)


def test_import_needs_csrf_header_and_login(
    anon: TestClient, client: TestClient, recipe: dict, fake: FakePexels
) -> None:
    response = client.post(
        f"{API}/recipes/{recipe['id']}/photo/from-search",
        json={"provider": "pexels", "photo_id": 101},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403
    client.cookies.clear()
    assert_problem(from_search(client, recipe["id"]), 401)
    assert fake.api_calls() == []


def test_import_pexels_without_key_is_503(client: TestClient, recipe: dict, configure) -> None:
    configure(None)
    assert_problem(from_search(client, recipe["id"]), 503)


def test_credit_cleared_by_upload_and_delete(client: TestClient, recipe: dict, db: Session) -> None:
    assert from_search(client, recipe["id"]).json()["photo_credit"] is not None
    uploaded = client.put(
        f"{API}/recipes/{recipe['id']}/photo",
        files={"file": ("photo.jpg", jpeg(), "image/jpeg")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["photo_credit"] is None
    assert db.get(Recipe, recipe["id"], populate_existing=True).photo_credit is None

    assert from_search(client, recipe["id"]).json()["photo_credit"] is not None
    removed = client.delete(f"{API}/recipes/{recipe['id']}/photo")
    assert removed.status_code == 200
    assert removed.json()["photo_credit"] is None
    assert db.get(Recipe, recipe["id"], populate_existing=True).photo_credit is None


def test_credit_is_hidden_without_photo(client: TestClient, recipe: dict, db: Session) -> None:
    row = db.get(Recipe, recipe["id"])
    row.photo_credit = {"provider": "pexels", "photographer": "Stale"}
    db.commit()
    assert client.get(f"{API}/recipes/{recipe['id']}").json()["photo_credit"] is None


def test_settings_repr_hides_key(configure) -> None:
    settings = get_settings()
    assert KEY not in repr(settings)
    assert KEY not in json.dumps(settings.model_dump(mode="json"))
