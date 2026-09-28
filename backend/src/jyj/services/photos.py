"""Recipe photos: sniff, decode, re-encode to metadata-free WebP, store under PHOTOS_DIR.

Files are written before the database row changes, so the session tracks which files to drop:
the new ones if the transaction rolls back, the replaced ones only once it commits.
"""

import io
import logging
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import pillow_heif
from PIL import ExifTags, Image, ImageOps
from sqlalchemy import event
from sqlalchemy.orm import Session

from jyj.config import get_settings
from jyj.models import Recipe
from jyj.services.errors import InvalidError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)

pillow_heif.register_heif_opener()

MAX_PIXELS = 50_000_000
# Pillow raises DecompressionBombError past twice this; MAX_PIXELS is checked explicitly first.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

FULL_EDGE = 1600
THUMB_EDGE = 400
FULL_QUALITY = 80
THUMB_QUALITY = 75
READ_CHUNK = 64 * 1024
LOCK_TIMEOUT_SECONDS = 30.0
MEDIA_PREFIX = "/media/"

_NAME = re.compile(r"^[0-9a-f]{32}\.webp$")
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_PILLOW_FORMAT = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "heif": "HEIF"}

# One decode at a time keeps peak memory bounded on the Pi.
_processing = threading.Lock()

_ON_COMMIT = "jyj_photos_on_commit"
_ON_ROLLBACK = "jyj_photos_on_rollback"


class PhotoTooLarge(ServiceError):
    status = 413


class PhotoBusy(ServiceError):
    status = 503


@dataclass(frozen=True, slots=True)
class ProcessedPhoto:
    full: bytes
    thumb: bytes
    width: int
    height: int


def photos_dir() -> Path:
    return get_settings().photos_dir


def max_bytes() -> int:
    return get_settings().photo_max_bytes


def photo_url(name: str | None) -> str | None:
    return MEDIA_PREFIX + name if name else None


def thumb_url(name: str | None) -> str | None:
    return MEDIA_PREFIX + thumb_name(name) if name else None


def thumb_name(name: str) -> str:
    return name.removesuffix(".webp") + "_thumb.webp"


def read_capped(stream: BinaryIO, limit: int) -> bytes:
    buf = bytearray()
    while chunk := stream.read(READ_CHUNK):
        buf += chunk
        if len(buf) > limit:
            raise PhotoTooLarge(f"photo must be at most {limit // (1024 * 1024)} MiB")
    return bytes(buf)


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "heif"
    return None


def process(data: bytes) -> ProcessedPhoto:
    kind = sniff(data)
    if kind is None:
        raise InvalidError("unsupported image type; use JPEG, PNG, WebP or HEIC")
    if not _processing.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise PhotoBusy("another photo is being processed; try again")
    try:
        return _process(data, kind)
    finally:
        _processing.release()


def _process(data: bytes, kind: str) -> ProcessedPhoto:
    try:
        with Image.open(io.BytesIO(data), formats=[_PILLOW_FORMAT[kind]]) as img:
            if img.width * img.height > MAX_PIXELS:
                raise InvalidError(f"image is too large; at most {MAX_PIXELS // 1_000_000} MP")
            if kind == "jpeg":
                img.draft("RGB", (FULL_EDGE, FULL_EDGE))
            img.load()
            full = _normalise(img)
    except ServiceError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidError("image could not be decoded") from exc

    thumb = full.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    return ProcessedPhoto(
        full=_encode(full, FULL_QUALITY),
        thumb=_encode(thumb, THUMB_QUALITY),
        width=full.width,
        height=full.height,
    )


def _normalise(img: Image.Image) -> Image.Image:
    """Shrink, apply EXIF orientation, and return a copy with no metadata attached."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    mode = "RGBA" if has_alpha else "RGB"
    orientation = img.getexif().get(ExifTags.Base.Orientation)
    work = img if img.mode == mode else img.convert(mode)
    work.thumbnail((FULL_EDGE, FULL_EDGE), Image.Resampling.LANCZOS)
    if orientation:
        work.getexif()[ExifTags.Base.Orientation] = orientation
        work = ImageOps.exif_transpose(work)
    # A fresh image carries pixels only: no EXIF, XMP, ICC or text chunks.
    clean = Image.new(mode, work.size)
    clean.paste(work)
    return clean


def _encode(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    img.save(out, "WEBP", quality=quality, method=4)
    return out.getvalue()


def store(processed: ProcessedPhoto, directory: Path | None = None) -> str:
    directory = directory or photos_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.webp"
    _write_atomic(directory / thumb_name(name), processed.thumb)
    _write_atomic(directory / name, processed.full)
    return name


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)  # noqa: S103 - Caddy serves these read-only from a shared volume
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove(name: str, directory: Path | None = None) -> None:
    if not _NAME.fullmatch(name):
        logger.warning("refusing to remove unexpected photo name")
        return
    directory = directory or photos_dir()
    for file in (name, thumb_name(name)):
        try:
            (directory / file).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove photo file", exc_info=True)


def remove_after_commit(db: Session, name: str | None) -> None:
    if name:
        db.info.setdefault(_ON_COMMIT, []).append((name, photos_dir()))


def _remove_after_rollback(db: Session, name: str) -> None:
    db.info.setdefault(_ON_ROLLBACK, []).append((name, photos_dir()))


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    for name, directory in db.info.pop(_ON_COMMIT, []):
        remove(name, directory)
    db.info.pop(_ON_ROLLBACK, None)


@event.listens_for(Session, "after_rollback")
def _after_rollback(db: Session) -> None:
    for name, directory in db.info.pop(_ON_ROLLBACK, []):
        remove(name, directory)
    db.info.pop(_ON_COMMIT, None)


def _get_recipe(db: Session, recipe_id: int) -> Recipe:
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise NotFoundError(f"recipe {recipe_id} not found")
    return recipe


def set_recipe_photo(
    db: Session, recipe_id: int, data: bytes, credit: dict[str, Any] | None = None
) -> Recipe:
    """Replace the photo; ``credit`` is the new photo's attribution, None for own uploads."""
    recipe = _get_recipe(db, recipe_id)
    processed = process(data)
    name = store(processed)
    _remove_after_rollback(db, name)
    remove_after_commit(db, recipe.photo_path)
    recipe.photo_path = name
    recipe.photo_credit = credit
    db.flush()
    db.refresh(recipe)
    return recipe


def clear_recipe_photo(db: Session, recipe_id: int) -> Recipe:
    recipe = _get_recipe(db, recipe_id)
    remove_after_commit(db, recipe.photo_path)
    recipe.photo_path = None
    recipe.photo_credit = None
    db.flush()
    db.refresh(recipe)
    return recipe
