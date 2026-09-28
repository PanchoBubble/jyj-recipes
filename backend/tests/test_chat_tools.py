import ast
import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session, sessionmaker

from jyj.chat.tools import Registry, Tool, ToolContext, ToolStatus, build_registry
from jyj.chat.tools.schema import StrictSchemaError, check_strict, strict_schema
from jyj.models import (
    ChatAction,
    ChatActionStatus,
    ChatConversation,
    Ingredient,
    Recipe,
    StockMovement,
    User,
)
from jyj.services import auth as auth_service
from jyj.services import ingredients as ingredients_service
from jyj.services import recipes as recipes_service
from jyj.services import stock as stock_service
from jyj.services.errors import NotFoundError

TOOLS_DIR = Path(__file__).resolve().parent.parent / "src" / "jyj" / "chat" / "tools"
STRICT_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "anyOf",
    "description",
}


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
def other_user(db: Session) -> User:
    return auth_service.create_user(db, "sous", "Sous", "correct horse battery")


@pytest.fixture
def ctx(db: Session, user: User) -> ToolContext:
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    return ToolContext(db=db, user=user, conversation_id=conversation.id)


@pytest.fixture
def registry() -> Registry:
    return build_registry()


def ingredient(ctx: ToolContext, name: str, dimension: str = "mass", **fields) -> Ingredient:
    return ingredients_service.create_ingredient(
        ctx.db, ctx.user, ctx.source, name=name, dimension=dimension, **fields
    )


def audit_rows(db: Session) -> list[ChatAction]:
    return list(db.scalars(select(ChatAction).order_by(ChatAction.id)))


class SpyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    label: str | None = None


def spy_registry(handler: Callable, kind: str = "write") -> Registry:
    registry = Registry()
    registry.register(
        Tool(name="spy", description="test", args_model=SpyArgs, kind=kind, handler=handler)
    )
    return registry


class Spy:
    def __init__(self, result: dict | None = None) -> None:
        self.calls: list = []
        self.result = result if result is not None else {"ok": True}

    def __call__(self, ctx: ToolContext, args: BaseModel) -> dict:
        self.calls.append(args)
        return self.result


# --- allowlist and validation ---------------------------------------------------------------


def test_unknown_tool_is_rejected_and_audited(registry: Registry, ctx: ToolContext) -> None:
    result = registry.execute("drop_tables", {"all": True}, ctx)

    assert result.status is ToolStatus.REJECTED
    assert result.error["code"] == "unknown_tool"
    [row] = audit_rows(ctx.db)
    assert (row.tool, row.status, row.arguments) == (
        "drop_tables",
        ChatActionStatus.REJECTED,
        {"all": True},
    )
    assert row.requested_by == ctx.user.id
    assert row.conversation_id == ctx.conversation_id
    assert row.executed_at is None


@pytest.mark.parametrize(
    "raw_args",
    [
        {"count": "many"},
        {"count": 1, "extra": "nope"},
        {},
        ["count", 1],
        None,
    ],
    ids=["wrong-type", "extra-field", "missing-field", "list", "null"],
)
def test_invalid_args_are_rejected_before_the_handler(ctx: ToolContext, raw_args) -> None:
    spy = Spy()
    registry = spy_registry(spy)

    result = registry.execute("spy", raw_args, ctx)

    assert result.status is ToolStatus.REJECTED
    assert result.error["code"] == "invalid_arguments"
    assert spy.calls == []
    [row] = audit_rows(ctx.db)
    assert row.status is ChatActionStatus.REJECTED


def test_validation_errors_do_not_echo_input(ctx: ToolContext) -> None:
    registry = spy_registry(Spy())

    result = registry.execute("spy", {"count": "secret-value-xyz"}, ctx)

    assert "secret-value-xyz" not in json.dumps(result.as_dict())
    assert result.error["details"] == [
        {"field": "count", "problem": result.error["details"][0]["problem"]}
    ]


def test_valid_args_reach_the_handler_as_a_model(ctx: ToolContext) -> None:
    spy = Spy({"echo": 3})
    registry = spy_registry(spy)

    result = registry.execute("spy", {"count": 3, "label": None}, ctx)

    assert result.status is ToolStatus.SUCCESS
    assert result.data == {"echo": 3}
    assert spy.calls == [SpyArgs(count=3)]
    [row] = audit_rows(ctx.db)
    assert row.status is ChatActionStatus.EXECUTED
    assert row.result == {"data": {"echo": 3}}
    assert row.executed_at is not None
    assert result.action_id == row.id


