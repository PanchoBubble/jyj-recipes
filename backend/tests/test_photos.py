import io
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.config import get_settings
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import Recipe, User
from jyj.services import auth as auth_service
from jyj.services import photos
from jyj.services import recipes as recipes_service

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1"
MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def photos_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    directory = tmp_path / "photos"
    monkeypatch.setenv("PHOTOS_DIR", str(directory))
    get_settings.cache_clear()
    yield directory
    get_settings.cache_clear()


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
def anon(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
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


def image_bytes(fmt: str, size: tuple[int, int] = (2400, 1200), mode: str = "RGB", **save) -> bytes:
    img = Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else "red")
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def upload(client: TestClient, recipe_id: int, data: bytes, **kwargs):
    filename = kwargs.pop("filename", "photo.jpg")
    content_type = kwargs.pop("content_type", "image/jpeg")
    return client.put(
        f"{API}/recipes/{recipe_id}/photo",
        files={"file": (filename, data, content_type)},
        **kwargs,
    )


def assert_problem(response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    return response.json()


def stored(photos_dir: Path, url: str) -> Path:
    assert url.startswith("/media/")
    return photos_dir / url.removeprefix("/media/")


def open_stored(path: Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img


@pytest.mark.parametrize(
    ("fmt", "mode"),
    [("JPEG", "RGB"), ("PNG", "RGB"), ("PNG", "RGBA"), ("WEBP", "RGB")],
)
def test_upload_valid_image_is_resized_to_webp(
    client: TestClient, recipe: dict, photos_dir: Path, fmt: str, mode: str
) -> None:
    response = upload(client, recipe["id"], image_bytes(fmt, mode=mode))

    assert response.status_code == 200, response.text
    body = response.json()
    full, thumb = stored(photos_dir, body["photo_url"]), stored(photos_dir, body["photo_thumb_url"])
    assert thumb.name == full.name.removesuffix(".webp") + "_thumb.webp"
    with open_stored(full) as img:
        assert img.format == "WEBP"
        assert img.size == (1600, 800)
        assert img.mode == mode
    with open_stored(thumb) as img:
        assert img.format == "WEBP"
        assert img.size == (400, 200)
    for path in (full, thumb):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert not list(photos_dir.glob(".upload-*"))

    fetched = client.get(f"{API}/recipes/{recipe['id']}").json()
    assert fetched["photo_url"] == body["photo_url"]
    assert fetched["photo_thumb_url"] == body["photo_thumb_url"]
    listed = client.get(f"{API}/recipes").json()["items"][0]
    assert listed["photo_url"] == body["photo_url"]
    assert "photo_path" not in listed


def test_small_image_is_not_upscaled(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    body = upload(client, recipe["id"], image_bytes("PNG", size=(300, 200))).json()
    with open_stored(stored(photos_dir, body["photo_url"])) as img:
        assert img.size == (300, 200)


def test_heic_upload(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    try:
        data = image_bytes("HEIF", size=(800, 600))
    except (KeyError, OSError, ValueError) as exc:
        pytest.skip(f"no HEIF encoder available: {exc}")
    response = upload(client, recipe["id"], data, filename="IMG_0001.HEIC")
    assert response.status_code == 200, response.text
    with open_stored(stored(photos_dir, response.json()["photo_url"])) as img:
        assert img.size == (800, 600)


def test_exif_gps_is_removed_and_orientation_applied(
    client: TestClient, recipe: dict, photos_dir: Path
) -> None:
    img = Image.new("RGB", (200, 100), "blue")
    img.paste("red", (0, 0, 100, 100))
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.Make] = "Phone"
    exif.get_ifd(ExifTags.IFD.GPSInfo).update(
        {ExifTags.GPS.GPSLatitudeRef: "N", ExifTags.GPS.GPSLatitude: (40.0, 25.0, 0.0)}
    )
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=95)
    with Image.open(io.BytesIO(buf.getvalue())) as check:
        assert check.getexif().get_ifd(ExifTags.IFD.GPSInfo)

    body = upload(client, recipe["id"], buf.getvalue()).json()

    for url in (body["photo_url"], body["photo_thumb_url"]):
        path = stored(photos_dir, url)
        assert b"Exif" not in path.read_bytes()
        with open_stored(path) as out:
            assert not out.getexif()
            assert "exif" not in out.info
            assert "icc_profile" not in out.info
            assert "xmp" not in out.info
    with open_stored(stored(photos_dir, body["photo_url"])) as out:
        # Orientation 6 means rotate 90 degrees clockwise: the red left half ends up on top.
        assert out.size == (100, 200)
        top, bottom = out.getpixel((50, 25)), out.getpixel((50, 175))
        assert top[0] > 200 and top[2] < 60
        assert bottom[2] > 200 and bottom[0] < 60


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (b"not an image at all, just text", "photo.jpg", "image/jpeg"),
        (b"GIF89a" + b"\x00" * 64, "photo.png", "image/png"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "photo.webp", "image/webp"),
        (b"\xff\xd8\xff\xe0" + b"garbage" * 100, "photo.jpg", "image/jpeg"),
        (b"", "photo.jpg", "image/jpeg"),
    ],
    ids=["text", "gif", "svg", "truncated-jpeg", "empty"],
)
def test_spoofed_or_broken_files_are_rejected(
    client: TestClient,
    recipe: dict,
    photos_dir: Path,
    data: bytes,
    filename: str,
    content_type: str,
) -> None:
    response = upload(client, recipe["id"], data, filename=filename, content_type=content_type)
    assert_problem(response, 422)
    assert not photos_dir.exists() or not any(photos_dir.iterdir())


