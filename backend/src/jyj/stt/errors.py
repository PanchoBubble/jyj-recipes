class SttError(RuntimeError):
    """Base class for transcription failures. Messages never contain transcript text."""


class SttUnavailable(SttError):
    """whisper-cli, ffmpeg or the model file is missing."""


class SttTooLarge(SttError):
    """Upload exceeds the byte limit."""


class SttTooLong(SttError):
    """Decoded audio exceeds the duration limit."""


class SttBadAudio(SttError):
    """Unsupported MIME type, empty upload, or audio ffmpeg cannot decode."""


class SttTimeout(SttError):
    """ffmpeg or whisper-cli exceeded the timeout."""


class SttFailed(SttError):
    """whisper-cli ran but did not produce a usable result."""
