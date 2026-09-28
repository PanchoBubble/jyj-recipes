import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fake_chat import FakeProvider, call, turn
from fastapi.testclient import TestClient
from sqlalchemy import Connection, update
from sqlalchemy.orm import Session, sessionmaker

from jyj import cli
from jyj.api.auth import SESSION_COOKIE
from jyj.chat.orchestrator import ChatEvent
from jyj.chat.provider import ProviderNotAuthenticated
from jyj.chat.router import (
    format_sse,
    get_chat_session_factory,
    get_codex_provider,
    stream_events,
)
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import ChatConversation, Ingredient, Recipe, StockItem, StockSource, User
from jyj.services import auth as auth_service
from jyj.services import ingredients as ingredients_service
from jyj.services import recipes as recipes_service

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1/chat"


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
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(session_factory, db: Session, provider: FakeProvider):
    clients: list[TestClient] = []

    def build(user: User | None) -> TestClient:
        app = create_app()

        def test_db() -> Iterator[Session]:
            yield from session_scope(session_factory)

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_codex_provider] = lambda: provider
        app.dependency_overrides[get_chat_session_factory] = lambda: session_factory
        client = TestClient(app, base_url="https://testserver")
        client.__enter__()
        clients.append(client)
        client.headers.update(CSRF)
        if user is not None:
            issued = auth_service.create_session(db, user, "pytest")
            db.commit()
            client.cookies.set(SESSION_COOKIE, issued.token)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def new_user(db: Session, name: str) -> User:
    user = auth_service.create_user(db, name, name.title(), "correct horse battery")
    db.commit()
    return user


@pytest.fixture
def user(db: Session) -> User:
    return new_user(db, "cook")


@pytest.fixture
def client(make_client, user: User) -> TestClient:
    return make_client(user)


@pytest.fixture
def intruder(make_client, db: Session) -> TestClient:
    return make_client(new_user(db, "sous"))


@pytest.fixture
def flour(db: Session, user: User) -> Ingredient:
    ingredient = ingredients_service.create_ingredient(
        db, user, StockSource.UI, name="Flour", dimension="mass"
    )
    db.commit()
    return ingredient


def parse_sse(body: str) -> list[tuple[str, dict]]:
    frames = []
    assert body.endswith("\n\n")
    for chunk in body.split("\n\n")[:-1]:
        lines = chunk.split("\n")
        if lines[0].startswith(":"):
            continue
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ") and len(lines) == 2
        frames.append(
            (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        )
    return frames


def new_conversation(client: TestClient) -> int:
    response = client.post(f"{API}/conversations")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client: TestClient, conversation_id: int, text: str = "hola", **extra):
    return client.post(
        f"{API}/conversations/{conversation_id}/messages", json={"text": text, **extra}
    )


# --- conversations -------------------------------------------------------------------------


def test_conversation_crud_and_listing(client: TestClient) -> None:
    first = new_conversation(client)
    second = new_conversation(client)

    listed = client.get(f"{API}/conversations").json()
    detail = client.get(f"{API}/conversations/{first}").json()

    assert {c["id"] for c in listed} == {first, second}
    assert detail["id"] == first
    assert detail["title"] is None
    assert detail["messages"] == [] and detail["actions"] == []


def test_conversations_are_private(client: TestClient, intruder: TestClient) -> None:
    mine = new_conversation(client)

    assert intruder.get(f"{API}/conversations").json() == []
    assert intruder.get(f"{API}/conversations/{mine}").status_code == 404
    assert send(intruder, mine).status_code == 404


def test_chat_requires_a_session(make_client) -> None:
    anon = make_client(None)

    assert anon.get(f"{API}/conversations").status_code == 401
    assert anon.post(f"{API}/conversations").status_code == 401


def test_message_body_is_validated(client: TestClient) -> None:
    conversation_id = new_conversation(client)

    assert send(client, conversation_id, "").status_code == 422
    assert send(client, conversation_id, "   ").status_code == 422
    assert send(client, conversation_id, "x" * 2001).status_code == 422
    assert send(client, conversation_id, "hi", transcript_confidence=0.5).status_code == 422
    assert send(client, conversation_id, "hi", role="system").status_code == 422