# --- error sanitisation ---------------------------------------------------------------------


def test_handler_exceptions_become_sanitised_errors_and_roll_back(ctx: ToolContext) -> None:
    def explode(c: ToolContext, args: SpyArgs) -> dict:
        ingredient(c, "Half-written")
        raise RuntimeError('psycopg.errors.UndefinedTable: relation "secret" SELECT * FROM users')

    result = spy_registry(explode).execute("spy", {"count": 1}, ctx)

    assert result.status is ToolStatus.ERROR
    assert result.error == {"code": "internal_error", "message": "the tool failed unexpectedly"}
    text = json.dumps(result.as_dict())
    assert "SELECT" not in text and "psycopg" not in text and "Traceback" not in text
    assert ctx.db.scalar(select(Ingredient).where(Ingredient.name == "Half-written")) is None
    [row] = audit_rows(ctx.db)
    assert row.status is ChatActionStatus.FAILED
    assert "SELECT" not in json.dumps(row.result)


def test_non_json_results_are_errors(ctx: ToolContext) -> None:
    result = spy_registry(lambda c, a: {"when": object()}).execute("spy", {"count": 1}, ctx)
    assert result.status is ToolStatus.ERROR


def test_service_errors_become_friendly_rejections(ctx: ToolContext) -> None:
    def missing(c: ToolContext, args: SpyArgs) -> dict:
        raise NotFoundError("recipe 99 not found")

    result = spy_registry(missing).execute("spy", {"count": 1}, ctx)

    assert result.status is ToolStatus.REJECTED
    assert result.error == {"code": "not_found", "message": "recipe 99 not found"}


def test_read_tools_cannot_persist_writes(ctx: ToolContext) -> None:
    def sneaky(c: ToolContext, args: SpyArgs) -> dict:
        ingredient(c, "Sneaky")
        return {"ok": True}

    result = spy_registry(sneaky, kind="read").execute("spy", {"count": 1}, ctx)

    assert result.ok
    assert ctx.db.scalar(select(Ingredient).where(Ingredient.name == "Sneaky")) is None


def test_missing_ids_are_rejected(registry: Registry, ctx: ToolContext) -> None:
    for name, args in [
        ("get_recipe", {"recipe_id": 999999, "servings": None}),
        ("update_recipe", {"recipe_id": 999999, "name": "X"}),
        ("adjust_stock", {"ingredient_id": 999999, "delta": 1, "unit": "g", "reason": None}),
        ("get_stock", {"ingredient_ids": [999999]}),
    ]:
        result = registry.execute(name, args, ctx)
        assert result.status is ToolStatus.REJECTED, name
        assert result.error["code"] == "not_found", name
        assert "not found" in result.error["message"]


# --- registration guards --------------------------------------------------------------------


def test_register_refuses_models_that_allow_extra_fields() -> None:
    class Loose(BaseModel):
        count: int

    with pytest.raises(ValueError, match="extra='forbid'"):
        Registry().register(
            Tool(name="loose", description="", args_model=Loose, kind="read", handler=Spy())
        )


def test_register_refuses_optional_fields_that_are_not_nullable() -> None:
    class Defaulted(BaseModel):
        model_config = ConfigDict(extra="forbid")
        limit: int = 20

    with pytest.raises(StrictSchemaError, match="must allow None"):
        strict_schema(Defaulted)


def test_register_refuses_duplicates_and_read_tools_needing_confirmation() -> None:
    registry = spy_registry(Spy())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            Tool(name="spy", description="", args_model=SpyArgs, kind="write", handler=Spy())
        )
    with pytest.raises(ValueError, match="only write tools"):
        registry.register(
            Tool(
                name="peek",
                description="",
                args_model=SpyArgs,
                kind="read",
                handler=Spy(),
                requires_confirmation=True,
            )
        )


# --- output schema --------------------------------------------------------------------------


