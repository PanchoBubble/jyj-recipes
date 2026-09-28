import datetime as dt
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.api.shopping import get_today
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import (
    RecipeIngredient,
    ShoppingListItem,
    StockMovement,
    StockReason,
    StockSource,
    User,
)
from jyj.services import auth as auth_service
from jyj.units import Dimension

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1"
TODAY = dt.date(2026, 10, 1)
START = dt.date(2026, 10, 5)
END = dt.date(2026, 10, 11)
RANGE = {"from": START.isoformat(), "to": END.isoformat()}


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
    user = auth_service.create_user(db, "shopper", "Shopper", "correct horse battery")
    db.commit()
    return user


@pytest.fixture
def anon(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    app = create_app()

    def test_db() -> Iterator[Session]:
        yield from session_scope(session_factory)

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_today] = lambda: TODAY
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


def ok(response, status: int = 200) -> dict:
    assert response.status_code == status, response.text
    return response.json() if response.content else {}


@pytest.fixture
def kitchen(client: TestClient, db: Session) -> dict[str, int]:
    """Flour/eggs to buy, milk to buy plus an unconvertible line, sugar covered, salt to taste.

    Planned for START: Pancakes x2 (lunch) and Bread x2 (dinner). Stock: 300 g flour, 1 kg
    sugar. A planned meal on TODAY reserves 100 g of the flour first.
    """

    def ingredient(name: str, dimension: str, **fields) -> int:
        body = {"name": name, "dimension": dimension, **fields}
        return ok(client.post(f"{API}/ingredients", json=body), 201)["id"]

    ids = {
        "flour": ingredient("Flour", "mass", category="Baking"),
        "sugar": ingredient("Sugar", "mass", category="Baking"),
        "milk": ingredient("Milk", "volume", category="Dairy"),
        "eggs": ingredient("Eggs", "count", category="Dairy", grams_per_piece="50"),
        "salt": ingredient("Salt", "mass"),
    }

    def recipe(name: str, *lines: tuple[str, str | None, str]) -> int:
        body = {
            "name": name,
            "default_servings": 1,
            "ingredients": [
                {"ingredient_id": ids[i], "amount_per_person": a, "unit": u} for i, a, u in lines
            ],
        }
        return ok(client.post(f"{API}/recipes", json=body), 201)["id"]

    pancakes = recipe(
        "Pancakes",
        ("flour", "100", "g"),
        ("sugar", "10", "g"),
        ("milk", "0.2", "l"),
        ("eggs", "1", "piece"),
        ("salt", None, "to_taste"),
    )
    bread = recipe("Bread", ("flour", "0.25", "kg"))
    # The recipes API refuses unconvertible lines; one can still exist once an ingredient's
    # conversion factor is removed, so seed it directly.
    db.add(
        RecipeIngredient(
            recipe_id=bread,
            ingredient_id=ids["milk"],
            amount_per_person=Decimal("50"),
            unit_code="g",
            unit_dimension=Dimension.MASS,
            position=1,
        )
    )
    db.commit()
    slots = {s["name"].lower(): s["id"] for s in ok(client.get(f"{API}/meal-slots"))}

    def plan(day: dt.date, slot: str, recipe_id: int, servings: int) -> None:
        body = {
            "date": day.isoformat(),
            "slot_id": slots[slot],
            "recipe_id": recipe_id,
            "servings": servings,
        }
        ok(client.post(f"{API}/planned-meals", json=body), 201)

    plan(START, "lunch", pancakes, 2)
    plan(START, "dinner", bread, 2)
    plan(TODAY, "lunch", pancakes, 1)
    for key, amount in (("flour", "300"), ("sugar", "1000")):
        ok(client.patch(f"{API}/stock/{ids[key]}", json={"delta": amount, "unit": "g"}))
    return ids


