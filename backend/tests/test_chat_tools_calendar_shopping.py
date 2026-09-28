import ast
import datetime as dt
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session, sessionmaker

from jyj.chat.tools import Registry, ToolContext, ToolStatus, build_registry
from jyj.chat.tools import shopping as shopping_tools
from jyj.models import (
    ChatAction,
    ChatActionStatus,
    ChatConversation,
    Ingredient,
    PlannedMeal,
    Recipe,
    ShoppingList,
    StockMovement,
    StockReason,
    User,
)
from jyj.services import auth as auth_service
from jyj.services import ingredients as ingredients_service
from jyj.services import meal_slots as slots_service
from jyj.services import planned_meals as meals_service
from jyj.services import recipes as recipes_service
from jyj.services import stock as stock_service

TOOLS_DIR = Path(__file__).resolve().parent.parent / "src" / "jyj" / "chat" / "tools"
MONDAY = dt.date(2026, 10, 5)
TUESDAY = MONDAY + dt.timedelta(days=1)


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
def ctx(db: Session, user: User) -> ToolContext:
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    return ToolContext(db=db, user=user, conversation_id=conversation.id)


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shopping_tools, "today", lambda: MONDAY)


@pytest.fixture
def flour(ctx: ToolContext) -> Ingredient:
    return ingredients_service.create_ingredient(
        ctx.db, ctx.user, ctx.source, name="Flour", dimension="mass"
    )


@pytest.fixture
def recipe(ctx: ToolContext, flour: Ingredient) -> Recipe:
    salt = ingredients_service.create_ingredient(
        ctx.db, ctx.user, ctx.source, name="Salt", dimension="mass"
    )
    return recipes_service.create_recipe(
        ctx.db,
        ctx.user,
        ctx.source,
        name="Bread",
        default_servings=2,
        ingredients=[
            {"ingredient_id": flour.id, "amount_per_person": Decimal(250), "unit": "g"},
            {"ingredient_id": salt.id, "amount_per_person": None, "unit": "pinch"},
        ],
    )


def plan(registry: Registry, ctx: ToolContext, recipe: Recipe, **overrides) -> dict:
    args = {
        "date": MONDAY.isoformat(),
        "slot": "Dinner",
        "recipe_id": recipe.id,
        "servings": None,
    }
    result = registry.execute("plan_meal", {**args, **overrides}, ctx)
    assert result.status is ToolStatus.SUCCESS, result.error
    return result.data


def assert_rejected(result, code: str = "invalid_arguments") -> dict:
    assert result.status is ToolStatus.REJECTED, result
    assert result.error["code"] == code
    return result.error


# --- calendar -------------------------------------------------------------------------------


