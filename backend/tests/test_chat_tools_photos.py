from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx2
import pytest
from fake_chat import FakeProvider, call, turn
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker
from test_image_search import KEY, FakePexels, jpeg, pexels_photo

from jyj.chat import router as chat_router
from jyj.chat.orchestrator import ChatEvent, run_turn
from jyj.chat.tools import Registry, ToolContext, ToolStatus, build_registry
from jyj.chat.tools.schema import check_strict
from jyj.config import get_settings
from jyj.models import ChatAction, ChatConversation, ChatInput, Recipe, StockSource, User
from jyj.services import auth as auth_service
from jyj.services import image_search, photos
from jyj.services import recipes as recipes_service


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePexels]:
    fake = FakePexels()
    monkeypatch.setenv("PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("PEXELS_API_KEY", KEY)
    monkeypatch.setattr(image_search, "_transport", httpx2.MockTransport(fake.handler))
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()
    yield fake
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()


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
    return auth_service.create_user(db, "cook", "Cook", "correct horse battery")


@pytest.fixture
def conversation(db: Session, user: User) -> ChatConversation:
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    return conversation


@pytest.fixture
def ctx(db: Session, user: User, conversation: ChatConversation) -> ToolContext:
    return ToolContext(db=db, user=user, conversation_id=conversation.id)


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture
def recipe(ctx: ToolContext) -> Recipe:
    return recipes_service.create_recipe(ctx.db, ctx.user, StockSource.UI, name="Tortilla")


def set_args(recipe: Recipe, photo_id: str = "101") -> dict:
    return {"recipe_id": recipe.id, "provider": "pexels", "photo_id": photo_id}


def test_schemas_are_strict(registry: Registry) -> None:
    for name in ("find_recipe_photos", "set_recipe_photo"):
        check_strict(registry.args_schema(name), path=name)
    registry.output_schema()
    catalog = registry.catalog_text()
    assert "find_recipe_photos(query: string, recipe_id: integer|null)" in catalog
    assert "set_recipe_photo(" in catalog
    assert "[sometimes asks the user to confirm first]" in catalog


def test_find_recipe_photos_returns_compact_results(
    registry: Registry, ctx: ToolContext, recipe: Recipe, fake: FakePexels
) -> None:
    fake.search_body = {"photos": [pexels_photo(i) for i in range(1, 11)]}
    result = registry.execute(
        "find_recipe_photos", {"query": "tortilla", "recipe_id": recipe.id}, ctx
    )
    assert result.status is ToolStatus.SUCCESS, result.error
    data = result.data
    assert data["recipe_id"] == recipe.id
    assert data["recipe_name"] == "Tortilla"
    assert data["has_photo"] is False
    assert len(data["photos"]) == 6
    assert data["provider"] == "pexels"
    assert set(data["photos"][0]) == {
        "provider",
        "id",
        "alt",
        "photographer",
        "license",
        "thumb_url",
    }
    assert fake.requests[0].url.params["per_page"] == "6"


def test_find_recipe_photos_without_recipe(
    registry: Registry, ctx: ToolContext, fake: FakePexels
) -> None:
    result = registry.execute("find_recipe_photos", {"query": "paella", "recipe_id": None}, ctx)
    assert result.ok
    assert result.data["recipe_id"] is None


def test_find_recipe_photos_forced_pexels_without_key_is_rejected(
    registry: Registry, ctx: ToolContext, fake: FakePexels, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PHOTO_SEARCH_PROVIDER", "pexels")
    get_settings.cache_clear()
    image_search.get_image_search.cache_clear()
    result = registry.execute("find_recipe_photos", {"query": "paella"}, ctx)
    assert result.status is ToolStatus.REJECTED
    assert result.error["message"] == "photo search isn't configured"
    assert fake.requests == []


def test_set_recipe_photo_runs_directly_without_existing_photo(
    registry: Registry, ctx: ToolContext, recipe: Recipe, fake: FakePexels
) -> None:
    result = registry.execute("set_recipe_photo", set_args(recipe), ctx)
    assert result.status is ToolStatus.SUCCESS, result.error
    assert result.data == {
        "recipe_id": recipe.id,
        "name": "Tortilla",
        "provider": "pexels",
        "photo_id": "101",
        "photographer": "Ana Cook",
        "license": None,
    }
    ctx.db.refresh(recipe)
    assert recipe.photo_path is not None
    assert recipe.photo_credit["photographer"] == "Ana Cook"


def test_set_recipe_photo_asks_before_replacing(
    registry: Registry, ctx: ToolContext, recipe: Recipe, fake: FakePexels
) -> None:
    photos.set_recipe_photo(ctx.db, recipe.id, jpeg())
    before = recipe.photo_path

    proposed = registry.execute("set_recipe_photo", set_args(recipe), ctx)
    assert proposed.status is ToolStatus.NEEDS_CONFIRMATION
    assert proposed.data["replaces_photo"] is True
    assert fake.requests == []
    ctx.db.refresh(recipe)
    assert recipe.photo_path == before

    confirmed = registry.confirm(ctx, proposed.action_id)
    assert confirmed.status is ToolStatus.SUCCESS, confirmed.error
    ctx.db.refresh(recipe)
    assert recipe.photo_path != before
    assert recipe.photo_credit["provider"] == "pexels"


def test_set_recipe_photo_unknown_recipe_or_photo(
    registry: Registry, ctx: ToolContext, recipe: Recipe, fake: FakePexels
) -> None:
    missing = registry.execute(
        "set_recipe_photo", {"recipe_id": 999_999, "provider": "pexels", "photo_id": "101"}, ctx
    )
    assert missing.status is ToolStatus.REJECTED
    assert missing.error["code"] == "not_found"
    gone = registry.execute("set_recipe_photo", set_args(recipe, photo_id="999"), ctx)
    assert gone.error["code"] == "not_found"


def test_set_recipe_photo_rejects_other_providers_and_urls(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    for args in (
        {**set_args(recipe), "provider": "unsplash"},
        {**set_args(recipe), "url": "https://evil.example/x.jpeg"},
    ):
        result = registry.execute("set_recipe_photo", args, ctx)
        assert result.error["code"] == "invalid_arguments"


def test_set_recipe_photo_off_cdn_download_is_rejected(
    registry: Registry, ctx: ToolContext, recipe: Recipe, fake: FakePexels
) -> None:
    fake.photos[101]["src"].update(
        large2x="https://evil.example/a.jpeg", original="https://evil.example/b.jpeg"
    )
    result = registry.execute("set_recipe_photo", set_args(recipe), ctx)
    assert result.status is ToolStatus.REJECTED
    ctx.db.refresh(recipe)
    assert recipe.photo_path is None


def test_photo_search_results_are_shown_as_a_card(
    db: Session,
    user: User,
    conversation: ChatConversation,
    registry: Registry,
    recipe: Recipe,
    fake: FakePexels,
) -> None:
    events: list[ChatEvent] = []
    provider = FakeProvider(
        turn("Looking.", needs=[call("find_recipe_photos", query="tortilla", recipe_id=recipe.id)]),
        turn("Here are some photos; tap Use on the one you like."),
    )
    run_turn(
        db,
        user,
        conversation,
        "find a photo for the tortilla",
        ChatInput.TEXT,
        events.append,
        provider=provider,
        registry=registry,
        today=date(2026, 9, 28),
    )
    cards = [e.data for e in events if e.type == "action"]
    assert [c["tool"] for c in cards] == ["find_recipe_photos"]
    assert cards[0]["status"] == "executed"
    assert cards[0]["data"]["recipe_id"] == recipe.id
    assert [p["id"] for p in cards[0]["data"]["photos"]] == [101, 102]

    detail = chat_router.get_conversation(conversation.id, user, db, registry)
    assert [a.tool for a in detail.actions] == ["find_recipe_photos"]
    assert detail.actions[0].data["photos"][0]["thumb_url"].startswith("https://images.pexels.com/")


def test_failed_photo_search_is_not_a_card(
    db: Session,
    user: User,
    conversation: ChatConversation,
    registry: Registry,
    fake: FakePexels,
) -> None:
    fake.api_status = 500
    events: list[ChatEvent] = []
    provider = FakeProvider(
        turn("Looking.", needs=[call("find_recipe_photos", query="tortilla", recipe_id=None)]),
        turn("Photo search is not working right now."),
    )
    run_turn(
        db,
        user,
        conversation,
        "find a photo",
        ChatInput.TEXT,
        events.append,
        provider=provider,
        registry=registry,
        today=date(2026, 9, 28),
    )
    assert [e for e in events if e.type == "action"] == []
    (row,) = db.scalars(select(ChatAction)).all()
    assert row.tool == "find_recipe_photos"
    detail = chat_router.get_conversation(conversation.id, user, db, registry)
    assert detail.actions == []
