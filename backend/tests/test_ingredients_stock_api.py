from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import Ingredient, StockItem, StockMovement, User
from jyj.services import auth as auth_service

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1"


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


def create(client: TestClient, **fields) -> dict:
    body = {"name": "Flour", "dimension": "mass", **fields}
    response = client.post(f"{API}/ingredients", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def patch_stock(client: TestClient, ingredient_id: int, **body):
    return client.patch(f"{API}/stock/{ingredient_id}", json=body)


def ledger_sum(db: Session, ingredient_id: int) -> Decimal:
    db.expire_all()
    return db.scalar(
        select(func.coalesce(func.sum(StockMovement.delta_base), 0)).where(
            StockMovement.ingredient_id == ingredient_id
        )
    )


def balance(db: Session, ingredient_id: int) -> Decimal:
    db.expire_all()
    return db.scalar(
        select(StockItem.quantity_base).where(StockItem.ingredient_id == ingredient_id)
    )


# --- auth and CSRF -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/units"),
        ("GET", "/ingredients"),
        ("GET", "/ingredients/1"),
        ("GET", "/stock"),
        ("GET", "/stock/movements"),
        ("POST", "/ingredients"),
        ("PATCH", "/ingredients/1"),
        ("DELETE", "/ingredients/1"),
        ("PATCH", "/stock/1"),
    ],
)
def test_endpoints_require_a_session(anon: TestClient, method: str, path: str) -> None:
    assert_problem(anon.request(method, API + path, headers=CSRF, json={}), 401)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ingredients"),
        ("PATCH", "/ingredients/1"),
        ("DELETE", "/ingredients/1"),
        ("PATCH", "/stock/1"),
    ],
)
def test_mutations_require_csrf_header(client: TestClient, method: str, path: str) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(client.request(method, API + path, json={}), 403)


# --- units ---------------------------------------------------------------------------------


def test_units_lists_seeded_units(client: TestClient) -> None:
    response = client.get(f"{API}/units")

    assert response.status_code == 200
    units = {u["code"]: u for u in response.json()}
    assert units["kg"] == {"code": "kg", "dimension": "mass", "to_base": "1000.000"}
    assert units["pinch"] == {"code": "pinch", "dimension": "none", "to_base": None}


# --- ingredients CRUD ----------------------------------------------------------------------


def test_create_get_list_update_delete(client: TestClient) -> None:
    created = create(client, name="  Plain   flour ", category="Baking")
    assert created["name"] == "Plain flour"
    assert created["default_unit"] == "g"
    assert created["category"] == "Baking"
    assert created["stock"] == {
        "quantity_base": "0.000",
        "base_unit": "g",
        "display": {"amount": "0.000", "unit": "g"},
    }

    iid = created["id"]
    assert client.get(f"{API}/ingredients/{iid}").json()["name"] == "Plain flour"

    updated = client.patch(
        f"{API}/ingredients/{iid}", json={"default_unit": "kg", "category": None}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["default_unit"] == "kg"
    assert updated.json()["category"] is None

    assert client.delete(f"{API}/ingredients/{iid}").status_code == 204
    assert_problem(client.get(f"{API}/ingredients/{iid}"), 404)


def test_list_search_is_case_insensitive_and_includes_stock(client: TestClient) -> None:
    flour = create(client, name="Flour")
    create(client, name="Sugar")
    create(client, name="Rice flour")
    create(client, name="100% cocoa")
    patch_stock(client, flour["id"], delta="1.5", unit="kg")

    names = [i["name"] for i in client.get(f"{API}/ingredients", params={"q": "FLOUR"}).json()]
    assert names == ["Flour", "Rice flour"]

    [row] = client.get(f"{API}/ingredients", params={"q": "flour"}).json()[:1]
    assert row["stock"]["quantity_base"] == "1500.000"
    assert row["stock"]["display"] == {"amount": "1.500", "unit": "kg"}

    wildcard = client.get(f"{API}/ingredients", params={"q": "%"}).json()
    assert [i["name"] for i in wildcard] == ["100% cocoa"]
    assert len(client.get(f"{API}/ingredients").json()) == 4


def test_duplicate_name_is_409_case_insensitive(client: TestClient) -> None:
    create(client, name="Flour")
    body = assert_problem(
        client.post(f"{API}/ingredients", json={"name": "FLOUR", "dimension": "mass"}), 409
    )
    assert "existing_id" in body

    sugar = create(client, name="Sugar")
    assert_problem(client.patch(f"{API}/ingredients/{sugar['id']}", json={"name": "flour"}), 409)
    renamed = client.patch(f"{API}/ingredients/{sugar['id']}", json={"name": "SUGAR"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "SUGAR"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "dimension": "mass"},
        {"name": "   ", "dimension": "mass"},
        {"name": "x" * 101, "dimension": "mass"},
        {"name": "Salt", "dimension": "none"},
        {"name": "Salt", "dimension": "weight"},
        {"name": "Salt", "dimension": "mass", "default_unit": "ml"},
        {"name": "Salt", "dimension": "mass", "default_unit": "furlong"},
        {"name": "Salt", "dimension": "mass", "grams_per_ml": "0"},
        {"name": "Salt", "dimension": "mass", "grams_per_ml": "-1"},
        {"name": "Salt", "dimension": "mass", "grams_per_piece": "1.2345"},
        {"name": "Salt", "dimension": "mass", "surprise": 1},
        {"dimension": "mass"},
    ],
)
def test_create_validation(client: TestClient, body: dict) -> None:
    assert_problem(client.post(f"{API}/ingredients", json=body), 422)