def _walk_strict(node: dict, path: str = "$", depth: int = 0) -> int:
    """Independent re-check of the strict rules; returns the max object depth."""
    assert set(node) <= STRICT_KEYWORDS, f"{path}: {sorted(set(node) - STRICT_KEYWORDS)}"
    if "anyOf" in node:
        return max(_walk_strict(o, f"{path}|", depth) for o in node["anyOf"])
    assert "type" in node, path
    if node["type"] == "object":
        assert node["additionalProperties"] is False, path
        assert set(node["required"]) == set(node["properties"]), path
        return max(
            [depth + 1]
            + [_walk_strict(p, f"{path}.{k}", depth + 1) for k, p in node["properties"].items()]
        )
    if node["type"] == "array":
        return _walk_strict(node["items"], f"{path}[]", depth)
    assert node["type"] in {"string", "number", "integer", "boolean", "null"}, path
    return depth


def test_output_schema_follows_strict_rules(registry: Registry) -> None:
    schema = registry.output_schema()

    check_strict(schema)
    assert _walk_strict(schema) <= 5
    text = json.dumps(schema)
    for banned in ("$ref", "$defs", "oneOf", "allOf", '"default"', '"format"', '"pattern"'):
        assert banned not in text
    assert schema["required"] == ["reply", "actions", "needs"]
    assert schema["properties"]["reply"]["type"] == "string"


def test_output_schema_splits_reads_and_writes(registry: Registry) -> None:
    schema = registry.output_schema()

    def tools_in(key: str) -> set[str]:
        branches = schema["properties"][key]["items"]["anyOf"]
        for branch in branches:
            assert branch["required"] == ["tool", "args"]
            assert branch["properties"]["args"] == registry.args_schema(
                branch["properties"]["tool"]["enum"][0]
            )
        return {b["properties"]["tool"]["enum"][0] for b in branches}

    assert tools_in("needs") == {
        "search_recipes",
        "get_recipe",
        "list_ingredients",
        "get_stock",
        "get_plan",
        "preview_shopping",
    }
    assert tools_in("actions") == {
        "create_recipe",
        "update_recipe",
        "delete_recipe",
        "create_ingredient",
        "adjust_stock",
        "set_stock",
        "plan_meal",
        "move_meal",
        "set_meal_slot",
        "set_servings",
        "remove_meal",
        "mark_cooked",
        "uncook_meal",
        "create_shopping_list",
    }


def test_every_args_schema_is_strict(registry: Registry) -> None:
    for name in registry.names:
        schema = registry.args_schema(name)
        check_strict(schema, path=name)
        _walk_strict(schema, name)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        {"type": "string", "format": "date"},
        {"oneOf": [{"type": "string"}]},
        {"type": "array"},
    ],
    ids=["open-object", "optional-property", "format", "oneOf", "array-without-items"],
)
def test_check_strict_catches_violations(schema: dict) -> None:
    with pytest.raises(StrictSchemaError):
        check_strict(schema)


def test_catalog_text_lists_every_tool(registry: Registry) -> None:
    text = registry.catalog_text()
    for name in registry.names:
        assert f"- {name}(" in text
    assert "delete_recipe(recipe_id: integer) [asks the user to confirm first]" in text
    assert "remove_meal(meal_id: integer) [asks the user to confirm first]" in text
    assert "get_plan(from: string, to: string)" in text


# --- recipe tools ---------------------------------------------------------------------------


def test_recipe_tools_happy_path(registry: Registry, ctx: ToolContext) -> None:
    flour = ingredient(ctx, "Flour")

    created = registry.execute(
        "create_recipe",
        {
            "name": "Bread",
            "description": None,
            "default_servings": 4,
            "ingredients": [
                {
                    "ingredient_id": flour.id,
                    "new_ingredient": None,
                    "amount_per_person": 125,
                    "unit": "g",
                    "note": None,
                },
                {
                    "ingredient_id": None,
                    "new_ingredient": {
                        "name": "Sea salt",
                        "dimension": "mass",
                        "default_unit": None,
                        "category": None,
                    },
                    "amount_per_person": None,
                    "unit": "pinch",
                    "note": "to taste",
                },
            ],
        },
        ctx,
    )
    assert created.status is ToolStatus.SUCCESS, created.error
    recipe_id = created.data["id"]
    assert created.data == {
        "id": recipe_id,
        "name": "Bread",
        "default_servings": 4,
        "ingredient_count": 2,
        "archived": False,
    }
    recipe = ctx.db.get(Recipe, recipe_id)
    assert recipe.created_by == ctx.user.id

    found = registry.execute(
        "search_recipes", {"query": "brea", "include_archived": None, "limit": None}, ctx
    )
    assert found.data["total"] == 1
    assert found.data["items"][0]["id"] == recipe_id

    got = registry.execute("get_recipe", {"recipe_id": recipe_id, "servings": 3}, ctx)
    assert got.ok
    flour_line, salt_line = got.data["ingredients"]
    assert flour_line == {
        "ingredient_id": flour.id,
        "name": "Flour",
        "per_person": "125",
        "unit": "g",
        "note": None,
        "total": "375",
    }
    assert salt_line["per_person"] is None and salt_line["total"] is None

    updated = registry.execute(
        "update_recipe",
        {
            "recipe_id": recipe_id,
            "name": "Sourdough",
            "description": None,
            "default_servings": None,
            "ingredients": None,
            "archived": None,
        },
        ctx,
    )
    assert updated.ok
    assert updated.data["name"] == "Sourdough"
    assert updated.data["default_servings"] == 4
    assert updated.data["ingredient_count"] == 2