# --- streaming -----------------------------------------------------------------------------


def test_message_streams_sse_events(
    client: TestClient, provider: FakeProvider, db: Session, flour: Ingredient
) -> None:
    conversation_id = new_conversation(client)
    provider.steps.append(
        turn(
            "Añadí 1 kg.",
            actions=[call("adjust_stock", ingredient_id=flour.id, delta=1, unit="kg")],
        )
    )

    response = send(client, conversation_id, "añade 1 kg de harina")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    frames = parse_sse(response.text)
    assert [kind for kind, _ in frames] == ["status", "status", "action", "assistant", "done"]
    action = frames[2][1]
    assert (action["tool"], action["status"], action["summary"]) == (
        "adjust_stock",
        "executed",
        "adjust stock: Flour",
    )
    assert frames[3][1]["reply"] == "Añadí 1 kg."
    assert frames[4][1]["assistant_message_id"] == frames[3][1]["message_id"]
    db.expire_all()
    assert db.get(StockItem, flour.id).quantity_base == Decimal("1000")

    detail = client.get(f"{API}/conversations/{conversation_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "tool"]
    assert detail["messages"][0]["input"] == "text"
    assert detail["title"] == "añade 1 kg de harina"
    [shown] = detail["actions"]
    assert shown["id"] == action["action_id"]
    assert shown["message_id"] == frames[3][1]["message_id"]


def test_read_actions_are_not_listed_as_cards(client: TestClient, provider: FakeProvider) -> None:
    conversation_id = new_conversation(client)
    provider.steps += [turn("", needs=[call("search_recipes", query=None)]), turn("none yet")]

    frames = parse_sse(send(client, conversation_id, "what recipes?").text)

    assert "action" not in [kind for kind, _ in frames]
    assert client.get(f"{API}/conversations/{conversation_id}").json()["actions"] == []


def test_voice_input_is_recorded(client: TestClient, provider: FakeProvider) -> None:
    conversation_id = new_conversation(client)
    provider.steps.append(turn("ok"))

    send(client, conversation_id, "hola", input="voice", transcript_confidence=0.82)

    [message, _] = client.get(f"{API}/conversations/{conversation_id}").json()["messages"]
    assert (message["input"], message["transcript_confidence"]) == ("voice", 0.82)


def test_not_authenticated_provider_gives_a_sanitized_error_event(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation_id = new_conversation(client)
    provider.steps.append(ProviderNotAuthenticated("exit 1 reading /codex-home/auth.json"))

    response = send(client, conversation_id)

    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert [kind for kind, _ in frames] == ["status", "error", "done"]
    error = frames[1][1]
    assert error["code"] == "codex_not_authenticated"
    assert "codex login" in error["message"]
    assert "auth.json" not in response.text


def test_format_sse_keeps_one_data_line() -> None:
    frame = format_sse(ChatEvent("assistant", {"reply": "line one\nline two\n\nevent: x"}))

    assert frame == 'event: assistant\ndata: {"reply":"line one\\nline two\\n\\nevent: x"}\n\n'


def test_closing_the_stream_cancels_the_turn() -> None:
    started = threading.Event()
    seen: list[bool] = []

    def work(emit, cancel: threading.Event) -> None:
        emit(ChatEvent("status", {"state": "thinking"}))
        started.set()
        seen.append(cancel.wait(timeout=5))

    async def scenario() -> None:
        stream = stream_events(work)
        first = await anext(stream)
        assert first.startswith("event: status\n")
        await asyncio.to_thread(started.wait, 5)
        await stream.aclose()

    asyncio.run(scenario())
    for _ in range(50):
        if seen:
            break
        threading.Event().wait(0.05)
    assert seen == [True]


def test_stream_sends_keepalives_while_waiting() -> None:
    def work(emit, cancel) -> None:
        threading.Event().wait(0.15)
        emit(ChatEvent("assistant", {"reply": "hi"}))

    async def collect() -> list[str]:
        return [chunk async for chunk in stream_events(work, keepalive=0.05)]

    chunks = asyncio.run(collect())

    assert ": keepalive\n\n" in chunks
    assert chunks[-1].startswith("event: assistant\n")


def test_worker_crash_becomes_a_sanitized_error_event() -> None:
    def work(emit, cancel) -> None:
        raise RuntimeError("password=hunter2")

    async def collect() -> list[str]:
        return [chunk async for chunk in stream_events(work)]

    chunks = asyncio.run(collect())

    assert chunks == [
        format_sse(
            ChatEvent(
                "error",
                {
                    "code": "internal_error",
                    "message": "Something went wrong. Please try again.",
                },
            )
        )
    ]


# --- confirmation --------------------------------------------------------------------------


def propose_delete(client: TestClient, provider: FakeProvider, recipe: Recipe) -> int:
    conversation_id = new_conversation(client)
    provider.steps.append(turn("Confirm?", actions=[call("delete_recipe", recipe_id=recipe.id)]))
    frames = parse_sse(send(client, conversation_id, "delete soup").text)
    [card] = [data for kind, data in frames if kind == "action"]
    assert card["status"] == "proposed"
    return card["action_id"]


@pytest.fixture
def soup(db: Session, user: User) -> Recipe:
    recipe = recipes_service.create_recipe(db, user, StockSource.UI, name="Soup")
    db.commit()
    return recipe


def test_confirm_endpoint_executes_once(
    client: TestClient, provider: FakeProvider, db: Session, soup: Recipe
) -> None:
    soup_id = soup.id
    action_id = propose_delete(client, provider, soup)

    confirmed = client.post(f"{API}/actions/{action_id}/confirm")
    again = client.post(f"{API}/actions/{action_id}/confirm")

    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert (body["status"], body["tool"], body["data"]["outcome"]) == (
        "executed",
        "delete_recipe",
        "deleted",
    )
    assert again.status_code == 409
    db.expire_all()
    assert db.get(Recipe, soup_id) is None


def test_reject_endpoint(client: TestClient, provider: FakeProvider, db: Session, soup) -> None:
    action_id = propose_delete(client, provider, soup)

    rejected = client.post(f"{API}/actions/{action_id}/reject")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.post(f"{API}/actions/{action_id}/confirm").status_code == 409
    db.expire_all()
    assert db.get(Recipe, soup.id) is not None


def test_only_the_owner_can_confirm_or_reject(
    client: TestClient, intruder: TestClient, provider: FakeProvider, db: Session, soup
) -> None:
    action_id = propose_delete(client, provider, soup)

    assert intruder.post(f"{API}/actions/{action_id}/confirm").status_code == 404
    assert intruder.post(f"{API}/actions/{action_id}/reject").status_code == 404
    assert client.post(f"{API}/actions/999999/confirm").status_code == 404
    db.expire_all()
    assert db.get(Recipe, soup.id) is not None


def test_confirm_needs_the_csrf_header(client, provider, soup) -> None:
    action_id = propose_delete(client, provider, soup)

    response = client.post(f"{API}/actions/{action_id}/confirm", headers={"X-Requested-With": ""})

    assert response.status_code == 403


# --- CLI -----------------------------------------------------------------------------------


def test_cli_purge(session_factory, db: Session, user: User, capsys) -> None:
    old = ChatConversation(user_id=user.id)
    fresh = ChatConversation(user_id=user.id)
    db.add_all([old, fresh])
    db.flush()
    db.execute(
        update(ChatConversation)
        .where(ChatConversation.id == old.id)
        .values(updated_at=datetime.now(UTC) - timedelta(days=100))
    )
    db.commit()
    old_id, fresh_id = old.id, fresh.id

    assert cli.main(["chat", "purge"], session_factory=session_factory) == 0

    assert "purged 1 conversations" in capsys.readouterr().out
    db.expire_all()
    assert db.get(ChatConversation, old_id) is None
    assert db.get(ChatConversation, fresh_id) is not None


def test_cli_purge_rejects_bad_days(session_factory) -> None:
    with pytest.raises(SystemExit):
        cli.main(["chat", "purge", "--days", "0"], session_factory=session_factory)
