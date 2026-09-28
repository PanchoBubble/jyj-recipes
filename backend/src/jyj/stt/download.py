"""Download a multilingual whisper.cpp ggml model and verify its pinned SHA-256.

Usage: python -m jyj.stt.download [tiny|base|small|turbo] [--dir DIR]
"""

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jyj.config import get_settings

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Model:
    filename: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.filename}"


MODELS: dict[str, Model] = {
    "tiny": Model(
        "ggml-tiny.bin",
        "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21",
        77691713,
    ),
    "base": Model(
        "ggml-base.bin",
        "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        147951465,
    ),
    "small": Model(
        "ggml-small.bin",
        "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
        487601967,
    ),
    "turbo": Model(
        "ggml-large-v3-turbo-q5_0.bin",
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
        574041195,
    ),
}


class ChecksumMismatch(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download(name: str, directory: Path, *, url: str | None = None) -> Path:
    model = MODELS[name]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / model.filename
    if target.is_file() and sha256_file(target) == model.sha256:
        return target

    source = url or model.url
    if not source.startswith(("https://", "file://")):
        raise ValueError("model URL must be https")
    fd, partial_name = tempfile.mkstemp(prefix=f".{model.filename}.", dir=directory)
    partial = Path(partial_name)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(source, timeout=60) as response:  # noqa: S310
            while chunk := response.read(CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != model.sha256:
            raise ChecksumMismatch(f"{model.filename} failed SHA-256 verification")
        partial.chmod(0o644)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jyj.stt.download")
    parser.add_argument("model", nargs="?", default="small", choices=sorted(MODELS))
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="target directory (default: the directory of WHISPER_MODEL_PATH)",
    )
    args = parser.parse_args(argv)
    directory = args.dir or get_settings().whisper_model_path.parent
    try:
        path = download(args.model, directory)
    except (OSError, ChecksumMismatch) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"{path} ok (sha256 verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