def test_real_image_with_wrong_content_type_is_accepted(client: TestClient, recipe: dict) -> None:
    response = upload(
        client, recipe["id"], image_bytes("PNG"), filename="x.txt", content_type="text/plain"
    )
    assert response.status_code == 200, response.text


def test_oversize_file_is_rejected(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    data = image_bytes("JPEG", size=(64, 64)) + b"\x00" * (10 * MiB)
    assert_problem(upload(client, recipe["id"], data), 413)
    assert not photos_dir.exists() or not any(photos_dir.iterdir())


def test_oversize_body_is_rejected_by_declared_length(client: TestClient, recipe: dict) -> None:
    response = client.put(
        f"{API}/recipes/{recipe['id']}/photo",
        content=b"x" * (11 * MiB),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert_problem(response, 413)


def test_oversize_streamed_body_is_rejected(client: TestClient, recipe: dict) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n'
        for _ in range(12):
            yield b"\x00" * MiB

    response = client.put(
        f"{API}/recipes/{recipe['id']}/photo",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert_problem(response, 413)


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_decompression_bomb_dimensions_are_rejected(
    client: TestClient, recipe: dict, photos_dir: Path
) -> None:
    data = image_bytes("PNG", size=(10_000, 6_000), mode="1")
    assert len(data) < MiB
    body = assert_problem(upload(client, recipe["id"], data), 422)
    assert "too large" in body["detail"]
    assert not photos_dir.exists() or not any(photos_dir.iterdir())


def test_replace_deletes_previous_files(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    first = upload(client, recipe["id"], image_bytes("JPEG")).json()
    old = [stored(photos_dir, first["photo_url"]), stored(photos_dir, first["photo_thumb_url"])]
    assert all(p.exists() for p in old)

    second = upload(client, recipe["id"], image_bytes("PNG")).json()

    assert second["photo_url"] != first["photo_url"]
    assert not any(p.exists() for p in old)
    assert stored(photos_dir, second["photo_url"]).exists()
    assert stored(photos_dir, second["photo_thumb_url"]).exists()
    assert len(list(photos_dir.iterdir())) == 2


def test_delete_photo_endpoint(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    body = upload(client, recipe["id"], image_bytes("JPEG")).json()

    response = client.delete(f"{API}/recipes/{recipe['id']}/photo")

    assert response.status_code == 200, response.text
    assert response.json()["photo_url"] is None
    assert response.json()["photo_thumb_url"] is None
    assert not stored(photos_dir, body["photo_url"]).exists()
    assert not any(photos_dir.iterdir())
    again = client.delete(f"{API}/recipes/{recipe['id']}/photo")
    assert again.status_code == 200


def test_hard_delete_recipe_removes_files(
    client: TestClient, recipe: dict, photos_dir: Path
) -> None:
    upload(client, recipe["id"], image_bytes("JPEG"))
    assert len(list(photos_dir.iterdir())) == 2

    assert client.delete(f"{API}/recipes/{recipe['id']}").status_code == 204

    assert not any(photos_dir.iterdir())


def test_archive_on_delete_keeps_files(
    client: TestClient, recipe: dict, photos_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload(client, recipe["id"], image_bytes("JPEG"))
    monkeypatch.setitem(recipes_service.REFERENCE_CHECKS, "pytest", lambda _db, _id: True)

    response = client.delete(f"{API}/recipes/{recipe['id']}")

    assert response.status_code == 200
    assert response.json()["photo_url"]
    assert len(list(photos_dir.iterdir())) == 2


def test_rollback_removes_new_files_and_keeps_old(
    db: Session, user: User, photos_dir: Path
) -> None:
    recipe = Recipe(name="Soup", created_by=user.id)
    db.add(recipe)
    db.flush()
    photos.set_recipe_photo(db, recipe.id, image_bytes("JPEG"))
    db.commit()
    kept = set(photos_dir.iterdir())

    photos.set_recipe_photo(db, recipe.id, image_bytes("PNG"))
    assert len(list(photos_dir.iterdir())) == 4
    db.rollback()

    assert set(photos_dir.iterdir()) == kept


def test_unknown_recipe_is_404(client: TestClient, photos_dir: Path) -> None:
    assert_problem(upload(client, 999_999, image_bytes("JPEG")), 404)
    assert_problem(client.delete(f"{API}/recipes/999_999/photo"), 404)
    assert not photos_dir.exists()


def test_missing_file_field_is_422(client: TestClient, recipe: dict) -> None:
    response = client.put(f"{API}/recipes/{recipe['id']}/photo", files={"other": ("a", b"x")})
    assert_problem(response, 422)


def test_requires_login(anon: TestClient, recipe: dict) -> None:
    anon.cookies.clear()
    response = upload(anon, recipe["id"], image_bytes("JPEG"), headers=CSRF)
    assert_problem(response, 401)
    assert_problem(anon.delete(f"{API}/recipes/{recipe['id']}/photo", headers=CSRF), 401)


def test_requires_csrf_header(client: TestClient, recipe: dict, photos_dir: Path) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(upload(client, recipe["id"], image_bytes("JPEG")), 403)
    assert_problem(client.delete(f"{API}/recipes/{recipe['id']}/photo"), 403)
    assert not photos_dir.exists()


@pytest.mark.parametrize("name", ["../etc/passwd", "abc.webp", "a" * 32 + ".png", ""])
def test_remove_ignores_unexpected_names(tmp_path: Path, name: str) -> None:
    victim = tmp_path / "keep.webp"
    victim.write_bytes(b"x")
    photos.remove(name, tmp_path)
    assert victim.exists()
