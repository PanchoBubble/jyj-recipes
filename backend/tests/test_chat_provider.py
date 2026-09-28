import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from jyj.chat import provider as provider_module
from jyj.chat.provider import (
    CodexProvider,
    ProviderCancelled,
    ProviderNotAuthenticated,
    ProviderProtocolError,
    ProviderTimeout,
    ProviderUnavailable,
)

FAKE_SOURCE = Path(__file__).parent / "fixtures" / "fake_codex.py"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply"],
    "properties": {"reply": {"type": "string"}},
}
ANSWER = {"reply": "hi", "actions": [], "needs": []}


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "bin" / "codex"
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + FAKE_SOURCE.read_text())
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CODEX_ARGV_LOG", str(tmp_path / "argv.jsonl"))
    monkeypatch.setenv("FAKE_CODEX_LOGGED_IN", "1")
    return binary


def calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / "argv.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def exec_calls(tmp_path: Path) -> list[dict]:
    return [c for c in calls(tmp_path) if c["argv"][:1] == ["exec"]]


def make(binary: Path, **kwargs) -> CodexProvider:
    return CodexProvider(binary=str(binary), **kwargs)


@pytest.mark.parametrize("mode", ["new", "old", "deltas_only"])
def test_complete_parses_final_message_from_either_event_schema(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    monkeypatch.setenv("FAKE_CODEX_PROMPT_LOG", str(tmp_path / "prompt.txt"))

    result = make(fake_codex).complete("plan dinner", SCHEMA, timeout=10)

    assert result == ANSWER
    assert (tmp_path / "prompt.txt").read_text() == "plan dinner"


def test_argv_is_locked_down_and_schema_is_written_to_the_run_dir(
    fake_codex: Path, tmp_path: Path
) -> None:
    make(fake_codex, model="gpt-test").complete("x", SCHEMA, timeout=10)

    (call,) = exec_calls(tmp_path)
    argv = call["argv"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv.count("--sandbox") == 1
    assert not any("dangerously" in arg for arg in argv)
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "gpt-test"
    assert argv[-1] == "-"
    assert call["schema"] == SCHEMA
    workdir = Path(argv[argv.index("--cd") + 1])
    schema_path = Path(argv[argv.index("--output-schema") + 1])
    assert Path(call["cwd"]).resolve() == workdir.resolve()
    assert schema_path.parent != workdir
    assert not workdir.exists()
    assert not schema_path.exists()


def test_model_flag_is_omitted_by_default(fake_codex: Path, tmp_path: Path) -> None:
    make(fake_codex).complete("x", SCHEMA, timeout=10)

    (call,) = exec_calls(tmp_path)
    assert "--model" not in call["argv"]


def test_error_event_raises_protocol_error_without_waiting_for_exit(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "error")
    started = time.monotonic()

    with pytest.raises(ProviderProtocolError, match="usage limit reached"):
        make(fake_codex).complete("x", SCHEMA, timeout=20)

    assert time.monotonic() - started < 10


def test_non_zero_exit_includes_stderr_tail(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "exit")

    with pytest.raises(ProviderProtocolError, match=r"exited with 3: boom: something broke"):
        make(fake_codex).complete("x", SCHEMA, timeout=10)


def test_non_zero_exit_when_logged_out_raises_not_authenticated(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "exit")
    monkeypatch.setenv("FAKE_CODEX_LOGGED_IN", "0")

    with pytest.raises(ProviderNotAuthenticated):
        make(fake_codex).complete("x", SCHEMA, timeout=10)


def _assert_dead(pid_file: Path) -> None:
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_timeout_kills_the_process(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "hang")
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_CODEX_PID_FILE", str(pid_file))
    started = time.monotonic()

    with pytest.raises(ProviderTimeout):
        make(fake_codex).complete("x", SCHEMA, timeout=1)

    assert time.monotonic() - started < 6
    _assert_dead(pid_file)


def test_cancel_stops_a_running_process(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "hang")
    pid_file = tmp_path / "pid"
    monkeypatch.setenv("FAKE_CODEX_PID_FILE", str(pid_file))
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()

    with pytest.raises(ProviderCancelled):
        make(fake_codex).complete("x", SCHEMA, timeout=30, cancel=cancel)

    _assert_dead(pid_file)


def test_cancel_before_dispatch_never_spawns(fake_codex: Path, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ProviderCancelled):
        make(fake_codex).complete("x", SCHEMA, cancel=cancel)

    assert exec_calls(tmp_path) == []


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("invalid_json", "not valid JSON"),
        ("array", "not a JSON object"),
        ("no_completion", "without a completion event"),
    ],
)
def test_unusable_final_message_raises_protocol_error(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch, mode: str, message: str
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)

    with pytest.raises(ProviderProtocolError, match=message):
        make(fake_codex).complete("x", SCHEMA, timeout=10)


def test_only_one_run_at_a_time_and_queued_callers_time_out(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "hang")
    monkeypatch.setenv("FAKE_CODEX_PID_FILE", str(tmp_path / "pid"))
    provider = make(fake_codex)
    cancel_first = threading.Event()
    errors: list[Exception] = []

    def first() -> None:
        try:
            provider.complete("x", SCHEMA, timeout=30, cancel=cancel_first)
        except Exception as error:
            errors.append(error)

    runner = threading.Thread(target=first)
    runner.start()
    while not (tmp_path / "pid").exists():
        time.sleep(0.05)

    with pytest.raises(ProviderTimeout, match="run slot"):
        provider.complete("y", SCHEMA, timeout=0.5)

    cancel_first.set()
    runner.join(timeout=10)
    assert isinstance(errors[0], ProviderCancelled)
    assert len(exec_calls(tmp_path)) == 1


def test_disabled_provider_refuses_to_run(fake_codex: Path, tmp_path: Path) -> None:
    provider = make(fake_codex, enabled=False)

    with pytest.raises(ProviderUnavailable):
        provider.complete("x", SCHEMA)
    assert provider.health().as_dict() == {"available": False, "detail": "disabled"}
    assert calls(tmp_path) == []


def test_missing_binary(tmp_path: Path) -> None:
    provider = CodexProvider(binary=str(tmp_path / "nope"))

    assert provider.health().detail == "binary_not_found"
    with pytest.raises(ProviderUnavailable):
        provider.complete("x", SCHEMA)


def test_health_ready_and_cached(fake_codex: Path, tmp_path: Path) -> None:
    provider = make(fake_codex)

    assert provider.health().as_dict() == {"available": True, "detail": "ready"}
    assert provider.health().detail == "ready"
    assert [c["argv"] for c in calls(tmp_path)] == [["login", "status"]]


def test_health_not_authenticated(fake_codex: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOGGED_IN", "0")

    assert make(fake_codex).health().as_dict() == {
        "available": False,
        "detail": "not_authenticated",
    }


def test_health_login_status_timeout(fake_codex: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_LOGIN_HANG", "1")
    monkeypatch.setattr(provider_module, "_LOGIN_STATUS_TIMEOUT_SECONDS", 0.5)

    assert make(fake_codex).health().detail == "unavailable:login_status_timeout"


def test_subprocess_env_sets_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "/var/lib/codex")

    env = CodexProvider._environment()

    assert env["NO_COLOR"] == "1"
    assert env["CODEX_HOME"] == "/var/lib/codex"


def test_provider_source_never_references_codex_credentials() -> None:
    source = Path(provider_module.__file__).read_text()

    assert "auth.json" not in source
    assert "dangerously" not in source