def test_update_recipe_with_nothing_to_change_is_rejected(
    registry: Registry, ctx: ToolContext
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Toast")
    result = registry.execute("update_recipe", {"recipe_id": recipe.id, "name": None}, ctx)
    assert result.error["code"] == "invalid_arguments"


def test_recipe_line_needs_exactly_one_ingredient(registry: Registry, ctx: ToolContext) -> None:
    result = registry.execute(
        "create_recipe",
        {
            "name": "Air",
            "description": None,
            "default_servings": None,
            "ingredients": [
                {
                    "ingredient_id": None,
                    "new_ingredient": None,
                    "amount_per_person": 1,
                    "unit": "g",
                    "note": None,
                }
            ],
        },
        ctx,
    )
    assert result.error["code"] == "invalid_arguments"


# --- ingredient and stock tools -------------------------------------------------------------


def test_ingredient_tools_happy_path(registry: Registry, ctx: ToolContext) -> None:
    created = registry.execute(
        "create_ingredient",
        {
            "name": "Milk",
            "dimension": "volume",
            "default_unit": "l",
            "category": "Dairy",
            "grams_per_ml": 1.03,
            "grams_per_piece": None,
        },
        ctx,
    )
    assert created.ok, created.error
    assert created.data == {
        "id": created.data["id"],
        "name": "Milk",
        "dimension": "volume",
        "default_unit": "l",
    }
    assert ctx.db.get(Ingredient, created.data["id"]).grams_per_ml == Decimal("1.03")

    dup = registry.execute(
        "create_ingredient",
        {"name": "milk", "dimension": "volume", "default_unit": None, "category": None},
        ctx,
    )
    assert dup.error["code"] == "conflict"
    assert dup.error["details"] == {"existing_id": created.data["id"]}

    listed = registry.execute("list_ingredients", {"query": "mil", "limit": None}, ctx)
    assert listed.data == {
        "total": 1,
        "items": [
            {
                "id": created.data["id"],
                "name": "Milk",
                "dimension": "volume",
                "default_unit": "l",
                "stock": "0 ml",
            }
        ],
    }


def test_stock_tools_happy_path(registry: Registry, ctx: ToolContext) -> None:
    flour = ingredient(ctx, "Flour")
    ingredient(ctx, "Rice")

    added = registry.execute(
        "adjust_stock",
        {"ingredient_id": flour.id, "delta": 1.5, "unit": "kg", "reason": "purchased"},
        ctx,
    )
    assert added.ok, added.error
    assert added.data == {"ingredient_id": flour.id, "name": "Flour", "stock": "1.5 kg"}

    used = registry.execute(
        "adjust_stock",
        {"ingredient_id": flour.id, "delta": -2000, "unit": "g", "reason": None},
        ctx,
    )
    assert used.data == {
        "ingredient_id": flour.id,
        "name": "Flour",
        "stock": "0 g",
        "shortfall": "500 g",
    }
    movements = list(ctx.db.scalars(select(StockMovement).order_by(StockMovement.id)))
    assert [(m.source.value, m.reason.value, m.user_id) for m in movements] == [
        ("chat", "purchased", ctx.user.id),
        ("chat", "manual", ctx.user.id),
    ]

    stock_service.adjust_stock(ctx.db, ctx.user, ctx.source, flour.id, Decimal("0.1"), "kg")
    everything = registry.execute("get_stock", {"ingredient_ids": None}, ctx)
    assert everything.data == {
        "items": [{"ingredient_id": flour.id, "name": "Flour", "stock": "100 g"}]
    }


def test_adjust_stock_rejects_unconvertible_units(registry: Registry, ctx: ToolContext) -> None:
    flour = ingredient(ctx, "Flour")
    result = registry.execute(
        "adjust_stock", {"ingredient_id": flour.id, "delta": 1, "unit": "ml", "reason": None}, ctx
    )
    assert result.status is ToolStatus.REJECTED
    assert result.error["code"] == "invalid"


# --- confirmation ---------------------------------------------------------------------------


def test_delete_recipe_waits_for_confirmation_and_runs_once(
    registry: Registry, ctx: ToolContext
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Soup")

    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, ctx)

    assert proposed.status is ToolStatus.NEEDS_CONFIRMATION
    assert proposed.data == {"recipe_id": recipe.id, "name": "Soup"}
    assert ctx.db.get(Recipe, recipe.id) is not None
    action = ctx.db.get(ChatAction, proposed.action_id)
    assert action.status is ChatActionStatus.PROPOSED
    assert action.executed_at is None and action.confirmed_by is None

    confirmer = ToolContext(db=ctx.db, user=ctx.user)
    confirmed = registry.confirm(confirmer, proposed.action_id)

    assert confirmed.status is ToolStatus.SUCCESS
    assert confirmed.data == {"recipe_id": recipe.id, "outcome": "deleted"}
    ctx.db.expunge_all()
    assert ctx.db.get(Recipe, recipe.id) is None
    action = ctx.db.get(ChatAction, proposed.action_id)
    assert action.status is ChatActionStatus.EXECUTED
    assert action.confirmed_by == action.requested_by == ctx.user.id
    assert action.executed_at is not None

    again = registry.confirm(confirmer, proposed.action_id)
    assert again.status is ToolStatus.REJECTED
    assert again.error["code"] == "not_pending"


def test_set_stock_never_runs_without_confirmation(registry: Registry, ctx: ToolContext) -> None:
    flour = ingredient(ctx, "Flour")
    stock_service.adjust_stock(ctx.db, ctx.user, ctx.source, flour.id, Decimal(200), "g")

    proposed = registry.execute(
        "set_stock", {"ingredient_id": flour.id, "set_to": 1, "unit": "kg"}, ctx
    )

    assert proposed.status is ToolStatus.NEEDS_CONFIRMATION
    assert proposed.data == {
        "ingredient_id": flour.id,
        "name": "Flour",
        "stock": "200 g",
        "new_stock": "1 kg",
    }
    assert ingredients_service.stock_of(ctx.db, flour.id) == Decimal(200)

    confirmed = registry.confirm(ctx, proposed.action_id)
    assert confirmed.ok, confirmed.error
    assert confirmed.data["stock"] == "1 kg"
    assert ingredients_service.stock_of(ctx.db, flour.id) == Decimal(1000)
    assert registry.confirm(ctx, proposed.action_id).status is ToolStatus.REJECTED
    assert ingredients_service.stock_of(ctx.db, flour.id) == Decimal(1000)


def test_confirmation_handler_is_never_called_by_execute(ctx: ToolContext) -> None:
    spy = Spy()
    registry = Registry()
    registry.register(
        Tool(
            name="nuke",
            description="",
            args_model=SpyArgs,
            kind="write",
            handler=spy,
            requires_confirmation=True,
        )
    )

    proposed = registry.execute("nuke", {"count": 1}, ctx)
    assert proposed.status is ToolStatus.NEEDS_CONFIRMATION
    assert spy.calls == []

    registry.confirm(ctx, proposed.action_id)
    registry.confirm(ctx, proposed.action_id)
    assert len(spy.calls) == 1


def test_proposals_for_missing_ids_are_rejected_up_front(
    registry: Registry, ctx: ToolContext
) -> None:
    result = registry.execute("delete_recipe", {"recipe_id": 999999}, ctx)

    assert result.status is ToolStatus.REJECTED
    assert result.error["code"] == "not_found"
    [row] = audit_rows(ctx.db)
    assert row.status is ChatActionStatus.REJECTED


def test_rejected_proposals_cannot_be_confirmed(registry: Registry, ctx: ToolContext) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Stew")
    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, ctx)

    declined = registry.reject(ctx, proposed.action_id)
    assert declined.status is ToolStatus.REJECTED
    assert ctx.db.get(ChatAction, proposed.action_id).status is ChatActionStatus.REJECTED

    assert registry.confirm(ctx, proposed.action_id).error["code"] == "not_pending"
    assert ctx.db.get(Recipe, recipe.id) is not None


