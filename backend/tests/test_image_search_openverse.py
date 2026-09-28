from collections.abc import Iterator

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_image_search import (  # noqa: F401 - fixtures
    API,
    KEY,
    FakePexels,
    anon,
    assert_problem,
    client,
    configure,
    db,
    fake,
    jpeg,
    make_search,
    recipe,
    session_factory,
    user,
)

from jyj.models import Recipe
from jyj.services import image_search
from jyj.services.errors import InvalidError, NotFoundError
from jyj.services.image_search import (
    ImageSearch,
    PhotoSearchFailed,
    PhotoSearchRateLimited,
    choose_provider,
    license_label,
)
from jyj.services.photos import PhotoTooLarge

OV = "https://api.openverse.org/v1"
ID = "d5f163e9-73a2-4fc8-8033-515f4380c930"
ID2 = "0b8e7a3c-5a1f-4c22-9a34-6d2f1e0c9b11"
FLICKR = "https://live.staticflickr.com/65535/50980878221_df0c7cb4d5_b.jpg"
PUBLIC = "151.101.1.1"


def ov_image(image_id: str = ID, **overrides) -> dict:
    image = {
        "id": image_id,
        "title": "Tortilla de patatas",
        "foreign_landing_url": "https://www.flickr.com/photos/64607715@N05/50980878221",
        "url": FLICKR,
        "creator": "Rod Waddington",
        "creator_url": "https://www.flickr.com/photos/64607715@N05",
        "license": "by-sa",
        "license_version": "2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
        "provider": "flickr",
        "category": "photograph",
        "mature": False,
        "width": 1024,
        "height": 683,
        "thumbnail": f"{OV}/images/{image_id}/thumb/",
    }
    image.update(overrides)
    return image


class FakeOpenverse:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.images: dict[str, dict] = {ID: ov_image(ID), ID2: ov_image(ID2, creator="Bea")}
        self.page_count = 1
        self.status = 200
        self.thumbs: dict[str, httpx2.Response] = {}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/thumb/"):
            image_id = path.split("/")[-3]
            override = self.thumbs.get(image_id)
            if override is not None:
                return override
            return httpx2.Response(
                200, content=jpeg((60, 40)), headers={"content-type": "image/jpeg"}
            )
        if self.status != 200:
            return httpx2.Response(self.status, text="openverse says no")
        if path == "/v1/images/":
            page = int(request.url.params.get("page", "1"))
            return httpx2.Response(
                200,
                json={
                    "result_count": len(self.images),
                    "page_count": self.page_count,
                    "page": page,
                    "results": list(self.images.values()),
                },
            )
        image_id = path.split("/")[-2]
        if image_id not in self.images:
            return httpx2.Response(404, json={"detail": "No Image matches the given query."})
        return httpx2.Response(200, json=self.images[image_id])

    def api_calls(self) -> list[httpx2.Request]:
        return [r for r in self.requests if not r.url.path.endswith("/thumb/")]


DNS: dict[str, list[str]] = {}


def fake_resolver(host: str, port: int) -> list[str]:
    return DNS.get(host, [PUBLIC])


@pytest.fixture(autouse=True)
def ov(fake: FakePexels, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOpenverse]:  # noqa: F811
    fake.openverse = FakeOpenverse()
    DNS.clear()
    monkeypatch.setattr(image_search, "_resolver", fake_resolver)
    image_search.get_image_search.cache_clear()
    yield fake.openverse
    DNS.clear()


def ov_search(fake: FakePexels, **kwargs) -> ImageSearch:  # noqa: F811
    kwargs.setdefault("key", None)
    kwargs.setdefault("resolver", fake_resolver)
    return make_search(fake, **kwargs)


# --- provider selection ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "has_key", "expected"),
    [
        ("auto", False, "openverse"),
        ("auto", True, "pexels"),
        ("openverse", True, "openverse"),
        ("openverse", False, "openverse"),
        ("pexels", True, "pexels"),
        ("pexels", False, "pexels"),
    ],
)
def test_choose_provider(setting: str, has_key: bool, expected: str) -> None:
    assert choose_provider(setting, has_key) == expected


