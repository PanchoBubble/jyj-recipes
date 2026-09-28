"""Stand-in for the codex CLI. Behaviour comes from FAKE_CODEX_* env vars."""

import json
import os
import sys
import time
from pathlib import Path

ANSWER = {"reply": "hi", "actions": [], "needs": []}


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def record() -> None:
    log = os.environ.get("FAKE_CODEX_ARGV_LOG")
    if not log:
        return
    args = sys.argv[1:]
    schema = None
    if "--output-schema" in args:
        schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": args, "schema": schema, "cwd": os.getcwd()}) + "\n")


def login_status() -> int:
    if os.environ.get("FAKE_CODEX_LOGGED_IN") == "1":
        print("Logged in using ChatGPT")
        return 0
    print("Not logged in", file=sys.stderr)
    return 1


def run_exec(mode: str) -> int:
    prompt = sys.stdin.read()
    if os.environ.get("FAKE_CODEX_PROMPT_LOG"):
        Path(os.environ["FAKE_CODEX_PROMPT_LOG"]).write_text(prompt)
    print("banner line that is not json")
    if mode == "new":
        emit({"type": "thread.started", "thread_id": "t1"})
        emit({"type": "turn.started"})
        emit({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}})
        item = {"type": "agent_message", "text": json.dumps(ANSWER)}
        emit({"type": "item.completed", "item": item})
        emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    elif mode == "old":
        emit({"id": "0", "msg": {"type": "task_started"}})
        text = json.dumps(ANSWER)
        for index in range(0, len(text), 7):
            chunk = text[index : index + 7]
            emit({"id": "0", "msg": {"type": "agent_message_delta", "delta": chunk}})
        emit({"id": "0", "msg": {"type": "agent_message", "message": text}})
        emit({"id": "0", "msg": {"type": "task_complete", "last_agent_message": text}})
    elif mode == "deltas_only":
        text = json.dumps(ANSWER)
        emit({"type": "turn.started"})
        emit({"type": "item.delta", "delta": text[:5]})
        emit({"type": "item.delta", "delta": text[5:]})
        emit({"type": "turn.completed"})
    elif mode == "error":
        emit({"type": "turn.started"})
        emit({"type": "error", "message": "usage limit reached"})
        time.sleep(30)
    elif mode == "exit":
        print("boom: something broke", file=sys.stderr)
        return 3
    elif mode == "hang":
        pid_file = os.environ.get("FAKE_CODEX_PID_FILE")
        if pid_file:
            Path(pid_file).write_text(str(os.getpid()))
        emit({"type": "turn.started"})
        time.sleep(60)
    elif mode == "invalid_json":
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "not json"}})
        emit({"type": "turn.completed"})
    elif mode == "array":
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "[1, 2]"}})
        emit({"type": "turn.completed"})
    elif mode == "no_completion":
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})
    return 0


def main() -> int:
    record()
    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        print("codex-cli 0.0.0-fake")
        return 0
    if args[:2] == ["login", "status"]:
        if os.environ.get("FAKE_CODEX_LOGIN_HANG") == "1":
            time.sleep(60)
        return login_status()
    if args[:1] == ["exec"]:
        return run_exec(os.environ.get("FAKE_CODEX_MODE", "new"))
    print(f"unexpected args: {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