def test_confirming_unknown_or_executed_actions_is_rejected(
    registry: Registry, ctx: ToolContext
) -> None:
    assert registry.confirm(ctx, 999999).error["code"] == "not_found"
    done = registry.execute("list_ingredients", {"query": None, "limit": None}, ctx)
    assert registry.confirm(ctx, done.action_id).error["code"] == "not_pending"


def test_only_the_requester_or_conversation_owner_can_decide(
    registry: Registry, ctx: ToolContext, other_user: User
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Stew")
    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, ctx)
    intruder = ToolContext(db=ctx.db, user=other_user, conversation_id=ctx.conversation_id)

    for decide in (registry.confirm, registry.reject):
        result = decide(intruder, proposed.action_id)
        assert result.status is ToolStatus.REJECTED
        assert result.error["code"] == "not_found"
        assert result.tool == ""

    row = ctx.db.get(ChatAction, proposed.action_id)
    assert row.status is ChatActionStatus.PROPOSED
    assert row.confirmed_by is None
    assert ctx.db.get(Recipe, recipe.id) is not None


def test_conversation_owner_can_decide_a_proposal_they_did_not_request(
    registry: Registry, ctx: ToolContext, other_user: User
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Stew")
    guest = ToolContext(db=ctx.db, user=other_user, conversation_id=ctx.conversation_id)
    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, guest)

    assert registry.confirm(ctx, proposed.action_id).ok
    assert ctx.db.get(ChatAction, proposed.action_id).confirmed_by == ctx.user.id