def stock_of(client: TestClient, ingredient_id: int) -> str:
    items = ok(client.get(f"{API}/stock"))
    return next((i["quantity_base"] for i in items if i["ingredient_id"] == ingredient_id), "0")


def purchases(db: Session) -> list[tuple[int, str, int | None]]:
    db.expire_all()
    rows = db.scalars(
        select(StockMovement)
        .where(StockMovement.reason == StockReason.PURCHASED)
        .order_by(StockMovement.id)
    )
    return [(m.ingredient_id, str(m.delta_base), m.shopping_list_id) for m in rows]


def items_by_name(shopping_list: dict) -> dict[str, dict]:
    return {i["ingredient_name"]: i for i in shopping_list["items"]}


# --- auth and CSRF -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/shopping/preview"),
        ("POST", "/shopping-lists"),
        ("GET", "/shopping-lists"),
        ("GET", "/shopping-lists/1"),
        ("DELETE", "/shopping-lists/1"),
        ("PATCH", "/shopping-lists/1/items/1"),
        ("POST", "/shopping-lists/1/complete"),
    ],
)
def test_endpoints_require_a_session(anon: TestClient, method: str, path: str) -> None:
    assert_problem(anon.request(method, API + path, headers=CSRF, json=RANGE), 401)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/shopping/preview"),
        ("POST", "/shopping-lists"),
        ("DELETE", "/shopping-lists/1"),
        ("PATCH", "/shopping-lists/1/items/1"),
        ("POST", "/shopping-lists/1/complete"),
    ],
)
def test_mutations_require_csrf_header(client: TestClient, method: str, path: str) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(client.request(method, API + path, json=RANGE), 403)


# --- preview and snapshot ------------------------------------------------------------------


def test_preview_computes_without_storing(client: TestClient, kitchen: dict) -> None:
    preview = ok(client.post(f"{API}/shopping/preview", json=RANGE))

    assert (preview["start_date"], preview["end_date"], preview["today"]) == (
        START.isoformat(),
        END.isoformat(),
        TODAY.isoformat(),
    )
    by_name = {i["ingredient_name"]: i for i in preview["items"]}
    assert list(by_name) == ["Flour", "Sugar", "Eggs", "Milk"]
    flour = by_name["Flour"]
    assert (
        flour["required_base"],
        flour["stock_base"],
        flour["reserved_base"],
        flour["available_base"],
        flour["to_buy_base"],
    ) == ("700.000", "300.000", "100.000", "200.000", "500.000")
    assert [c["meal"]["recipe_name"] for c in flour["contributions"]] == ["Pancakes", "Bread"]
    assert by_name["Sugar"]["nothing_to_buy"] is True
    assert by_name["Milk"]["unconverted"][0]["amount"] == "100.000"
    assert by_name["Milk"]["unconverted"][0]["reason"] == "missing_grams_per_ml"
    assert [c["ingredient_name"] for c in preview["check_have"]] == ["Salt"]
    assert ok(client.get(f"{API}/shopping-lists")) == []