def test_patch_rejects_null_required_fields(client: TestClient) -> None:
    flour = create(client)
    assert_problem(client.patch(f"{API}/ingredients/{flour['id']}", json={"name": None}), 422)


def test_patch_dimension_resets_default_unit_when_unused(client: TestClient) -> None:
    egg = create(client, name="Egg", default_unit="kg")
    response = client.patch(f"{API}/ingredients/{egg['id']}", json={"dimension": "count"})
    assert response.status_code == 200, response.text
    assert response.json()["default_unit"] == "piece"


def test_dimension_change_is_409_once_stock_history_exists(client: TestClient) -> None:
    flour = create(client)
    patch_stock(client, flour["id"], delta="100", unit="g")
    body = assert_problem(
        client.patch(f"{API}/ingredients/{flour['id']}", json={"dimension": "volume"}), 409
    )
    assert body["references"] == ["stock_movements"]


def test_delete_referenced_ingredient_is_409(client: TestClient) -> None:
    flour = create(client)
    patch_stock(client, flour["id"], delta="100", unit="g")

    body = assert_problem(client.delete(f"{API}/ingredients/{flour['id']}"), 409)
    assert body["references"] == ["stock_movements"]
    assert client.get(f"{API}/ingredients/{flour['id']}").status_code == 200


def test_missing_ingredient_is_404(client: TestClient) -> None:
    assert_problem(client.get(f"{API}/ingredients/999999"), 404)
    assert_problem(client.patch(f"{API}/ingredients/999999", json={"name": "x"}), 404)
    assert_problem(client.delete(f"{API}/ingredients/999999"), 404)
    assert_problem(patch_stock(client, 999999, delta="1", unit="g"), 404)


# --- stock ---------------------------------------------------------------------------------


def test_delta_and_set_to_write_movements(client: TestClient, db: Session, user: User) -> None:
    flour = create(client)
    iid = flour["id"]

    first = patch_stock(client, iid, delta="2", unit="kg")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["stock"]["quantity_base"] == "2000.000"
    assert body["stock"]["display"] == {"amount": "2.000", "unit": "kg"}
    assert body["movement"]["delta_base"] == "2000.000"
    assert body["movement"]["shortfall_base"] == "0.000"
    assert body["movement"]["reason"] == "manual"
    assert body["movement"]["source"] == "ui"
    assert body["movement"]["user_id"] == user.id

    purchased = patch_stock(client, iid, delta="250", unit="g", reason="purchased").json()
    assert purchased["movement"]["reason"] == "purchased"

    corrected = patch_stock(client, iid, set_to="500", unit="g").json()
    assert corrected["stock"]["quantity_base"] == "500.000"
    assert corrected["movement"]["delta_base"] == "-1750.000"
    assert corrected["movement"]["reason"] == "correction"

    unchanged = patch_stock(client, iid, set_to="0.5", unit="kg").json()
    assert unchanged["movement"] is None

    assert balance(db, iid) == ledger_sum(db, iid) == Decimal("500.000")


def test_floor_at_zero_records_shortfall(client: TestClient, db: Session) -> None:
    egg = create(client, name="Egg", dimension="count")
    iid = egg["id"]
    patch_stock(client, iid, delta="3", unit="piece")

    body = patch_stock(client, iid, delta="-5", unit="piece").json()
    assert body["stock"]["quantity_base"] == "0.000"
    assert body["movement"]["delta_base"] == "-3.000"
    assert body["movement"]["shortfall_base"] == "2.000"

    again = patch_stock(client, iid, delta="-1", unit="piece").json()
    assert again["movement"]["delta_base"] == "0.000"
    assert again["movement"]["shortfall_base"] == "1.000"

    assert balance(db, iid) == ledger_sum(db, iid) == 0


def test_ledger_sum_equals_balance_over_mixed_units(client: TestClient, db: Session) -> None:
    milk = create(client, name="Milk", dimension="volume", default_unit="l")
    iid = milk["id"]
    for body in (
        {"delta": "1", "unit": "l"},
        {"delta": "2", "unit": "cup"},
        {"delta": "-3", "unit": "tbsp"},
        {"delta": "1.5", "unit": "tsp"},
        {"delta": "-10", "unit": "l"},
        {"set_to": "750", "unit": "ml"},
        {"delta": "-0.25", "unit": "l"},
    ):
        assert patch_stock(client, iid, **body).status_code == 200

    assert balance(db, iid) == ledger_sum(db, iid) == Decimal("500.000")


