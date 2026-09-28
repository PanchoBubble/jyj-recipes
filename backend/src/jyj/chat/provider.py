"""Codex CLI provider: shells out to `codex exec` and returns its structured answer.

The CLI owns its credentials under CODEX_HOME. This module only runs the binary and
reads its JSONL event stream; it never opens, copies or parses anything the CLI stores.
Every run is pinned to a read-only sandbox in a throwaway empty directory, ignores the
user config (so no MCP servers, hooks or custom providers load) and persists no session.
"""

from __future__ import annotations

import json
import logging
import os
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

logger = logging.getLogger(__name__)

_DELTA_EVENTS = frozenset({"agent_message_delta", "item.delta", "response.output_text.delta"})
_ERROR_EVENTS = frozenset({"error", "stream_error", "turn.failed"})
_COMPLETION_EVENTS = frozenset({"task_complete", "turn.completed"})
_POLL_SECONDS = 0.05
_STDERR_TAIL_LINES = 20
_TERMINATE_GRACE_SECONDS = 2.0
_LOGIN_STATUS_TIMEOUT_SECONDS = 5.0
_HEALTH_CACHE_SECONDS = 30.0
_QUEUE_POLL_SECONDS = 0.1


class ProviderError(RuntimeError):
    """Bounded provider failure; the message is safe for logs, not for end users."""


class ProviderUnavailable(ProviderError):
    """Codex is disabled, not installed, or could not be started."""


class ProviderNotAuthenticated(ProviderError):
    """The CLI is installed but nobody has run `codex login` in CODEX_HOME."""


class ProviderTimeout(ProviderError):
    """The run, or the wait for the single run slot, exceeded its timeout."""


class ProviderCancelled(ProviderError):
    """The caller cancelled the request."""


class ProviderProtocolError(ProviderError):
    """The CLI exited or streamed something we cannot turn into a JSON object."""


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "detail": self.detail}