def test_snapshot_equals_preview(client: TestClient, kitchen: dict) -> None:
    preview = ok(client.post(f"{API}/shopping/preview", json=RANGE))
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)

    assert created["status"] == "open"
    assert created["completed_at"] is None
    assert (created["start_date"], created["end_date"]) == (START.isoformat(), END.isoformat())
    expected = [
        {
            "ingredient_id": i["ingredient_id"],
            "ingredient_name": i["ingredient_name"],
            "category": i["category"],
            "base_unit": i["base_unit"],
            "required_base": i["required_base"],
            "available_base": i["available_base"],
            "to_buy_base": i["to_buy_base"],
            "display": i["display"],
            "unconverted": [
                {k: u[k] for k in ("amount", "unit", "reason", "recipe_names")}
                for u in i["unconverted"]
            ],
        }
        for i in preview["items"]
        if not i["nothing_to_buy"]
    ] + [
        {
            "ingredient_id": c["ingredient_id"],
            "ingredient_name": c["ingredient_name"],
            "category": c["category"],
            "base_unit": "g",
            "required_base": "0.000",
            "available_base": "0.000",
            "to_buy_base": "0.000",
            "display": {"amount": "0.000", "unit": "g"},
            "unconverted": [],
        }
        for c in preview["check_have"]
    ]
    assert [{k: item[k] for k in expected[0]} for item in created["items"]] == expected
    assert [(i["kind"], i["position"]) for i in created["items"]] == [
        ("buy", 0),
        ("buy", 1),
        ("buy", 2),
        ("check_have", 3),
    ]
    by_name = items_by_name(created)
    assert by_name["Milk"]["recipe_names"] == ["Pancakes", "Bread"]
    assert by_name["Salt"]["recipe_names"] == ["Pancakes"]
    assert all(not i["checked"] and i["bought_base"] is None for i in created["items"])
    assert ok(client.get(f"{API}/shopping-lists/{created['id']}")) == created


def test_unconverted_only_item_is_informational(client: TestClient, kitchen: dict) -> None:
    ok(client.patch(f"{API}/stock/{kitchen['milk']}", json={"delta": "1", "unit": "l"}))

    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)

    milk = items_by_name(created)["Milk"]
    assert (milk["kind"], milk["to_buy_base"]) == ("unconverted", "0.000")
    assert milk["unconverted"] == [
        {
            "amount": "100.000",
            "unit": "g",
            "reason": "missing_grams_per_ml",
            "recipe_names": ["Bread"],
        }
    ]


def test_snapshot_does_not_follow_later_changes(client: TestClient, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    ok(client.patch(f"{API}/stock/{kitchen['flour']}", json={"delta": "5", "unit": "kg"}))

    again = ok(client.get(f"{API}/shopping-lists/{created['id']}"))

    assert items_by_name(again)["Flour"]["to_buy_base"] == "500.000"


def test_empty_range_creates_an_empty_list(client: TestClient) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)

    assert created["items"] == []
    assert (created["item_count"], created["checked_count"]) == (0, 0)


@pytest.mark.parametrize("path", ["/shopping/preview", "/shopping-lists"])
@pytest.mark.parametrize(
    "body",
    [
        {"from": "2026-10-05", "to": "2026-10-04"},
        {"from": "2026-10-01", "to": "2026-12-02"},
        {"from": "2026-10-05"},
        {"to": "2026-10-05"},
        {"from": "not-a-date", "to": "2026-10-05"},
        {"from": "2026-10-05", "to": "2026-10-05", "extra": 1},
    ],
)
def test_range_validation(client: TestClient, path: str, body: dict) -> None:
    assert_problem(client.post(API + path, json=body), 422)
    assert ok(client.get(f"{API}/shopping-lists")) == []


@pytest.mark.parametrize("path", ["/shopping/preview", "/shopping-lists"])
def test_range_limits_are_inclusive(client: TestClient, path: str) -> None:
    single = {"from": "2026-10-05", "to": "2026-10-05"}
    longest = {"from": "2026-10-01", "to": "2026-12-01"}
    for body in (single, longest):
        assert client.post(API + path, json=body).status_code in (200, 201)


# --- listing and items ---------------------------------------------------------------------


def test_lists_are_recent_first_with_counts(client: TestClient, kitchen: dict) -> None:
    first = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    second = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    flour = items_by_name(first)["Flour"]["id"]
    ok(client.patch(f"{API}/shopping-lists/{first['id']}/items/{flour}", json={"checked": True}))

    lists = ok(client.get(f"{API}/shopping-lists"))

    assert [(s["id"], s["item_count"], s["checked_count"]) for s in lists] == [
        (second["id"], 4, 0),
        (first["id"], 4, 1),
    ]
    assert "items" not in lists[0]
    assert [s["id"] for s in ok(client.get(f"{API}/shopping-lists", params={"limit": 1}))] == [
        second["id"]
    ]