def test_plan_meal_resolves_slot_names_case_insensitively(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    meal = plan(registry, ctx, recipe, slot="  dINNER ")

    assert meal == {
        "meal_id": meal["meal_id"],
        "date": "2026-10-05",
        "position": 0,
        "slot": "Dinner",
        "recipe_id": recipe.id,
        "recipe": "Bread",
        "servings": 2,
        "status": "planned",
    }
    stored = ctx.db.get(PlannedMeal, meal["meal_id"])
    assert stored.created_by == ctx.user.id


def test_plan_meal_without_slot_appends_to_the_day(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    labelled = plan(registry, ctx, recipe, slot="Tea")
    unlabelled = plan(registry, ctx, recipe, slot=None)

    assert (unlabelled["slot"], unlabelled["position"]) == (None, 1)
    assert labelled["position"] == 0
    assert ctx.db.get(PlannedMeal, unlabelled["meal_id"]).slot_id is None


def test_plan_meal_accepts_slot_ids_and_servings(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    lunch = next(s for s in slots_service.list_slots(ctx.db) if s.name == "Lunch")

    meal = plan(registry, ctx, recipe, slot=lunch.id, servings=4)

    assert (meal["slot"], meal["servings"]) == ("Lunch", 4)


def test_plan_meal_rejects_unknown_slots(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    for slot in ("Brunch", 999_999):
        result = registry.execute(
            "plan_meal",
            {"date": "2026-10-05", "slot": slot, "recipe_id": recipe.id, "servings": None},
            ctx,
        )
        error = assert_rejected(result, "unknown_slot")
        assert "Lunch, Dinner, Tea" in error["message"]
    assert ctx.db.scalars(select(PlannedMeal)).all() == []


@pytest.mark.parametrize(
    "date",
    ["2026-10-05T10:00:00", "05/10/2026", "tomorrow", "20261005", 1_791_158_400, "2026-02-30"],
)
def test_dates_must_be_iso(
    registry: Registry, ctx: ToolContext, recipe: Recipe, date: object
) -> None:
    result = registry.execute(
        "plan_meal", {"date": date, "slot": "Dinner", "recipe_id": recipe.id, "servings": None}, ctx
    )

    error = assert_rejected(result)
    assert error["details"][0]["field"] == "date"


def test_plan_meal_surfaces_service_errors(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    missing = registry.execute(
        "plan_meal",
        {"date": "2026-10-05", "slot": "Dinner", "recipe_id": 999_999, "servings": None},
        ctx,
    )
    assert_rejected(missing, "not_found")

    too_many = registry.execute(
        "plan_meal",
        {"date": "2026-10-05", "slot": "Dinner", "recipe_id": recipe.id, "servings": 0},
        ctx,
    )
    assert_rejected(too_many)


def test_get_plan_is_compact_and_ordered(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    dinner = plan(registry, ctx, recipe, slot="Dinner")
    lunch = plan(registry, ctx, recipe, slot="Lunch")
    unlabelled = plan(registry, ctx, recipe, slot=None)
    later = plan(registry, ctx, recipe, date=TUESDAY.isoformat(), servings=3)
    plan(registry, ctx, recipe, date="2026-11-30")

    result = registry.execute("get_plan", {"from": "2026-10-05", "to": "2026-10-06"}, ctx)

    assert result.status is ToolStatus.SUCCESS
    assert result.data["from"] == "2026-10-05" and result.data["to"] == "2026-10-06"
    assert [(m["meal_id"], m["position"], m["slot"]) for m in result.data["meals"]] == [
        (dinner["meal_id"], 0, "Dinner"),
        (lunch["meal_id"], 1, "Lunch"),
        (unlabelled["meal_id"], 2, None),
        (later["meal_id"], 0, "Dinner"),
    ]
    assert result.data["meals"][3] == later


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ({"from": "2026-10-05", "to": "2026-10-04"}, "args"),
        ({"from": "2026-10-01", "to": "2026-11-01"}, "args"),
        ({"from": "2026-10-01"}, "to"),
        ({"start": "2026-10-01", "end": "2026-10-02"}, "from"),
        ({"from": "next week", "to": "2026-10-02"}, "from"),
    ],
    ids=["reversed", "over-31-days", "missing-to", "wrong-names", "not-iso"],
)
@pytest.mark.parametrize("tool", ["get_plan", "preview_shopping", "create_shopping_list"])
def test_ranges_are_validated(
    registry: Registry, ctx: ToolContext, tool: str, args: dict, field: str
) -> None:
    error = assert_rejected(registry.execute(tool, args, ctx))
    assert error["details"][0]["field"] == field
    assert ctx.db.scalars(select(ShoppingList)).all() == []


def test_get_plan_allows_exactly_31_days(registry: Registry, ctx: ToolContext) -> None:
    result = registry.execute("get_plan", {"from": "2026-10-01", "to": "2026-10-31"}, ctx)
    assert result.status is ToolStatus.SUCCESS
    assert result.data["meals"] == []


def test_move_meal(registry: Registry, ctx: ToolContext, recipe: Recipe) -> None:
    a, b, c = (plan(registry, ctx, recipe)["meal_id"] for _ in range(3))

    def move(meal_id: int, date: str, position: int | None) -> dict:
        result = registry.execute(
            "move_meal", {"meal_id": meal_id, "date": date, "position": position}, ctx
        )
        assert result.status is ToolStatus.SUCCESS, result.error
        return result.data

    def order(date: dt.date) -> list[int]:
        return [m.id for m in meals_service.list_planned_meals(ctx.db, date, date)]

    reordered = move(c, "2026-10-05", 0)
    assert (reordered["date"], reordered["position"], reordered["slot"]) == (
        "2026-10-05",
        0,
        "Dinner",
    )
    assert order(MONDAY) == [c, a, b]

    in_place = move(a, "2026-10-05", None)
    assert in_place["position"] == 1

    appended = move(c, "2026-10-06", None)
    assert (appended["date"], appended["position"]) == ("2026-10-06", 0)
    placed = move(b, "2026-10-06", 0)
    assert placed["position"] == 0
    assert order(MONDAY) == [a]
    assert order(TUESDAY) == [b, c]


def test_move_meal_validation(registry: Registry, ctx: ToolContext, recipe: Recipe) -> None:
    meal = plan(registry, ctx, recipe)

    assert_rejected(
        registry.execute(
            "move_meal", {"meal_id": meal["meal_id"], "date": None, "position": 0}, ctx
        )
    )
    assert_rejected(
        registry.execute(
            "move_meal", {"meal_id": meal["meal_id"], "date": "2026-10-06", "position": -1}, ctx
        )
    )
    assert_rejected(
        registry.execute(
            "move_meal", {"meal_id": meal["meal_id"], "date": "2026-10-06", "slot": "Tea"}, ctx
        )
    )
    assert_rejected(
        registry.execute(
            "move_meal", {"meal_id": 999_999, "date": "2026-10-06", "position": None}, ctx
        ),
        "not_found",
    )
    ctx.db.expire_all()
    stored = ctx.db.get(PlannedMeal, meal["meal_id"])
    assert (stored.date, stored.slot.name) == (MONDAY, "Dinner")


def test_set_meal_slot(registry: Registry, ctx: ToolContext, recipe: Recipe) -> None:
    first = plan(registry, ctx, recipe, slot=None)
    second = plan(registry, ctx, recipe)

    labelled = registry.execute(
        "set_meal_slot", {"meal_id": first["meal_id"], "slot": "  lunch"}, ctx
    )
    assert labelled.status is ToolStatus.SUCCESS, labelled.error
    assert (labelled.data["slot"], labelled.data["position"]) == ("Lunch", 0)

    cleared = registry.execute("set_meal_slot", {"meal_id": second["meal_id"], "slot": None}, ctx)
    assert (cleared.data["slot"], cleared.data["position"]) == (None, 1)
    ctx.db.expire_all()
    assert ctx.db.get(PlannedMeal, second["meal_id"]).slot_id is None


def test_set_meal_slot_validation(registry: Registry, ctx: ToolContext, recipe: Recipe) -> None:
    meal = plan(registry, ctx, recipe)

    assert_rejected(registry.execute("set_meal_slot", {"meal_id": meal["meal_id"]}, ctx))
    assert_rejected(
        registry.execute("set_meal_slot", {"meal_id": meal["meal_id"], "slot": "Supper"}, ctx),
        "unknown_slot",
    )
    assert_rejected(
        registry.execute("set_meal_slot", {"meal_id": 999_999, "slot": None}, ctx), "not_found"
    )
    ctx.db.expire_all()
    assert ctx.db.get(PlannedMeal, meal["meal_id"]).slot.name == "Dinner"


def test_set_servings(registry: Registry, ctx: ToolContext, recipe: Recipe) -> None:
    meal = plan(registry, ctx, recipe)

    result = registry.execute("set_servings", {"meal_id": meal["meal_id"], "servings": 5}, ctx)
    assert result.status is ToolStatus.SUCCESS
    assert result.data["servings"] == 5

    assert_rejected(
        registry.execute("set_servings", {"meal_id": meal["meal_id"], "servings": None}, ctx)
    )
    registry.execute("mark_cooked", {"meal_id": meal["meal_id"]}, ctx)
    assert_rejected(
        registry.execute("set_servings", {"meal_id": meal["meal_id"], "servings": 2}, ctx),
        "conflict",
    )


def test_remove_meal_waits_for_confirmation(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    meal = plan(registry, ctx, recipe)

    proposed = registry.execute("remove_meal", {"meal_id": meal["meal_id"]}, ctx)

    assert proposed.status is ToolStatus.NEEDS_CONFIRMATION
    assert proposed.data == meal
    assert ctx.db.get(PlannedMeal, meal["meal_id"]) is not None
    assert ctx.db.get(ChatAction, proposed.action_id).status is ChatActionStatus.PROPOSED

    confirmed = registry.confirm(ctx, proposed.action_id)

    assert confirmed.status is ToolStatus.SUCCESS
    assert confirmed.data == {"meal_id": meal["meal_id"], "removed": True}
    ctx.db.expunge_all()
    assert ctx.db.get(PlannedMeal, meal["meal_id"]) is None
    assert ctx.db.get(ChatAction, proposed.action_id).status is ChatActionStatus.EXECUTED
    assert_rejected(registry.confirm(ctx, proposed.action_id), "not_pending")


def test_remove_meal_rejects_missing_and_cooked_meals(
    registry: Registry, ctx: ToolContext, recipe: Recipe
) -> None:
    assert_rejected(registry.execute("remove_meal", {"meal_id": 999_999}, ctx), "not_found")

    meal = plan(registry, ctx, recipe)
    proposed = registry.execute("remove_meal", {"meal_id": meal["meal_id"]}, ctx)
    meals_service.cook(ctx.db, ctx.user, ctx.source, meal["meal_id"])

    assert_rejected(registry.confirm(ctx, proposed.action_id), "conflict")
    assert ctx.db.get(PlannedMeal, meal["meal_id"]) is not None


def test_mark_cooked_reports_stock_impact_and_uncook_returns_it(
    registry: Registry, ctx: ToolContext, recipe: Recipe, flour: Ingredient
) -> None:
    stock_service.adjust_stock(ctx.db, ctx.user, ctx.source, flour.id, Decimal(300), "g")
    meal = plan(registry, ctx, recipe)

    cooked = registry.execute("mark_cooked", {"meal_id": meal["meal_id"]}, ctx)

    assert cooked.status is ToolStatus.SUCCESS
    assert cooked.data["changed"] is True
    assert cooked.data["meal"]["status"] == "cooked"
    assert cooked.data["stock_changes"] == [
        {"ingredient_id": flour.id, "name": "Flour", "change": "-300 g", "shortfall": "200 g"}
    ]
    assert cooked.data["skipped"] == [
        {"ingredient_id": recipe.ingredients[1].ingredient_id, "name": "Salt", "reason": "to_taste"}
    ]
    assert ingredients_service.stock_of(ctx.db, flour.id) == 0
    movement = ctx.db.scalars(
        select(StockMovement).where(StockMovement.reason == StockReason.COOKED)
    ).one()
    assert (movement.source.value, movement.user_id) == ("chat", ctx.user.id)

    again = registry.execute("mark_cooked", {"meal_id": meal["meal_id"]}, ctx)
    assert again.data["changed"] is False
    assert ingredients_service.stock_of(ctx.db, flour.id) == 0

    uncooked = registry.execute("uncook_meal", {"meal_id": meal["meal_id"]}, ctx)

    assert uncooked.status is ToolStatus.SUCCESS
    assert uncooked.data["meal"]["status"] == "planned"
    assert uncooked.data["stock_changes"] == [
        {"ingredient_id": flour.id, "name": "Flour", "change": "300 g"}
    ]
    assert ingredients_service.stock_of(ctx.db, flour.id) == 300

    not_cooked = registry.execute("uncook_meal", {"meal_id": meal["meal_id"]}, ctx)
    assert not_cooked.data["changed"] is False


def test_cook_tools_reject_missing_meals(registry: Registry, ctx: ToolContext) -> None:
    for tool in ("mark_cooked", "uncook_meal"):
        assert_rejected(registry.execute(tool, {"meal_id": 999_999}, ctx), "not_found")
        assert_rejected(registry.execute(tool, {"meal_id": "1; drop"}, ctx))


# --- shopping -------------------------------------------------------------------------------


def test_preview_shopping_is_compact_and_saves_nothing(
    registry: Registry, ctx: ToolContext, recipe: Recipe, flour: Ingredient
) -> None:
    stock_service.adjust_stock(ctx.db, ctx.user, ctx.source, flour.id, Decimal(500), "g")
    plan(registry, ctx, recipe, servings=4)
    plan(registry, ctx, recipe, date=TUESDAY.isoformat(), servings=4)

    result = registry.execute("preview_shopping", {"from": "2026-10-05", "to": "2026-10-06"}, ctx)

    assert result.status is ToolStatus.SUCCESS
    assert result.data == {
        "from": "2026-10-05",
        "to": "2026-10-06",
        "to_buy": [{"ingredient_id": flour.id, "name": "Flour", "buy": "1.5 kg", "category": None}],
        "unconverted_count": 0,
        "to_taste_count": 1,
    }
    assert ctx.db.scalars(select(ShoppingList)).all() == []


def test_create_shopping_list(
    registry: Registry, ctx: ToolContext, recipe: Recipe, flour: Ingredient
) -> None:
    plan(registry, ctx, recipe)

    result = registry.execute(
        "create_shopping_list", {"from": "2026-10-05", "to": "2026-10-11"}, ctx
    )

    assert result.status is ToolStatus.SUCCESS
    created = ctx.db.get(ShoppingList, result.data["list_id"])
    assert result.data == {
        "list_id": created.id,
        "from": "2026-10-05",
        "to": "2026-10-11",
        "item_count": 2,
        "to_buy_count": 1,
    }
    assert created.created_by == ctx.user.id
    assert created.status.value == "open"
    assert not ctx.db.scalars(
        select(StockMovement).where(StockMovement.reason == StockReason.PURCHASED)
    ).all()


def test_no_tool_can_complete_shopping_or_buy_stock(registry: Registry) -> None:
    shopping_names = {n for n in registry.names if "shopping" in n or "list" in n}
    assert shopping_names == {"preview_shopping", "create_shopping_list", "list_ingredients"}
    for name in registry.names:
        assert not any(word in name for word in ("complete", "check", "purchase", "buy")), name

    for module in ("shopping.py", "calendar.py"):
        tree = ast.parse((TOOLS_DIR / module).read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for forbidden in (
            "complete_list",
            "update_item",
            "delete_list",
            "PURCHASED",
            "adjust_stock",
        ):
            assert forbidden not in attrs | names, (module, forbidden)