def test_openverse_can_be_forced_with_a_key(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    page = ov_search(fake, key=KEY, provider="openverse").search(1, "tortilla")
    assert page.provider == "openverse"
    assert fake.api_calls() == []


@pytest.mark.parametrize(
    ("code", "version", "label"),
    [
        ("by-sa", "2.0", "CC BY-SA 2.0"),
        ("by", "4.0", "CC BY 4.0"),
        ("by-nc-sa", "3.0", "CC BY-NC-SA 3.0"),
        ("cc0", "1.0", "CC0 1.0"),
        ("pdm", "1.0", "Public Domain Mark 1.0"),
        ("BY", None, "CC BY"),
        ("sampling+", "1.0", None),
        ("<script>", "1", None),
        (None, "1.0", None),
    ],
)
def test_license_label(code, version, label) -> None:
    assert license_label(code, version) == label


# --- search ------------------------------------------------------------------------------


def test_search_maps_openverse_results(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    page = ov_search(fake).search(1, "  tortilla   de patatas ", per_page=24)
    assert page.provider == "openverse"
    assert page.query == "tortilla de patatas"
    assert page.results[0].as_dict() == {
        "provider": "openverse",
        "id": ID,
        "alt": "Tortilla de patatas",
        "width": 1024,
        "height": 683,
        "photographer": "Rod Waddington",
        "photographer_url": "https://www.flickr.com/photos/64607715@N05",
        "page_url": "https://www.flickr.com/photos/64607715@N05/50980878221",
        "thumb_url": f"/api/v1/images/thumb?provider=openverse&id={ID}",
        "preview_url": f"/api/v1/images/thumb?provider=openverse&id={ID}",
        "title": "Tortilla de patatas",
        "license": "CC BY-SA 2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
    }
    (request,) = ov.requests
    assert str(request.url).startswith(f"{OV}/images/?")
    params = request.url.params
    assert params["q"] == "tortilla de patatas"
    assert params["page_size"] == "20"
    assert params["category"] == "photograph"
    assert "nd" not in params["license"].split(",")
    assert "authorization" not in request.headers
    assert request.headers["user-agent"].startswith("jyj-recipes")


def test_search_pagination(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    ov.page_count = 3
    search = ov_search(fake)
    assert search.search(1, "tortilla", page=2).has_more is True
    assert ov.requests[-1].url.params["page"] == "2"
    assert search.search(1, "tortilla", page=3).has_more is False


def test_search_drops_malformed_mature_and_unlicensed(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    ov.images = {
        "a": ov_image("not-a-uuid"),
        "b": ov_image(ID.upper()),
        "c": ov_image("11111111-1111-1111-1111-111111111111", mature=True),
        "d": ov_image("22222222-2222-2222-2222-222222222222", license="sampling+"),
        "e": ov_image(
            "33333333-3333-3333-3333-333333333333",
            creator_url="javascript:alert(1)",
            foreign_landing_url="http://insecure.example/",
            license_url="https://evil.example/license",
            creator=None,
        ),
        "f": "junk",
    }
    (only,) = ov_search(fake).search(1, "tortilla").results
    assert only.id == "33333333-3333-3333-3333-333333333333"
    assert only.photographer == "Unknown creator"
    assert only.photographer_url is None
    assert only.page_url is None
    assert only.license_url is None


def test_search_is_cached(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    search = ov_search(fake)
    search.search(1, "Tortilla")
    search.search(2, "tortilla")
    assert len(ov.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 500, 503])
def test_openverse_errors_are_sanitized(fake: FakePexels, ov: FakeOpenverse, status: int) -> None:  # noqa: F811
    ov.status = status
    with pytest.raises(PhotoSearchFailed) as exc:
        ov_search(fake).search(1, "tortilla")
    assert exc.value.status == 502
    assert "openverse says no" not in exc.value.detail


def test_openverse_throttle_is_429(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    ov.status = 429
    with pytest.raises(PhotoSearchRateLimited):
        ov_search(fake).search(1, "tortilla")


def test_openverse_unreachable(fake: FakePexels) -> None:  # noqa: F811
    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    search = ImageSearch(
        api_key=None,
        timeout=1,
        rate_limit_per_minute=5,
        max_download_bytes=1024,
        transport=httpx2.MockTransport(boom),
    )
    with pytest.raises(PhotoSearchFailed, match="unreachable"):
        search.search(1, "tortilla")


def test_household_shares_the_openverse_budget(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    search = ov_search(fake, rate_limit_per_minute=100)
    for i in range(image_search.OPENVERSE_CALLS_PER_MINUTE):
        search.search(i % 3, f"dish {i}")
    with pytest.raises(PhotoSearchRateLimited, match="busy"):
        search.search(99, "one more")


# --- import ------------------------------------------------------------------------------


def test_download_refetches_metadata_and_downloads_source(
    fake: FakePexels,  # noqa: F811
    ov: FakeOpenverse,
) -> None:
    photo, data = ov_search(fake).download(1, "openverse", ID)
    assert photo.id == ID
    assert data.startswith(b"\xff\xd8\xff")
    assert [str(r.url) for r in fake.requests] == [f"{OV}/images/{ID}/", FLICKR]
    assert image_search.credit(photo) == {
        "provider": "openverse",
        "photographer": "Rod Waddington",
        "photographer_url": "https://www.flickr.com/photos/64607715@N05",
        "page_url": "https://www.flickr.com/photos/64607715@N05/50980878221",
        "title": "Tortilla de patatas",
        "license": "CC BY-SA 2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://live.staticflickr.com/a.jpg",
        "https://internal.example/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://user@live.staticflickr.com/a.jpg",
        "https://live.staticflickr.com:8080/a.jpg",
    ],
)
def test_unsafe_source_falls_back_to_openverse_rendition(
    fake: FakePexels,  # noqa: F811
    ov: FakeOpenverse,
    url: str,
) -> None:
    DNS.update({"internal.example": ["10.0.0.5"], "127.0.0.1": ["127.0.0.1"]})
    ov.images[ID]["url"] = url
    _, data = ov_search(fake).download(1, "openverse", ID)
    assert data.startswith(b"\xff\xd8\xff")
    hosts = [r.url.host for r in fake.requests]
    assert hosts == ["api.openverse.org", "api.openverse.org"]
    assert fake.requests[-1].url.params["full_size"] == "true"


def test_redirect_to_private_host_is_not_followed(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    DNS["metadata.internal"] = ["169.254.169.254"]
    fake.images[FLICKR] = httpx2.Response(
        302, headers={"location": "https://metadata.internal/latest/"}
    )
    ov.thumbs[ID] = httpx2.Response(404)
    with pytest.raises(PhotoSearchFailed):
        ov_search(fake).download(1, "openverse", ID)
    assert "metadata.internal" not in {r.url.host for r in fake.requests}


def test_redirect_to_plain_http_is_not_followed(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    fake.images[FLICKR] = httpx2.Response(
        302, headers={"location": "http://live.staticflickr.com/x"}
    )
    ov.thumbs[ID] = httpx2.Response(404)
    with pytest.raises(PhotoSearchFailed):
        ov_search(fake).download(1, "openverse", ID)
    assert all(r.url.scheme == "https" for r in fake.requests)


def test_redirect_to_public_host_is_followed(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    moved = "https://farm66.staticflickr.com/moved.jpg"
    fake.images[FLICKR] = httpx2.Response(301, headers={"location": moved})
    ov_search(fake).download(1, "openverse", ID)
    assert str(fake.requests[-1].url) == moved


def test_redirect_hops_are_capped(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    fake.images[FLICKR] = httpx2.Response(302, headers={"location": FLICKR})
    ov.thumbs[ID] = httpx2.Response(404)
    with pytest.raises(PhotoSearchFailed):
        ov_search(fake).download(1, "openverse", ID)
    flickr_hits = [r for r in fake.requests if str(r.url) == FLICKR]
    assert len(flickr_hits) == image_search.MAX_REDIRECTS + 1


def test_download_size_is_capped(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    fake.images[FLICKR] = httpx2.Response(200, content=b"\xff" * 4096)
    with pytest.raises(PhotoTooLarge):
        ov_search(fake, max_download_bytes=1024).download(1, "openverse", ID)


def test_download_validates_id(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    search = ov_search(fake)
    for bad in ("../../etc", "101", 101, ID.upper() + "x"):
        with pytest.raises(InvalidError):
            search.download(1, "openverse", bad)
    with pytest.raises(NotFoundError):
        search.download(1, "openverse", "44444444-4444-4444-4444-444444444444")
    ov.images[ID] = ov_image(ID2)
    with pytest.raises(PhotoSearchFailed):
        search.download(1, "openverse", ID)
    with pytest.raises(InvalidError):
        search.download(1, "unsplash", ID)


# --- thumbnails --------------------------------------------------------------------------


def test_thumbnail_is_proxied_and_cached(fake: FakePexels, ov: FakeOpenverse) -> None:  # noqa: F811
    search = ov_search(fake)
    data, content_type = search.thumbnail("openverse", ID)
    assert content_type == "image/jpeg"
    assert data.startswith(b"\xff\xd8\xff")
    assert search.thumbnail("openverse", ID) == (data, content_type)
    assert [str(r.url) for r in ov.requests] == [f"{OV}/images/{ID}/thumb/"]


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(
            200, content=b"<svg onload=alert(1)>", headers={"content-type": "image/svg+xml"}
        ),
        httpx2.Response(200, content=jpeg((10, 10)), headers={"content-type": "text/html"}),
        httpx2.Response(200, content=b"\xff\xd8\xff" + b"0" * (600 * 1024)),
        httpx2.Response(302, headers={"location": "https://evil.example/x.jpg"}),
        httpx2.Response(500),
    ],
)
def test_thumbnail_rejects_bad_answers(
    fake: FakePexels,  # noqa: F811
    ov: FakeOpenverse,
    response: httpx2.Response,
) -> None:
    ov.thumbs[ID] = response
    with pytest.raises((PhotoSearchFailed, PhotoTooLarge)):
        ov_search(fake).thumbnail("openverse", ID)
    assert "evil.example" not in {r.url.host for r in fake.requests}


def test_thumbnail_validates_provider_and_id(fake: FakePexels) -> None:  # noqa: F811
    search = ov_search(fake, key=KEY)
    for provider, photo_id in (("pexels", "101"), ("openverse", "../x"), ("nope", ID)):
        with pytest.raises(InvalidError):
            search.thumbnail(provider, photo_id)


# --- API ---------------------------------------------------------------------------------


def from_search(client: TestClient, recipe_id: int, photo_id: object = ID):  # noqa: F811
    return client.post(
        f"{API}/recipes/{recipe_id}/photo/from-search",
        json={"provider": "openverse", "photo_id": photo_id},
    )


def test_search_endpoint_works_without_a_key(client: TestClient, configure) -> None:  # noqa: F811
    configure(None)
    response = client.get(f"{API}/images/search", params={"q": "tortilla"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "openverse"
    first = body["results"][0]
    assert first["id"] == ID
    assert first["license"] == "CC BY-SA 2.0"
    assert first["thumb_url"] == f"/api/v1/images/thumb?provider=openverse&id={ID}"
    assert FLICKR not in response.text


def test_search_endpoint_openverse_down_is_502(
    client: TestClient,  # noqa: F811
    configure,  # noqa: F811
    ov: FakeOpenverse,
) -> None:
    configure(None)
    ov.status = 503
    body = assert_problem(client.get(f"{API}/images/search", params={"q": "tortilla"}), 502)
    assert body["detail"] == "photo search failed; try again later"


def test_thumb_endpoint(client: TestClient, configure) -> None:  # noqa: F811
    configure(None)
    response = client.get(f"{API}/images/thumb", params={"provider": "openverse", "id": ID})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in response.headers["cache-control"]
    assert response.content.startswith(b"\xff\xd8\xff")


def test_thumb_endpoint_validates(client: TestClient, anon: TestClient, configure) -> None:  # noqa: F811
    assert_problem(client.get(f"{API}/images/thumb", params={"provider": "x", "id": ID}), 422)
    assert_problem(client.get(f"{API}/images/thumb", params={"provider": "openverse"}), 422)
    assert_problem(
        client.get(f"{API}/images/thumb", params={"provider": "openverse", "id": "../../x"}), 422
    )
    client.cookies.clear()
    assert_problem(
        client.get(f"{API}/images/thumb", params={"provider": "openverse", "id": ID}), 401
    )


def test_import_from_openverse_stores_license_credit(
    client: TestClient,  # noqa: F811
    recipe: dict,  # noqa: F811
    configure,  # noqa: F811
    db: Session,  # noqa: F811
) -> None:
    configure(None)
    response = from_search(client, recipe["id"])
    assert response.status_code == 200, response.text
    credit = response.json()["photo_credit"]
    assert credit == {
        "provider": "openverse",
        "photographer": "Rod Waddington",
        "photographer_url": "https://www.flickr.com/photos/64607715@N05",
        "page_url": "https://www.flickr.com/photos/64607715@N05/50980878221",
        "title": "Tortilla de patatas",
        "license": "CC BY-SA 2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
    }
    stored = db.get(Recipe, recipe["id"], populate_existing=True)
    assert stored.photo_credit["license"] == "CC BY-SA 2.0"


def test_import_from_openverse_works_alongside_pexels(
    client: TestClient,  # noqa: F811
    recipe: dict,  # noqa: F811
) -> None:
    assert from_search(client, recipe["id"]).json()["photo_credit"]["provider"] == "openverse"


def test_import_from_openverse_rejects_bad_ids_and_urls(
    client: TestClient,  # noqa: F811
    recipe: dict,  # noqa: F811
    fake: FakePexels,  # noqa: F811
) -> None:
    assert_problem(from_search(client, recipe["id"], photo_id="not-a-uuid"), 422)
    assert_problem(from_search(client, recipe["id"], photo_id=101), 422)
    response = client.post(
        f"{API}/recipes/{recipe['id']}/photo/from-search",
        json={"provider": "openverse", "photo_id": ID, "url": "https://evil.example/x.jpg"},
    )
    assert_problem(response, 422)
    assert fake.requests == []


def test_import_blocked_download_is_502(
    client: TestClient,  # noqa: F811
    recipe: dict,  # noqa: F811
    ov: FakeOpenverse,
) -> None:
    DNS["internal.example"] = ["10.0.0.5"]
    ov.images[ID]["url"] = "https://internal.example/a.jpg"
    ov.thumbs[ID] = httpx2.Response(404)
    assert_problem(from_search(client, recipe["id"]), 502)
    assert client.get(f"{API}/recipes/{recipe['id']}").json()["photo_url"] is None