def test_check_and_uncheck(client: TestClient, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    flour = items_by_name(created)["Flour"]
    url = f"{API}/shopping-lists/{created['id']}/items/{flour['id']}"

    checked = ok(client.patch(url, json={"checked": True}))
    assert checked["checked"] is True
    assert checked["bought_base"] is None
    unchecked = ok(client.patch(url, json={"checked": False}))
    assert unchecked["checked"] is False
    assert ok(client.patch(url, json={}))["checked"] is False
    assert_problem(client.patch(url, json={"checked": None}), 422)
    assert_problem(client.patch(url, json={"checked": True, "extra": 1}), 422)


@pytest.mark.parametrize(
    ("item", "body", "bought_base", "bought_display"),
    [
        ("Flour", {"bought_quantity": "0.75", "unit": "kg"}, "750.000", ["750.000", "g"]),
        ("Flour", {"bought_quantity": "1000"}, "1000.000", ["1.000", "kg"]),
        ("Eggs", {"bought_quantity": "300", "unit": "g"}, "6.000", ["6.000", "piece"]),
        ("Milk", {"bought_quantity": "2", "unit": "cup"}, "480.000", ["480.000", "ml"]),
        ("Salt", {"bought_quantity": "0"}, "0.000", ["0.000", "g"]),
    ],
)
def test_bought_quantity_converts_to_base(
    client: TestClient,
    kitchen: dict,
    item: str,
    body: dict,
    bought_base: str,
    bought_display: list[str],
) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    item_id = items_by_name(created)[item]["id"]

    updated = ok(client.patch(f"{API}/shopping-lists/{created['id']}/items/{item_id}", json=body))

    assert updated["bought_base"] == bought_base
    assert [updated["bought_display"]["amount"], updated["bought_display"]["unit"]] == (
        bought_display
    )


def test_bought_quantity_errors_and_clearing(client: TestClient, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    items = items_by_name(created)
    milk = f"{API}/shopping-lists/{created['id']}/items/{items['Milk']['id']}"
    flour = f"{API}/shopping-lists/{created['id']}/items/{items['Flour']['id']}"

    problem = assert_problem(client.patch(milk, json={"bought_quantity": "100", "unit": "g"}), 422)
    assert problem["reason"] == "missing_grams_per_ml"
    assert "known_units" in assert_problem(
        client.patch(flour, json={"bought_quantity": "1", "unit": "sack"}), 422
    )
    assert_problem(client.patch(flour, json={"bought_quantity": "1", "unit": "pinch"}), 422)
    assert_problem(client.patch(flour, json={"bought_quantity": "-1"}), 422)
    assert_problem(client.patch(flour, json={"bought_quantity": "1e12", "unit": "kg"}), 422)
    assert_problem(client.patch(flour, json={"unit": "kg"}), 422)

    ok(client.patch(flour, json={"bought_quantity": "1", "unit": "kg"}))
    cleared = ok(client.patch(flour, json={"bought_quantity": None}))
    assert (cleared["bought_base"], cleared["bought_display"]) == (None, None)


def test_unknown_list_or_item_is_404(client: TestClient, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    other = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    foreign_item = other["items"][0]["id"]

    assert_problem(client.get(f"{API}/shopping-lists/999999"), 404)
    assert_problem(client.delete(f"{API}/shopping-lists/999999"), 404)
    assert_problem(client.post(f"{API}/shopping-lists/999999/complete"), 404)
    assert_problem(
        client.patch(
            f"{API}/shopping-lists/{created['id']}/items/{foreign_item}", json={"checked": True}
        ),
        404,
    )


# --- complete ------------------------------------------------------------------------------


def test_complete_writes_purchases_once(client: TestClient, db: Session, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    list_url = f"{API}/shopping-lists/{created['id']}"
    items = items_by_name(created)
    patches = {
        "Flour": {"checked": True, "bought_quantity": "1", "unit": "kg"},
        "Eggs": {"checked": True},
        "Milk": {"bought_quantity": "1", "unit": "l"},
        "Salt": {"checked": True},
    }
    for name, body in patches.items():
        ok(client.patch(f"{list_url}/items/{items[name]['id']}", json=body))

    done = ok(client.post(f"{list_url}/complete"))

    assert done["status"] == "done"
    assert done["completed_at"] is not None
    assert purchases(db) == [
        (kitchen["flour"], "1000.000", created["id"]),
        (kitchen["eggs"], "2.000", created["id"]),
    ]
    assert stock_of(client, kitchen["flour"]) == "1300.000"
    assert stock_of(client, kitchen["eggs"]) == "2.000"
    assert stock_of(client, kitchen["milk"]) == "0"
    movement = db.scalars(
        select(StockMovement).where(StockMovement.ingredient_id == kitchen["eggs"])
    ).one()
    assert (movement.source, movement.user_id) == (StockSource.UI, created["created_by"])

    again = ok(client.post(f"{list_url}/complete"))
    assert again == done
    assert len(purchases(db)) == 2
    assert stock_of(client, kitchen["flour"]) == "1300.000"


def test_completed_list_rejects_edits(client: TestClient, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    list_url = f"{API}/shopping-lists/{created['id']}"
    ok(client.post(f"{list_url}/complete"))
    item_url = f"{list_url}/items/{created['items'][0]['id']}"

    assert_problem(client.patch(item_url, json={"checked": True}), 409)
    assert_problem(client.patch(item_url, json={"bought_quantity": "1"}), 409)
    assert ok(client.get(list_url))["checked_count"] == 0


def test_complete_with_nothing_checked_writes_nothing(
    client: TestClient, db: Session, kitchen: dict
) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)

    assert ok(client.post(f"{API}/shopping-lists/{created['id']}/complete"))["status"] == "done"
    assert purchases(db) == []


def test_complete_rolls_back_when_stock_would_overflow(
    client: TestClient, db: Session, kitchen: dict
) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    list_url = f"{API}/shopping-lists/{created['id']}"
    items = items_by_name(created)
    ok(client.patch(f"{list_url}/items/{items['Eggs']['id']}", json={"checked": True}))
    body = {"checked": True, "bought_quantity": "999999999", "unit": "g"}
    ok(client.patch(f"{list_url}/items/{items['Flour']['id']}", json=body))

    assert_problem(client.post(f"{list_url}/complete"), 422)
    assert ok(client.get(list_url))["status"] == "open"
    assert purchases(db) == []


# --- delete --------------------------------------------------------------------------------


def test_delete_open_list_removes_items(client: TestClient, db: Session, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)

    assert client.delete(f"{API}/shopping-lists/{created['id']}").status_code == 204
    assert_problem(client.get(f"{API}/shopping-lists/{created['id']}"), 404)
    count = db.scalar(
        select(func.count())
        .select_from(ShoppingListItem)
        .where(ShoppingListItem.list_id == created["id"])
    )
    assert count == 0


def test_delete_done_list_keeps_movements(client: TestClient, db: Session, kitchen: dict) -> None:
    created = ok(client.post(f"{API}/shopping-lists", json=RANGE), 201)
    list_url = f"{API}/shopping-lists/{created['id']}"
    flour = items_by_name(created)["Flour"]["id"]
    ok(client.patch(f"{list_url}/items/{flour}", json={"checked": True}))
    ok(client.post(f"{list_url}/complete"))
    assert purchases(db) == [(kitchen["flour"], "500.000", created["id"])]

    assert client.delete(list_url).status_code == 204

    assert purchases(db) == [(kitchen["flour"], "500.000", None)]
    assert stock_of(client, kitchen["flour"]) == "800.000"
    assert_problem(client.post(f"{list_url}/complete"), 404)