def test_requester_can_decide_after_the_conversation_is_gone(
    registry: Registry, ctx: ToolContext, other_user: User
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Stew")
    loose = ToolContext(db=ctx.db, user=ctx.user)
    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, loose)

    assert (
        registry.reject(ToolContext(db=ctx.db, user=other_user), proposed.action_id).error["code"]
        == "not_found"
    )
    assert registry.reject(loose, proposed.action_id).error["code"] == "declined"


def test_confirm_of_a_vanished_recipe_is_recorded_as_rejected(
    registry: Registry, ctx: ToolContext
) -> None:
    recipe = recipes_service.create_recipe(ctx.db, ctx.user, ctx.source, name="Pie")
    proposed = registry.execute("delete_recipe", {"recipe_id": recipe.id}, ctx)
    recipes_service.delete_recipe(ctx.db, ctx.user, ctx.source, recipe.id)

    result = registry.confirm(ctx, proposed.action_id)

    assert result.error["code"] == "not_found"
    assert ctx.db.get(ChatAction, proposed.action_id).status is ChatActionStatus.REJECTED


def test_every_call_is_audited(registry: Registry, ctx: ToolContext) -> None:
    registry.execute("search_recipes", {"query": None, "include_archived": None, "limit": 5}, ctx)
    registry.execute("nope", {}, ctx)
    registry.execute("get_recipe", {"recipe_id": "x"}, ctx)

    rows = audit_rows(ctx.db)
    assert [(r.tool, r.status) for r in rows] == [
        ("search_recipes", ChatActionStatus.EXECUTED),
        ("nope", ChatActionStatus.REJECTED),
        ("get_recipe", ChatActionStatus.REJECTED),
    ]
    assert rows[0].arguments == {"query": None, "include_archived": None, "limit": 5}


# --- scope ----------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = (
    "sqlalchemy",
    "jyj.db",
    "os",
    "subprocess",
    "socket",
    "shutil",
    "pathlib",
    "urllib",
    "http",
    "httpx",
    "requests",
    "psycopg",
)


