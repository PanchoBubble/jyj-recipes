import datetime as dt
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import (
    Ingredient,
    MealSlot,
    PlannedMeal,
    PlannedMealStatus,
    Recipe,
    RecipeIngredient,
    StockItem,
    StockMovement,
    StockReason,
    StockSource,
    User,
)
from jyj.services import auth as auth_service
from jyj.services import planned_meals
from jyj.units import Dimension

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1"
MONDAY = dt.date(2026, 10, 5)


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
    user = auth_service.create_user(db, "cook", "Cook", "correct horse battery")
    db.commit()
    return user


@pytest.fixture
def anon(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    app = create_app()

    def test_db() -> Iterator[Session]:
        yield from session_scope(session_factory)

    app.dependency_overrides[get_db] = test_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def client(anon: TestClient, db: Session, user: User) -> TestClient:
    issued = auth_service.create_session(db, user, "pytest")
    db.commit()
    anon.cookies.set(SESSION_COOKIE, issued.token)
    anon.headers.update(CSRF)
    return anon


def assert_problem(response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    return body


def ingredient(client: TestClient, name: str, dimension: str, **fields) -> dict:
    response = client.post(
        f"{API}/ingredients", json={"name": name, "dimension": dimension, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def line(ingredient_id: int, amount: str | None, unit: str) -> dict:
    return {"ingredient_id": ingredient_id, "amount_per_person": amount, "unit": unit}


def set_stock(client: TestClient, ingredient_id: int, amount: str, unit: str) -> None:
    response = client.patch(f"{API}/stock/{ingredient_id}", json={"set_to": amount, "unit": unit})
    assert response.status_code == 200, response.text


def balance(client: TestClient, ingredient_id: int) -> Decimal:
    for item in client.get(f"{API}/stock").json():
        if item["ingredient_id"] == ingredient_id:
            return Decimal(item["quantity_base"])
    return Decimal(0)


def cook(client: TestClient, meal_id: int, action: str = "cook") -> dict:
    response = client.post(f"{API}/planned-meals/{meal_id}/{action}")
    assert response.status_code == 200, response.text
    return response.json()


def by_ingredient(result: dict) -> dict[int, tuple[Decimal, Decimal]]:
    return {
        s["ingredient_id"]: (Decimal(s["delta_base"]), Decimal(s["shortfall_base"]))
        for s in result["stock"]
    }


def meal_movements(db: Session, meal_id: int) -> list[tuple[int, StockReason, Decimal, Decimal]]:
    db.expire_all()
    return [
        (m.ingredient_id, m.reason, m.delta_base, m.shortfall_base)
        for m in db.scalars(
            select(StockMovement)
            .where(StockMovement.planned_meal_id == meal_id)
            .order_by(StockMovement.id)
        )
    ]


@pytest.fixture
def pantry(client: TestClient) -> dict[str, dict]:
    return {
        "flour": ingredient(client, "Flour", "mass"),
        "milk": ingredient(client, "Milk", "volume"),
        "eggs": ingredient(client, "Eggs", "count"),
        "butter": ingredient(client, "Butter", "mass", grams_per_ml="0.9"),
        "salt": ingredient(client, "Salt", "mass"),
    }


@pytest.fixture
def meal(client: TestClient, pantry: dict[str, dict]) -> dict:
    """Pancakes for 3, per person: 100 g + 0.05 kg flour, 1/2 cup milk, 1 egg, 1 tbsp butter."""
    p = {k: v["id"] for k, v in pantry.items()}
    recipe = client.post(
        f"{API}/recipes",
        json={
            "name": "Pancakes",
            "default_servings": 3,
            "ingredients": [
                line(p["flour"], "100", "g"),
                line(p["milk"], "0.5", "cup"),
                line(p["eggs"], "1", "piece"),
                line(p["butter"], "1", "tbsp"),
                line(p["flour"], "0.05", "kg"),
                line(p["salt"], None, "to_taste"),
            ],
        },
    )
    assert recipe.status_code == 201, recipe.text
    slot = client.get(f"{API}/meal-slots").json()[0]
    body = {"date": MONDAY.isoformat(), "slot_id": slot["id"], "recipe_id": recipe.json()["id"]}
    response = client.post(f"{API}/planned-meals", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def stocked(client: TestClient, pantry: dict[str, dict]) -> dict[str, int]:
    set_stock(client, pantry["flour"]["id"], "1", "kg")
    set_stock(client, pantry["milk"]["id"], "1", "l")
    set_stock(client, pantry["eggs"]["id"], "6", "piece")
    set_stock(client, pantry["butter"]["id"], "250", "g")
    return {k: v["id"] for k, v in pantry.items()}


# --- auth and CSRF -------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["cook", "uncook"])
def test_cook_requires_a_session(anon: TestClient, action: str) -> None:
    assert_problem(anon.post(f"{API}/planned-meals/1/{action}", headers=CSRF), 401)


@pytest.mark.parametrize("action", ["cook", "uncook"])
def test_cook_requires_csrf_header(client: TestClient, meal: dict, action: str) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(client.post(f"{API}/planned-meals/{meal['id']}/{action}"), 403)
    client.headers.update(CSRF)
    assert client.get(f"{API}/planned-meals/{meal['id']}").json()["status"] == "planned"


@pytest.mark.parametrize("action", ["cook", "uncook"])
def test_cook_unknown_meal_is_404(client: TestClient, action: str) -> None:
    assert_problem(client.post(f"{API}/planned-meals/999999/{action}"), 404)


# --- cook ----------------------------------------------------------------------------------


def test_cook_deducts_scaled_amounts_across_units(
    client: TestClient, db: Session, user: User, meal: dict, stocked: dict[str, int]
) -> None:
    result = cook(client, meal["id"])

    assert result["changed"] is True
    assert result["meal"]["status"] == "cooked"
    assert result["meal"]["cooked_by"] == user.id
    assert result["meal"]["cooked_at"] is not None
    assert by_ingredient(result) == {
        stocked["flour"]: (Decimal("-450"), Decimal(0)),
        stocked["milk"]: (Decimal("-360"), Decimal(0)),
        stocked["eggs"]: (Decimal("-3"), Decimal(0)),
        stocked["butter"]: (Decimal("-40.5"), Decimal(0)),
    }
    flour = next(s for s in result["stock"] if s["ingredient_id"] == stocked["flour"])
    assert flour["name"] == "Flour"
    assert flour["base_unit"] == "g"
    assert flour["display"] == {"amount": "-450.000", "unit": "g"}

    assert balance(client, stocked["flour"]) == Decimal(550)
    assert balance(client, stocked["milk"]) == Decimal(640)
    assert balance(client, stocked["eggs"]) == Decimal(3)
    assert balance(client, stocked["butter"]) == Decimal("209.5")
    assert all(reason is StockReason.COOKED for _, reason, _, _ in meal_movements(db, meal["id"]))
    assert len(meal_movements(db, meal["id"])) == 4


def test_to_taste_lines_are_skipped_and_reported(
    client: TestClient, meal: dict, stocked: dict[str, int]
) -> None:
    result = cook(client, meal["id"])

    assert [(s["name"], s["amount"], s["unit"], s["reason"]) for s in result["skipped"]] == [
        ("Salt", None, "to_taste", "to_taste")
    ]
    assert stocked["salt"] not in by_ingredient(result)
    assert balance(client, stocked["salt"]) == 0


def test_unconvertible_lines_are_reported_not_guessed(
    client: TestClient, db: Session, pantry: dict[str, dict], stocked: dict[str, int]
) -> None:
    garlic = ingredient(client, "Garlic", "count", grams_per_piece="5")
    oil = ingredient(client, "Oil", "mass")
    recipe = client.post(
        f"{API}/recipes",
        json={
            "name": "Aioli",
            "default_servings": 2,
            "ingredients": [
                line(garlic["id"], "10", "g"),
                line(oil["id"], "2", "pinch"),
                line(pantry["eggs"]["id"], "1", "piece"),
            ],
        },
    ).json()
    set_stock(client, garlic["id"], "4", "piece")
    # The recipe was valid when saved; the factor it relied on is gone by cooking time. The API
    # refuses that edit now, so reach past it to model data saved before the guard existed.
    db.execute(update(Ingredient).where(Ingredient.id == garlic["id"]).values(grams_per_piece=None))
    db.commit()
    slot = client.get(f"{API}/meal-slots").json()[0]
    meal = client.post(
        f"{API}/planned-meals",
        json={"date": MONDAY.isoformat(), "slot_id": slot["id"], "recipe_id": recipe["id"]},
    ).json()

    result = cook(client, meal["id"])

    assert [
        (s["ingredient_id"], s["amount"], s["unit"], s["reason"]) for s in result["skipped"]
    ] == [
        (garlic["id"], "20.000", "g", "missing_grams_per_piece"),
        (oil["id"], "4.000", "pinch", "dimensionless"),
    ]
    assert by_ingredient(result) == {stocked["eggs"]: (Decimal(-2), Decimal(0))}
    assert balance(client, garlic["id"]) == Decimal(4)


def test_cook_floors_at_zero_and_records_shortfall(
    client: TestClient, meal: dict, stocked: dict[str, int]
) -> None:
    set_stock(client, stocked["eggs"], "2", "piece")

    result = cook(client, meal["id"])

    assert by_ingredient(result)[stocked["eggs"]] == (Decimal(-2), Decimal(1))
    assert balance(client, stocked["eggs"]) == 0


def test_cook_creates_stock_for_ingredients_never_stocked(
    client: TestClient, meal: dict, pantry: dict[str, dict]
) -> None:
    result = cook(client, meal["id"])

    assert by_ingredient(result)[pantry["flour"]["id"]] == (Decimal(0), Decimal(450))
    assert balance(client, pantry["flour"]["id"]) == 0


def test_double_cook_is_a_noop(
    client: TestClient, db: Session, meal: dict, stocked: dict[str, int]
) -> None:
    set_stock(client, stocked["eggs"], "2", "piece")
    first = cook(client, meal["id"])
    before = meal_movements(db, meal["id"])

    second = cook(client, meal["id"])

    assert second["changed"] is False
    assert second["stock"] == first["stock"]
    assert second["skipped"] == first["skipped"]
    assert second["meal"]["cooked_at"] == first["meal"]["cooked_at"]
    assert meal_movements(db, meal["id"]) == before
    assert balance(client, stocked["eggs"]) == 0


def test_skipped_meal_can_be_cooked(client: TestClient, meal: dict, stocked: dict) -> None:
    client.patch(f"{API}/planned-meals/{meal['id']}", json={"status": "skipped"})

    assert cook(client, meal["id"])["meal"]["status"] == "cooked"
    assert balance(client, stocked["eggs"]) == Decimal(3)


# --- uncook --------------------------------------------------------------------------------


def test_uncook_restores_balances_exactly_after_a_shortfall(
    client: TestClient, db: Session, meal: dict, stocked: dict[str, int]
) -> None:
    set_stock(client, stocked["eggs"], "2", "piece")
    cook(client, meal["id"])

    result = cook(client, meal["id"], "uncook")

    assert result["changed"] is True
    assert result["skipped"] == []
    assert result["meal"]["status"] == "planned"
    assert result["meal"]["cooked_at"] is None
    assert result["meal"]["cooked_by"] is None
    assert by_ingredient(result) == {
        stocked["flour"]: (Decimal(450), Decimal(0)),
        stocked["milk"]: (Decimal(360), Decimal(0)),
        stocked["eggs"]: (Decimal(2), Decimal(0)),
        stocked["butter"]: (Decimal("40.5"), Decimal(0)),
    }
    assert balance(client, stocked["flour"]) == Decimal(1000)
    assert balance(client, stocked["milk"]) == Decimal(1000)
    assert balance(client, stocked["eggs"]) == Decimal(2)
    assert balance(client, stocked["butter"]) == Decimal(250)
    movements = meal_movements(db, meal["id"])
    cooked = sorted((i, d) for i, r, d, _ in movements if r is StockReason.COOKED)
    undone = sorted((i, -d) for i, r, d, _ in movements if r is StockReason.UNDO)
    assert undone == cooked


def test_uncook_skips_ingredients_that_were_fully_short(
    client: TestClient, db: Session, meal: dict, stocked: dict[str, int]
) -> None:
    set_stock(client, stocked["eggs"], "0", "piece")
    cook(client, meal["id"])

    result = cook(client, meal["id"], "uncook")

    assert stocked["eggs"] not in by_ingredient(result)
    assert balance(client, stocked["eggs"]) == 0
    reasons = [(i, r) for i, r, _, _ in meal_movements(db, meal["id"])]
    assert (stocked["eggs"], StockReason.UNDO) not in reasons


def test_uncook_of_a_planned_meal_is_a_noop(client: TestClient, db: Session, meal: dict) -> None:
    result = cook(client, meal["id"], "uncook")

    assert result["changed"] is False
    assert result["stock"] == []
    assert result["meal"]["status"] == "planned"
    assert meal_movements(db, meal["id"]) == []


def test_double_uncook_is_a_noop(
    client: TestClient, db: Session, meal: dict, stocked: dict[str, int]
) -> None:
    cook(client, meal["id"])
    cook(client, meal["id"], "uncook")
    before = meal_movements(db, meal["id"])

    assert cook(client, meal["id"], "uncook")["changed"] is False
    assert meal_movements(db, meal["id"]) == before
    assert balance(client, stocked["eggs"]) == Decimal(6)


def test_uncook_after_manual_edits_reverses_only_what_cook_applied(
    client: TestClient, meal: dict, stocked: dict[str, int]
) -> None:
    cook(client, meal["id"])
    set_stock(client, stocked["eggs"], "1", "piece")
    client.patch(f"{API}/stock/{stocked['flour']}", json={"delta": "-50", "unit": "g"})

    cook(client, meal["id"], "uncook")

    assert balance(client, stocked["eggs"]) == Decimal(4)
    assert balance(client, stocked["flour"]) == Decimal(950)


def test_recook_after_manual_edits_then_uncook_reverses_only_the_latest_cook(
    client: TestClient, meal: dict, stocked: dict[str, int]
) -> None:
    cook(client, meal["id"])
    cook(client, meal["id"], "uncook")
    set_stock(client, stocked["eggs"], "1", "piece")

    again = cook(client, meal["id"])
    assert by_ingredient(again)[stocked["eggs"]] == (Decimal(-1), Decimal(2))
    assert balance(client, stocked["eggs"]) == 0
    assert cook(client, meal["id"])["stock"] == again["stock"]

    undone = cook(client, meal["id"], "uncook")

    assert by_ingredient(undone)[stocked["eggs"]] == (Decimal(1), Decimal(0))
    assert balance(client, stocked["eggs"]) == Decimal(1)
    assert balance(client, stocked["flour"]) == Decimal(1000)


def test_ledger_still_sums_to_balances(
    client: TestClient, db: Session, meal: dict, stocked: dict[str, int]
) -> None:
    set_stock(client, stocked["eggs"], "2", "piece")
    cook(client, meal["id"])
    cook(client, meal["id"], "uncook")
    cook(client, meal["id"])

    db.expire_all()
    for ingredient_id in stocked.values():
        total = db.scalar(
            select(func.coalesce(func.sum(StockMovement.delta_base), 0)).where(
                StockMovement.ingredient_id == ingredient_id
            )
        )
        assert Decimal(total) == balance(client, ingredient_id)


# --- guards and status ---------------------------------------------------------------------


def test_cooked_meal_cannot_be_deleted_until_uncooked(client: TestClient, meal: dict) -> None:
    cook(client, meal["id"])

    body = assert_problem(client.delete(f"{API}/planned-meals/{meal['id']}"), 409)
    assert "uncook" in body["detail"]

    cook(client, meal["id"], "uncook")
    assert client.delete(f"{API}/planned-meals/{meal['id']}").status_code == 204


def test_patch_status_skipped_and_back(client: TestClient, meal: dict) -> None:
    path = f"{API}/planned-meals/{meal['id']}"
    skipped = client.patch(path, json={"status": "skipped"})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "skipped"

    planned = client.patch(path, json={"status": "planned"})
    assert planned.json()["status"] == "planned"


def test_patch_cannot_set_or_leave_cooked(client: TestClient, meal: dict) -> None:
    path = f"{API}/planned-meals/{meal['id']}"
    assert_problem(client.patch(path, json={"status": "cooked"}), 422)

    cook(client, meal["id"])
    body = assert_problem(client.patch(path, json={"status": "skipped"}), 409)
    assert "uncook" in body["detail"]
    assert client.patch(path, json={"status": "planned"}).status_code == 409


# --- concurrency (real commits) ------------------------------------------------------------


@pytest.fixture
def committed_meal(db_engine: Engine) -> Iterator[tuple[sessionmaker[Session], User, int, int]]:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:12]
    with factory() as db:
        user = User(username=f"cook-{tag}", display_name="Cook", password_hash="x")
        eggs = Ingredient(name=f"Eggs {tag}", dimension=Dimension.COUNT, default_unit="piece")
        db.add_all([user, eggs])
        db.flush()
        recipe = Recipe(name=f"Omelette {tag}", created_by=user.id, default_servings=2)
        recipe.ingredients.append(
            RecipeIngredient(
                ingredient_id=eggs.id,
                amount_per_person=Decimal(2),
                unit_code="piece",
                unit_dimension=Dimension.COUNT,
                position=0,
            )
        )
        db.add(recipe)
        db.flush()
        slot_id = db.scalar(select(MealSlot.id).order_by(MealSlot.id).limit(1))
        meal = PlannedMeal(
            date=MONDAY,
            slot_id=slot_id,
            recipe_id=recipe.id,
            servings=2,
            position=0,
            status=PlannedMealStatus.PLANNED,
            created_by=user.id,
        )
        db.add(meal)
        db.add(StockItem(ingredient_id=eggs.id, quantity_base=Decimal(10)))
        db.add(
            StockMovement(
                ingredient_id=eggs.id,
                delta_base=Decimal(10),
                reason=StockReason.PURCHASED,
                user_id=user.id,
                source=StockSource.UI,
            )
        )
        db.commit()
        ids = (user, meal.id, eggs.id, recipe.id)
    try:
        yield factory, ids[0], ids[1], ids[2]
    finally:
        with factory() as db:
            db.execute(delete(StockMovement).where(StockMovement.ingredient_id == ids[2]))
            db.execute(delete(StockItem).where(StockItem.ingredient_id == ids[2]))
            db.execute(delete(PlannedMeal).where(PlannedMeal.id == ids[1]))
            db.execute(delete(Recipe).where(Recipe.id == ids[3]))
            db.execute(delete(Ingredient).where(Ingredient.id == ids[2]))
            db.execute(delete(User).where(User.id == user.id))
            db.commit()


@pytest.mark.parametrize("action", ["cook", "uncook"])
def test_concurrent_cook_or_uncook_applies_once(
    committed_meal: tuple[sessionmaker[Session], User, int, int], action: str
) -> None:
    factory, user, meal_id, eggs_id = committed_meal
    if action == "uncook":
        with factory() as db:
            planned_meals.cook(db, user, StockSource.UI, meal_id)
            db.commit()
    run = getattr(planned_meals, action)
    barrier = threading.Barrier(2)

    def attempt(source: StockSource) -> bool:
        with factory() as db:
            barrier.wait(timeout=10)
            changed = run(db, user, source, meal_id).changed
            db.commit()
            return changed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, s) for s in (StockSource.UI, StockSource.CHAT)]
        results = sorted(f.result(timeout=30) for f in futures)

    assert results == [False, True]
    with factory() as db:
        qty = db.scalar(select(StockItem.quantity_base).where(StockItem.ingredient_id == eggs_id))
        reasons = db.scalars(
            select(StockMovement.reason).where(StockMovement.planned_meal_id == meal_id)
        ).all()
    expected = Decimal(6) if action == "cook" else Decimal(10)
    assert qty == expected
    assert reasons.count(StockReason.COOKED) == 1
    assert reasons.count(StockReason.UNDO) == (1 if action == "uncook" else 0)
