# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM python:${PYTHON_VERSION}-slim AS native-build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates nasm \
    && rm -rf /var/lib/apt/lists/*

# Debian's ffmpeg package adds ~420 MB; this build only carries what the STT service accepts.
FROM native-build AS ffmpeg
ARG FFMPEG_VERSION=n8.0.3
RUN git clone --branch "$FFMPEG_VERSION" --depth 1 https://git.ffmpeg.org/ffmpeg.git /src
WORKDIR /src
RUN ./configure --prefix=/opt/ffmpeg --disable-everything --disable-autodetect \
        --disable-doc --disable-debug --disable-network --disable-ffplay --disable-ffprobe \
        --enable-static --disable-shared \
        --enable-protocol=file,pipe \
        --enable-demuxer=matroska,mov,ogg,wav,mp3,aac \
        --enable-parser=opus,aac,vorbis,mpegaudio \
        --enable-decoder=opus,aac,vorbis,mp3,mp3float,pcm_s16le,pcm_f32le \
        --enable-encoder=pcm_s16le --enable-muxer=wav \
        --enable-filter=aresample,aformat,anull,atrim \
        --enable-swresample \
    && make -j"$(nproc)" \
    && make install \
    && strip /opt/ffmpeg/bin/ffmpeg

FROM native-build AS whisper
ARG WHISPER_VERSION=v1.9.1
ARG TARGETARCH
RUN git clone --branch "$WHISPER_VERSION" --depth 1 https://github.com/ggml-org/whisper.cpp.git /src
# GGML_NATIVE would tune for the build host (e.g. an M-series Mac) and SIGILL on the Pi 5's A76.
RUN case "$TARGETARCH" in \
        arm64) arch_flags="-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16" ;; \
        *) arch_flags="" ;; \
    esac \
    && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF $arch_flags \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DWHISPER_SDL2=OFF \
    && cmake --build /build --config Release --target whisper-cli -j"$(nproc)" \
    && strip /build/bin/whisper-cli \
    && /build/bin/whisper-cli --help >/dev/null

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first so source edits don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Codex CLI: the official static musl build from github.com/openai/codex releases.
# To bump: set CODEX_VERSION to the new rust-v* tag and copy the sha256 digests of
# codex-{aarch64,x86_64}-unknown-linux-musl.tar.gz from the release page, or run
#   gh release view rust-v<version> --repo openai/codex --json assets \
#     -q '.assets[]|select(.name|test("^codex-(aarch64|x86_64)-unknown-linux-musl.tar.gz$"))|.name+" "+.digest'
FROM python:${PYTHON_VERSION}-slim AS codex
ARG TARGETARCH
ARG CODEX_VERSION=0.158.0
ARG CODEX_SHA256_ARM64=91e3fafe5ff845a8f685a05d04919146fb30fdc9245af1a42a2f7f239959e90c
ARG CODEX_SHA256_AMD64=af9f5aa6e6662accf9d707cef0d9ca083880a173a9c2b6c22947edb7830e5778
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) triple=aarch64-unknown-linux-musl; sha="$CODEX_SHA256_ARM64" ;; \
      amd64) triple=x86_64-unknown-linux-musl; sha="$CODEX_SHA256_AMD64" ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${triple}.tar.gz"; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/codex.tar.gz')" "$url"; \
    echo "${sha}  /tmp/codex.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/codex.tar.gz -C /tmp; \
    install -m 0755 "/tmp/codex-${triple}" /usr/local/bin/codex; \
    rm -f /tmp/codex.tar.gz "/tmp/codex-${triple}"

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHOTOS_DIR=/var/lib/jyj/photos \
    CODEX_HOME=/var/lib/codex \
    WHISPER_MODEL_DIR=/var/lib/jyj/whisper \
    FORWARDED_ALLOW_IPS=127.0.0.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=whisper /build/bin/whisper-cli /usr/local/bin/whisper-cli
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p "$PHOTOS_DIR" "$CODEX_HOME" "$WHISPER_MODEL_DIR" \
    && chown app:app "$PHOTOS_DIR" "$CODEX_HOME" "$WHISPER_MODEL_DIR"
WORKDIR /app
COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "jyj.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