@pytest.mark.parametrize(
    "module",
    ["recipes.py", "ingredients.py", "stock.py", "calendar.py", "shopping.py", "common.py"],
)
def test_tool_modules_only_reach_data_through_services(module: str) -> None:
    tree = ast.parse((TOOLS_DIR / module).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for name in imported:
        assert not any(name == f or name.startswith(f"{f}.") for f in FORBIDDEN_IMPORTS), name
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in {"open", "eval", "exec", "__import__"}, node.id


# --- statement timeout -----------------------------------------------------------------------


def slow_registry(kind: str = "read", timeout: float | None = 0.1) -> Registry:
    """A test-only tool that writes a row, then sleeps for ``count`` milliseconds in Postgres."""

    def handler(ctx: ToolContext, args: SpyArgs) -> dict:
        if kind == "write":
            ingredient(ctx, args.label or "Slow")
        ctx.db.execute(text("SELECT pg_sleep(:s)"), {"s": args.count / 1000})
        return {"slept": args.count}

    registry = Registry(tool_timeout_seconds=timeout)
    registry.register(
        Tool(name="slow", description="test", args_model=SpyArgs, kind=kind, handler=handler)
    )
    return registry


def statement_timeout(db: Session) -> str:
    return db.scalar(text("SELECT current_setting('statement_timeout')"))


@pytest.mark.parametrize("kind", ["read", "write"])
def test_slow_tools_time_out_with_a_sanitised_result(ctx: ToolContext, kind: str) -> None:
    before = statement_timeout(ctx.db)

    result = slow_registry(kind).execute("slow", {"count": 2000, "label": "Saffron"}, ctx)

    assert result.status is ToolStatus.TIMEOUT
    assert result.error == {
        "code": "timeout",
        "message": "the tool took too long; try a narrower request",
    }
    assert result.as_dict()["status"] == "timeout"
    [row] = audit_rows(ctx.db)
    assert row.status is ChatActionStatus.TIMEOUT
    assert row.result == {"error": result.error}
    assert "pg_sleep" not in json.dumps(row.result)
    assert ctx.db.scalars(select(Ingredient).where(Ingredient.name == "Saffron")).all() == []
    assert statement_timeout(ctx.db) == before


def test_fast_writes_commit_and_restore_the_session_timeout(ctx: ToolContext) -> None:
    before = statement_timeout(ctx.db)

    result = slow_registry("write", timeout=2).execute("slow", {"count": 1, "label": "Mace"}, ctx)

    assert result.ok
    assert statement_timeout(ctx.db) == before
    assert ctx.db.scalars(select(Ingredient).where(Ingredient.name == "Mace")).one()
    ctx.db.execute(text("SELECT pg_sleep(0.3)"))


def test_the_timeout_applies_inside_the_call_only(ctx: ToolContext) -> None:
    registry = slow_registry("read", timeout=0.1)

    assert registry.execute("slow", {"count": 1}, ctx).ok
    ctx.db.execute(text("SELECT pg_sleep(0.3)"))
    assert registry.execute("slow", {"count": 1000}, ctx).status is ToolStatus.TIMEOUT
    ctx.db.execute(text("SELECT pg_sleep(0.3)"))


def test_timeouts_can_be_turned_off(ctx: ToolContext) -> None:
    assert slow_registry("read", timeout=None).execute("slow", {"count": 300}, ctx).ok


def test_confirmed_actions_time_out_too(ctx: ToolContext) -> None:
    def handler(c: ToolContext, args: SpyArgs) -> dict:
        c.db.execute(text("SELECT pg_sleep(1)"))
        return {}

    registry = Registry(tool_timeout_seconds=0.1)
    registry.register(
        Tool(
            name="slow_delete",
            description="test",
            args_model=SpyArgs,
            kind="write",
            handler=handler,
            requires_confirmation=True,
        )
    )
    proposed = registry.execute("slow_delete", {"count": 1}, ctx)

    result = registry.confirm(ctx, proposed.action_id)

    assert result.status is ToolStatus.TIMEOUT
    assert ctx.db.get(ChatAction, proposed.action_id).status is ChatActionStatus.TIMEOUT


def test_other_database_errors_stay_generic(ctx: ToolContext) -> None:
    def handler(c: ToolContext, args: SpyArgs) -> dict:
        c.db.execute(text("SELECT 1 / 0"))
        return {}

    result = spy_registry(handler).execute("spy", {"count": 1}, ctx)

    assert result.status is ToolStatus.ERROR
    assert result.error["code"] == "internal_error"


def test_registry_timeout_comes_from_settings() -> None:
    from jyj.chat.router import get_chat_registry

    get_chat_registry.cache_clear()
    try:
        assert get_chat_registry().tool_timeout_seconds == 3.0
    finally:
        get_chat_registry.cache_clear()