def test_cross_dimension_conversion_uses_ingredient_factors(client: TestClient) -> None:
    egg = create(client, name="Egg", dimension="count", grams_per_piece="50")
    body = patch_stock(client, egg["id"], delta="125", unit="g").json()
    assert body["stock"]["quantity_base"] == "2.500"

    oil = create(client, name="Oil", dimension="volume", grams_per_ml="0.92")
    body = patch_stock(client, oil["id"], delta="92", unit="g").json()
    assert body["stock"]["quantity_base"] == "100.000"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"delta": "100", "unit": "ml"}, "missing_grams_per_ml"),
        ({"delta": "2", "unit": "piece"}, "missing_grams_per_piece"),
        ({"delta": "1", "unit": "pinch"}, "dimensionless"),
        ({"set_to": "1", "unit": "to_taste"}, "dimensionless"),
    ],
)
def test_unconvertible_units_are_422(client: TestClient, body: dict, reason: str) -> None:
    flour = create(client)
    problem = assert_problem(patch_stock(client, flour["id"], **body), 422)
    assert problem["reason"] == reason
    assert problem["base_unit"] == "g"


@pytest.mark.parametrize(
    "body",
    [
        {"unit": "g"},
        {"delta": "1", "set_to": "1", "unit": "g"},
        {"set_to": "-1", "unit": "g"},
        {"delta": "0", "unit": "g"},
        {"delta": "0.0001", "unit": "g"},
        {"delta": "1", "unit": "furlong"},
        {"delta": "1"},
        {"delta": "NaN", "unit": "g"},
        {"delta": "1e12", "unit": "g"},
        {"delta": "1", "unit": "g", "reason": "cooked"},
        {"delta": "1", "unit": "g", "extra": True},
    ],
)
def test_stock_update_validation(client: TestClient, db: Session, body: dict) -> None:
    flour = create(client)
    assert_problem(patch_stock(client, flour["id"], **body), 422)
    assert ledger_sum(db, flour["id"]) == 0


def test_balance_overflow_is_422(client: TestClient) -> None:
    flour = create(client)
    assert patch_stock(client, flour["id"], set_to="999999", unit="kg").status_code == 200
    assert_problem(patch_stock(client, flour["id"], delta="1", unit="kg"), 422)


def test_stock_list_only_tracked_ingredients(client: TestClient) -> None:
    create(client, name="Sugar")
    flour = create(client, name="Flour")
    patch_stock(client, flour["id"], delta="1", unit="kg")

    [row] = client.get(f"{API}/stock").json()
    assert row["ingredient_id"] == flour["id"]
    assert row["ingredient_name"] == "Flour"
    assert row["quantity_base"] == "1000.000"
    assert row["base_unit"] == "g"


def test_movements_are_filtered_and_paginated(client: TestClient, db: Session) -> None:
    flour = create(client, name="Flour")
    sugar = create(client, name="Sugar")
    for n in range(1, 6):
        patch_stock(client, flour["id"], delta=str(n), unit="g")
    patch_stock(client, sugar["id"], delta="1", unit="g")

    page = client.get(
        f"{API}/stock/movements", params={"ingredient_id": flour["id"], "limit": 2}
    ).json()
    assert page["total"] == 5
    assert [m["delta_base"] for m in page["items"]] == ["5.000", "4.000"]

    rest = client.get(
        f"{API}/stock/movements",
        params={"ingredient_id": flour["id"], "limit": 2, "offset": 4},
    ).json()
    assert [m["delta_base"] for m in rest["items"]] == ["1.000"]

    assert client.get(f"{API}/stock/movements").json()["total"] == 6

    db.expire_all()
    created = db.scalar(select(func.min(StockMovement.created_at)))
    after = client.get(
        f"{API}/stock/movements",
        params={"from": (created.replace(year=created.year + 1)).isoformat()},
    ).json()
    assert after["total"] == 0
    before = client.get(f"{API}/stock/movements", params={"to": created.isoformat()}).json()
    assert before["total"] == 0
    window = client.get(
        f"{API}/stock/movements",
        params={
            "from": created.isoformat(),
            "to": created.replace(year=created.year + 1).isoformat(),
        },
    ).json()
    assert window["total"] == 6


def test_movements_rejects_bad_pagination(client: TestClient) -> None:
    assert_problem(client.get(f"{API}/stock/movements", params={"limit": 0}), 422)
    assert_problem(client.get(f"{API}/stock/movements", params={"offset": -1}), 422)


def test_deleting_unreferenced_ingredient_with_stock_row_cascades(
    client: TestClient, db: Session
) -> None:
    flour = create(client)
    db.add(StockItem(ingredient_id=flour["id"], quantity_base=Decimal(0)))
    db.commit()

    assert client.delete(f"{API}/ingredients/{flour['id']}").status_code == 204
    db.expire_all()
    assert db.get(Ingredient, flour["id"]) is None
    assert db.get(StockItem, flour["id"]) is None