def _decode_event(payload: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Normalise both Codex event schemas to (type, body).

    Older builds wrap events as {"id": ..., "msg": {"type": ...}}; newer ones put the type
    at the top level.
    """
    body = payload.get("msg")
    if isinstance(body, Mapping):
        kind = body.get("type")
        return (kind if isinstance(kind, str) else ""), body
    kind = payload.get("type")
    return (kind if isinstance(kind, str) else ""), payload


def _decode_line(raw: bytes) -> tuple[str, Mapping[str, Any]] | None:
    line = raw.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return _decode_event(payload)


def _delta(kind: str, body: Mapping[str, Any]) -> str:
    if kind not in _DELTA_EVENTS:
        return ""
    for key in ("delta", "text"):
        value = body.get(key)
        if isinstance(value, str):
            return value
    return ""


def _final_message(kind: str, body: Mapping[str, Any]) -> str:
    if kind == "agent_message":
        value = body.get("message")
        return value if isinstance(value, str) else ""
    if kind == "item.completed":
        item = body.get("item")
        if isinstance(item, Mapping) and item.get("type") == "agent_message":
            value = item.get("text")
            return value if isinstance(value, str) else ""
        return ""
    if kind in _COMPLETION_EVENTS:
        value = body.get("last_agent_message")
        return value if isinstance(value, str) else ""
    return ""


def _reason(body: Mapping[str, Any]) -> str:
    for key in ("message", "error", "reason"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value[:200]
        if isinstance(value, Mapping):
            nested = value.get("message")
            if isinstance(nested, str) and nested:
                return nested[:200]
    return "no detail"


class _StderrTail:
    """Drain stderr in the background so a full pipe cannot wedge the CLI."""

    def __init__(self, stream: IO[bytes] | None) -> None:
        self._lines: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._thread = threading.Thread(
            target=self._drain, args=(stream,), name="jyj-codex-stderr", daemon=True
        )
        self._thread.start()

    def _drain(self, stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        try:
            for raw_line in stream:
                line = raw_line.decode("utf-8", "replace").strip()
                if line:
                    self._lines.append(line)
        except (OSError, ValueError):
            return

    def text(self) -> str:
        self._thread.join(timeout=_TERMINATE_GRACE_SECONDS)
        return " | ".join(self._lines)[:240] or "no stderr output"


class CodexProvider:
    """Run the signed-in Codex CLI, one process at a time."""

    def __init__(
        self,
        binary: str = "codex",
        model: str | None = None,
        timeout: float = 90.0,
        enabled: bool = True,
    ) -> None:
        self.binary = binary
        self.model = model or None
        self.timeout = timeout
        self.enabled = enabled
        self._slot = threading.Semaphore(1)
        self._health_lock = threading.Lock()
        self._health_cache: tuple[float, ProviderHealth] | None = None

    def health(self) -> ProviderHealth:
        with self._health_lock:
            now = time.monotonic()
            if self._health_cache and now - self._health_cache[0] < _HEALTH_CACHE_SECONDS:
                return self._health_cache[1]
        return self._refresh_health()

    def _refresh_health(self) -> ProviderHealth:
        with self._health_lock:
            result = self._check_health()
            self._health_cache = (time.monotonic(), result)
            return result

    def _check_health(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth(False, "disabled")
        executable = self._executable()
        if executable is None:
            return ProviderHealth(False, "binary_not_found")
        try:
            completed = subprocess.run(  # noqa: S603
                [executable, "login", "status"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_LOGIN_STATUS_TIMEOUT_SECONDS,
                env=self._environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProviderHealth(False, "unavailable:login_status_timeout")
        except OSError as error:
            return ProviderHealth(False, f"unavailable:{type(error).__name__}")
        if completed.returncode != 0:
            return ProviderHealth(False, "not_authenticated")
        return ProviderHealth(True, "ready")

    def complete(
        self,
        prompt: str,
        output_schema: Mapping[str, Any],
        *,
        timeout: float | None = None,
        cancel: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Run one `codex exec` turn and return the final message parsed as a JSON object.

        Waiting for the run slot and the run itself are each bounded by `timeout`.
        """
        if not self.enabled:
            raise ProviderUnavailable("codex provider is disabled")
        limit = self.timeout if timeout is None else timeout
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise ProviderCancelled("request cancelled before dispatch")
        executable = self._executable()
        if executable is None:
            raise ProviderUnavailable("codex binary not found")
        self._acquire_slot(limit, cancel)
        try:
            with tempfile.TemporaryDirectory(prefix="jyj-codex-") as root:
                workdir = Path(root) / "work"
                workdir.mkdir()
                schema_path = Path(root) / "schema.json"
                schema_path.write_text(json.dumps(output_schema), encoding="utf-8")
                argv = self.build_argv(executable, workdir, schema_path)
                text = self._run(argv, workdir, prompt, limit, cancel)
        finally:
            self._slot.release()
        return self._parse(text)

    def build_argv(self, executable: str, workdir: Path, schema_path: Path) -> list[str]:
        argv = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
            "--cd",
            str(workdir),
            "--output-schema",
            str(schema_path),
        ]
        if self.model:
            argv.extend(["--model", self.model])
        argv.append("-")
        return argv

    def _acquire_slot(self, limit: float, cancel: threading.Event) -> None:
        deadline = time.monotonic() + limit
        while not self._slot.acquire(timeout=_QUEUE_POLL_SECONDS):
            if cancel.is_set():
                raise ProviderCancelled("request cancelled while queued")
            if time.monotonic() >= deadline:
                raise ProviderTimeout("timed out waiting for the codex run slot")
        if cancel.is_set():
            self._slot.release()
            raise ProviderCancelled("request cancelled while queued")

    def _run(
        self,
        argv: list[str],
        workdir: Path,
        prompt: str,
        limit: float,
        cancel: threading.Event,
    ) -> str:
        logger.debug("codex prompt: %s", prompt)
        try:
            process = subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env=self._environment(),
            )
        except OSError as error:
            raise ProviderUnavailable(
                f"codex exec failed to start: {type(error).__name__}"
            ) from error
        stderr = _StderrTail(process.stderr)
        try:
            self._send_prompt(process, prompt)
            return self._consume(process, stderr, time.monotonic() + limit, cancel)
        finally:
            self._stop(process)

    @staticmethod
    def _send_prompt(process: subprocess.Popen[bytes], prompt: str) -> None:
        if process.stdin is None:
            raise ProviderProtocolError("codex exec has no stdin")
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
        except OSError as error:
            raise ProviderProtocolError(
                f"codex exec rejected the prompt: {type(error).__name__}"
            ) from error

    def _consume(
        self,
        process: subprocess.Popen[bytes],
        stderr: _StderrTail,
        deadline: float,
        cancel: threading.Event,
    ) -> str:
        stdout = process.stdout
        if stdout is None:
            raise ProviderProtocolError("codex exec has no stdout")
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        buffered = b""
        deltas: list[str] = []
        final_text = ""
        completed = False
        try:
            while True:
                if cancel.is_set():
                    raise ProviderCancelled("request cancelled")
                if time.monotonic() > deadline:
                    raise ProviderTimeout("codex exec exceeded its timeout")
                if not selector.select(timeout=_POLL_SECONDS):
                    continue
                data = os.read(stdout.fileno(), 65_536)
                if not data:
                    break
                buffered += data
                while b"\n" in buffered:
                    raw, buffered = buffered.split(b"\n", 1)
                    event = _decode_line(raw)
                    if event is None:
                        continue
                    kind, body = event
                    text = _delta(kind, body)
                    if text:
                        deltas.append(text)
                        continue
                    if kind in _ERROR_EVENTS:
                        raise ProviderProtocolError(f"codex exec reported {kind}: {_reason(body)}")
                    message = _final_message(kind, body)
                    if message:
                        final_text = message
                    if kind in _COMPLETION_EVENTS:
                        completed = True
        finally:
            selector.close()
        returncode = self._wait(process)
        if returncode != 0:
            tail = stderr.text()
            if self._refresh_health().detail == "not_authenticated":
                raise ProviderNotAuthenticated("codex CLI is not logged in")
            raise ProviderProtocolError(f"codex exec exited with {returncode}: {tail}")
        if not completed:
            raise ProviderProtocolError("codex exec ended without a completion event")
        answer = final_text or "".join(deltas)
        if not answer:
            raise ProviderProtocolError("codex exec produced no answer")
        logger.debug("codex answer: %s", answer)
        return answer

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        try:
            parsed = json.loads(text)
        except ValueError as error:
            raise ProviderProtocolError("codex final message is not valid JSON") from error
        if not isinstance(parsed, dict):
            raise ProviderProtocolError("codex final message is not a JSON object")
        return parsed

    @staticmethod
    def _wait(process: subprocess.Popen[bytes]) -> int:
        try:
            return process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(timeout=_TERMINATE_GRACE_SECONDS)

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    continue

    def _executable(self) -> str | None:
        candidate = self.binary.strip()
        if not candidate:
            return None
        if os.path.sep in candidate:
            return candidate if os.access(candidate, os.X_OK) else None
        return shutil.which(candidate)

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = dict(os.environ)
        environment["NO_COLOR"] = "1"
        return environment
