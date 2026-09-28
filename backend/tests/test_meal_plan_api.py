import datetime as dt
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select, update
from sqlalchemy.exc import IntegrityError
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
    StockMovement,
    StockReason,
    StockSource,
    User,
)
from jyj.services import auth as auth_service
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


@pytest.fixture
def slots(client: TestClient) -> dict[str, dict]:
    return {s["name"].lower(): s for s in client.get(f"{API}/meal-slots").json()}


def create_recipe(client: TestClient, name: str = "Pancakes", **fields) -> dict:
    response = client.post(f"{API}/recipes", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def plan(client: TestClient, day: dt.date, slot_id: int | None, recipe_id: int, **fields) -> dict:
    body = {"date": day.isoformat(), "recipe_id": recipe_id, **fields}
    if slot_id is not None:
        body["slot_id"] = slot_id
    response = client.post(f"{API}/planned-meals", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def week(client: TestClient, start: dt.date, end: dt.date) -> list[dict]:
    response = client.get(
        f"{API}/planned-meals", params={"from": start.isoformat(), "to": end.isoformat()}
    )
    assert response.status_code == 200, response.text
    return response.json()


def day_ids(client: TestClient, day: dt.date) -> list[int]:
    meals = week(client, day, day)
    assert [m["position"] for m in meals] == list(range(len(meals)))
    return [m["id"] for m in meals]


def mark_cooked(db: Session, meal_id: int, user: User) -> None:
    db.execute(
        update(PlannedMeal)
        .where(PlannedMeal.id == meal_id)
        .values(
            status=PlannedMealStatus.COOKED, cooked_at=dt.datetime.now(dt.UTC), cooked_by=user.id
        )
    )
    db.commit()


# --- auth and CSRF -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/meal-slots"),
        ("POST", "/meal-slots"),
        ("PATCH", "/meal-slots/1"),
        ("DELETE", "/meal-slots/1"),
        ("PUT", "/meal-slots/order"),
        ("GET", "/planned-meals?from=2026-10-05&to=2026-10-11"),
        ("POST", "/planned-meals"),
        ("GET", "/planned-meals/1"),
        ("PATCH", "/planned-meals/1"),
        ("DELETE", "/planned-meals/1"),
    ],
)
def test_endpoints_require_a_session(anon: TestClient, method: str, path: str) -> None:
    assert_problem(anon.request(method, API + path, headers=CSRF, json={}), 401)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/meal-slots"),
        ("PATCH", "/meal-slots/1"),
        ("DELETE", "/meal-slots/1"),
        ("PUT", "/meal-slots/order"),
        ("POST", "/planned-meals"),
        ("PATCH", "/planned-meals/1"),
        ("DELETE", "/planned-meals/1"),
    ],
)
def test_mutations_require_csrf_header(client: TestClient, method: str, path: str) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(client.request(method, API + path, json={}), 403)


# --- meal slots ----------------------------------------------------------------------------


def test_slots_are_seeded_in_order(client: TestClient) -> None:
    slots = client.get(f"{API}/meal-slots").json()

    assert [(s["name"], s["position"], s["active"]) for s in slots] == [
        ("Lunch", 0, True),
        ("Dinner", 1, True),
        ("Tea", 2, True),
    ]


def test_create_slot_appends_and_normalises_name(client: TestClient) -> None:
    response = client.post(f"{API}/meal-slots", json={"name": "  Late   snack "})

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Late snack"
    assert response.json()["position"] == 3
    assert [s["name"] for s in client.get(f"{API}/meal-slots").json()][-1] == "Late snack"


def test_slot_names_are_unique_case_insensitively(client: TestClient, slots: dict) -> None:
    assert_problem(client.post(f"{API}/meal-slots", json={"name": "LUNCH"}), 409)
    assert_problem(
        client.patch(f"{API}/meal-slots/{slots['tea']['id']}", json={"name": "dinner"}), 409
    )
    renamed = client.patch(f"{API}/meal-slots/{slots['tea']['id']}", json={"name": "TEA"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "TEA"


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x" * 51}, {"name": "ok", "extra": 1}])
def test_create_slot_validation(client: TestClient, body: dict) -> None:
    assert_problem(client.post(f"{API}/meal-slots", json=body), 422)


def test_blank_slot_name_after_normalising_is_422(client: TestClient) -> None:
    assert_problem(client.post(f"{API}/meal-slots", json={"name": "   "}), 422)


def test_patch_slot_rejects_null(client: TestClient, slots: dict) -> None:
    for body in ({"name": None}, {"active": None}):
        assert_problem(client.patch(f"{API}/meal-slots/{slots['lunch']['id']}", json=body), 422)


def test_deactivate_and_reactivate_slot(client: TestClient, slots: dict) -> None:
    slot_id = slots["tea"]["id"]
    assert (
        client.patch(f"{API}/meal-slots/{slot_id}", json={"active": False}).json()["active"]
        is False
    )
    assert (
        client.patch(f"{API}/meal-slots/{slot_id}", json={"active": True}).json()["active"] is True
    )


def test_reorder_slots(client: TestClient, slots: dict) -> None:
    ids = [slots["tea"]["id"], slots["lunch"]["id"], slots["dinner"]["id"]]

    response = client.put(f"{API}/meal-slots/order", json={"ids": ids})

    assert response.status_code == 200, response.text
    assert [(s["id"], s["position"]) for s in response.json()] == list(
        zip(ids, range(3), strict=True)
    )
    assert [s["id"] for s in client.get(f"{API}/meal-slots").json()] == ids


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown"])
def test_reorder_must_list_every_slot_once(client: TestClient, slots: dict, kind: str) -> None:
    ids = [s["id"] for s in slots.values()]
    bad = {"missing": ids[:-1], "duplicate": [*ids, ids[0]], "unknown": [*ids, 999_999]}[kind]

    assert_problem(client.put(f"{API}/meal-slots/order", json={"ids": bad}), 422)
    assert [s["id"] for s in client.get(f"{API}/meal-slots").json()] == ids


def test_delete_unused_slot_compacts_positions(client: TestClient, slots: dict) -> None:
    assert client.delete(f"{API}/meal-slots/{slots['lunch']['id']}").status_code == 204

    remaining = client.get(f"{API}/meal-slots").json()
    assert [(s["name"], s["position"]) for s in remaining] == [("Dinner", 0), ("Tea", 1)]
    assert_problem(client.delete(f"{API}/meal-slots/{slots['lunch']['id']}"), 404)


def test_delete_referenced_slot_unlabels_its_meals(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    lunch = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    dinner = plan(client, MONDAY, slots["dinner"]["id"], recipe["id"])

    assert client.delete(f"{API}/meal-slots/{slots['dinner']['id']}").status_code == 204

    assert [s["name"] for s in client.get(f"{API}/meal-slots").json()] == ["Lunch", "Tea"]
    meals = week(client, MONDAY, MONDAY)
    assert [(m["id"], m["position"], m["slot_id"]) for m in meals] == [
        (lunch["id"], 0, slots["lunch"]["id"]),
        (dinner["id"], 1, None),
    ]
    assert meals[1]["slot"] is None


# --- planned meals: create -----------------------------------------------------------------


def test_create_defaults_servings_from_recipe_and_renders_recipe_and_slot(
    client: TestClient, slots: dict, user: User
) -> None:
    recipe = create_recipe(client, default_servings=3)

    meal = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])

    assert meal["servings"] == 3
    assert meal["status"] == "planned"
    assert meal["position"] == 0
    assert meal["date"] == MONDAY.isoformat()
    assert meal["cooked_at"] is None and meal["cooked_by"] is None
    assert meal["created_by"] == user.id
    assert meal["recipe"] == {
        "id": recipe["id"],
        "name": "Pancakes",
        "photo_url": None,
        "photo_thumb_url": None,
        "default_servings": 3,
        "archived_at": None,
    }
    assert meal["slot"]["name"] == "Lunch"
    assert client.get(f"{API}/planned-meals/{meal['id']}").json() == meal


def test_planned_recipe_exposes_photo_urls_not_the_stored_path(
    client: TestClient, db: Session, slots: dict
) -> None:
    recipe = create_recipe(client)
    db.execute(update(Recipe).where(Recipe.id == recipe["id"]).values(photo_path="abc123.webp"))
    db.commit()

    meal = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    listed = week(client, MONDAY, MONDAY)

    assert meal["recipe"]["photo_url"] == "/media/abc123.webp"
    assert meal["recipe"]["photo_thumb_url"] == "/media/abc123_thumb.webp"
    assert "photo_path" not in meal["recipe"]
    assert listed[0]["recipe"] == meal["recipe"]


def test_create_appends_to_the_day_whatever_the_slot(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    first = plan(client, MONDAY, slots["dinner"]["id"], recipe["id"], servings=4)
    second = plan(client, MONDAY, None, recipe["id"])
    third = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    other_day = plan(client, MONDAY + dt.timedelta(days=1), slots["lunch"]["id"], recipe["id"])

    assert [m["position"] for m in (first, second, third, other_day)] == [0, 1, 2, 0]
    assert first["servings"] == 4
    assert day_ids(client, MONDAY) == [first["id"], second["id"], third["id"]]


def test_create_without_slot(client: TestClient) -> None:
    recipe = create_recipe(client)

    omitted = plan(client, MONDAY, None, recipe["id"])
    response = client.post(
        f"{API}/planned-meals",
        json={"date": MONDAY.isoformat(), "recipe_id": recipe["id"], "slot_id": None},
    )
    assert response.status_code == 201, response.text
    explicit = response.json()

    for meal in (omitted, explicit):
        assert (meal["slot_id"], meal["slot"]) == (None, None)
    assert client.get(f"{API}/planned-meals/{omitted['id']}").json() == omitted
    assert week(client, MONDAY, MONDAY) == [omitted, explicit]


def test_create_at_a_position_inserts_into_the_day(client: TestClient) -> None:
    recipe = create_recipe(client)
    a, b = (plan(client, MONDAY, None, recipe["id"])["id"] for _ in range(2))

    first = plan(client, MONDAY, None, recipe["id"], position=0)
    middle = plan(client, MONDAY, None, recipe["id"], position=2)
    last = plan(client, MONDAY, None, recipe["id"], position=99)

    assert (first["position"], middle["position"], last["position"]) == (0, 2, 4)
    assert day_ids(client, MONDAY) == [first["id"], a, middle["id"], b, last["id"]]


@pytest.mark.parametrize("servings", [0, -1, 101, "two", 1.5])
def test_create_rejects_bad_servings(client: TestClient, slots: dict, servings) -> None:
    recipe = create_recipe(client)
    body = {"date": "2026-10-05", "slot_id": slots["lunch"]["id"], "recipe_id": recipe["id"]}

    assert_problem(client.post(f"{API}/planned-meals", json={**body, "servings": servings}), 422)


@pytest.mark.parametrize(
    "patch",
    [
        {"date": "2026-02-30"},
        {"date": None},
        {"recipe_id": None},
        {"position": -1},
        {"slot_id": "lunch"},
        {"extra": 1},
    ],
)
def test_create_validation(client: TestClient, slots: dict, patch: dict) -> None:
    recipe = create_recipe(client)
    body = {"date": "2026-10-05", "slot_id": slots["lunch"]["id"], "recipe_id": recipe["id"]}

    assert_problem(client.post(f"{API}/planned-meals", json={**body, **patch}), 422)


def test_create_with_unknown_recipe_or_slot_is_404(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    body = {"date": "2026-10-05", "slot_id": slots["lunch"]["id"], "recipe_id": recipe["id"]}

    assert_problem(client.post(f"{API}/planned-meals", json={**body, "recipe_id": 999_999}), 404)
    assert_problem(client.post(f"{API}/planned-meals", json={**body, "slot_id": 999_999}), 404)


def test_create_in_inactive_slot_is_422(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    client.patch(f"{API}/meal-slots/{slots['tea']['id']}", json={"active": False})
    body = {"date": "2026-10-05", "slot_id": slots["tea"]["id"], "recipe_id": recipe["id"]}

    assert (
        "inactive" in assert_problem(client.post(f"{API}/planned-meals", json=body), 422)["detail"]
    )


def test_create_with_archived_recipe_is_422(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    client.patch(f"{API}/recipes/{recipe['id']}", json={"archived": True})
    body = {"date": "2026-10-05", "slot_id": slots["lunch"]["id"], "recipe_id": recipe["id"]}

    assert (
        "archived" in assert_problem(client.post(f"{API}/planned-meals", json=body), 422)["detail"]
    )


def test_database_allows_meals_without_slot(db: Session, user: User) -> None:
    recipe = Recipe(name="Soup", created_by=user.id)
    db.add(recipe)
    db.flush()
    meal = PlannedMeal(date=MONDAY, recipe_id=recipe.id, servings=2, created_by=user.id)
    db.add(meal)
    db.flush()
    db.refresh(meal)

    assert (meal.slot_id, meal.slot, meal.position) == (None, None, 0)


def test_database_rejects_non_positive_servings(db: Session, user: User, slots: dict) -> None:
    recipe = Recipe(name="Soup", created_by=user.id)
    db.add(recipe)
    db.flush()
    db.add(
        PlannedMeal(
            date=MONDAY,
            slot_id=slots["lunch"]["id"],
            recipe_id=recipe.id,
            servings=0,
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError, match="ck_planned_meals_servings_positive"):
        db.flush()


# --- planned meals: range query ------------------------------------------------------------


def test_range_is_inclusive_and_ordered_by_date_then_position(
    client: TestClient, slots: dict
) -> None:
    recipe = create_recipe(client)
    before = plan(client, MONDAY - dt.timedelta(days=1), slots["lunch"]["id"], recipe["id"])
    sunday = MONDAY + dt.timedelta(days=6)
    last_dinner = plan(client, sunday, slots["dinner"]["id"], recipe["id"])
    last_lunch = plan(client, sunday, slots["lunch"]["id"], recipe["id"])
    first_b = plan(client, MONDAY, slots["tea"]["id"], recipe["id"])
    first_a = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    first_a2 = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    after = plan(client, sunday + dt.timedelta(days=1), slots["lunch"]["id"], recipe["id"])

    ids = [m["id"] for m in week(client, MONDAY, sunday)]

    assert ids == [
        first_b["id"],
        first_a["id"],
        first_a2["id"],
        last_dinner["id"],
        last_lunch["id"],
    ]
    assert before["id"] not in ids and after["id"] not in ids
    assert day_ids(client, sunday) == [last_dinner["id"], last_lunch["id"]]


def test_range_ignores_slot_order(client: TestClient, slots: dict) -> None:
    recipe = create_recipe(client)
    lunch = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])
    tea = plan(client, MONDAY, slots["tea"]["id"], recipe["id"])
    client.put(
        f"{API}/meal-slots/order",
        json={"ids": [slots["tea"]["id"], slots["dinner"]["id"], slots["lunch"]["id"]]},
    )

    assert day_ids(client, MONDAY) == [lunch["id"], tea["id"]]


def test_range_limits(client: TestClient) -> None:
    assert week(client, MONDAY, MONDAY + dt.timedelta(days=61)) == []
    too_long = {"from": MONDAY.isoformat(), "to": (MONDAY + dt.timedelta(days=62)).isoformat()}
    assert_problem(client.get(f"{API}/planned-meals", params=too_long), 422)
    backwards = {"from": MONDAY.isoformat(), "to": (MONDAY - dt.timedelta(days=1)).isoformat()}
    assert_problem(client.get(f"{API}/planned-meals", params=backwards), 422)


@pytest.mark.parametrize(
    "params", [{}, {"from": "2026-10-05"}, {"to": "2026-10-05"}, {"from": "x", "to": "2026-10-05"}]
)
def test_range_requires_valid_dates(client: TestClient, params: dict) -> None:
    assert_problem(client.get(f"{API}/planned-meals", params=params), 422)


# --- planned meals: move and reorder -------------------------------------------------------


@pytest.fixture
def lunch_cell(client: TestClient, slots: dict) -> list[int]:
    recipe = create_recipe(client)
    return [plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])["id"] for _ in range(3)]


def test_reorder_within_a_day(client: TestClient, slots: dict, lunch_cell: list[int]) -> None:
    a, b, c = lunch_cell
    recipe = create_recipe(client, "Soup")
    d = plan(client, MONDAY, None, recipe["id"])["id"]

    moved = client.patch(f"{API}/planned-meals/{d}", json={"position": 0})

    assert moved.status_code == 200, moved.text
    assert (moved.json()["position"], moved.json()["slot_id"]) == (0, None)
    assert day_ids(client, MONDAY) == [d, a, b, c]

    client.patch(f"{API}/planned-meals/{d}", json={"position": 2})
    assert day_ids(client, MONDAY) == [a, b, d, c]

    same_day = client.patch(
        f"{API}/planned-meals/{a}", json={"date": MONDAY.isoformat(), "position": 99}
    )
    assert same_day.json()["position"] == 3
    assert day_ids(client, MONDAY) == [b, d, c, a]


def test_move_across_days_compacts_both(
    client: TestClient, slots: dict, lunch_cell: list[int]
) -> None:
    a, b, c = lunch_cell
    recipe = create_recipe(client, "Soup")
    tuesday = MONDAY + dt.timedelta(days=1)
    x = plan(client, tuesday, slots["dinner"]["id"], recipe["id"])["id"]
    y = plan(client, tuesday, None, recipe["id"])["id"]

    moved = client.patch(
        f"{API}/planned-meals/{a}", json={"date": tuesday.isoformat(), "position": 1}
    ).json()

    assert (moved["date"], moved["slot_id"], moved["position"]) == (
        tuesday.isoformat(),
        slots["lunch"]["id"],
        1,
    )
    assert moved["slot"]["name"] == "Lunch"
    assert day_ids(client, MONDAY) == [b, c]
    assert day_ids(client, tuesday) == [x, a, y]


def test_move_across_days_without_position_appends(
    client: TestClient, slots: dict, lunch_cell: list[int]
) -> None:
    a, b, c = lunch_cell
    recipe = create_recipe(client, "Soup")
    tuesday = MONDAY + dt.timedelta(days=1)
    x = plan(client, tuesday, None, recipe["id"])["id"]

    moved = client.patch(f"{API}/planned-meals/{b}", json={"date": tuesday.isoformat()}).json()

    assert moved["position"] == 1
    assert day_ids(client, tuesday) == [x, b]
    assert day_ids(client, MONDAY) == [a, c]


def test_set_and_clear_slot_label_keeps_order(
    client: TestClient, slots: dict, lunch_cell: list[int]
) -> None:
    a, b, c = lunch_cell

    labelled = client.patch(f"{API}/planned-meals/{a}", json={"slot_id": slots["dinner"]["id"]})
    assert labelled.status_code == 200, labelled.text
    assert (labelled.json()["slot"]["name"], labelled.json()["position"]) == ("Dinner", 0)

    cleared = client.patch(f"{API}/planned-meals/{b}", json={"slot_id": None}).json()
    assert (cleared["slot_id"], cleared["slot"], cleared["position"]) == (None, None, 1)

    untouched = client.patch(f"{API}/planned-meals/{c}", json={"servings": 3}).json()
    assert untouched["slot_id"] == slots["lunch"]["id"]
    kept = client.patch(f"{API}/planned-meals/{b}", json={"servings": 3}).json()
    assert kept["slot_id"] is None

    relabelled = client.patch(
        f"{API}/planned-meals/{b}", json={"slot_id": slots["tea"]["id"], "position": 0}
    ).json()
    assert (relabelled["slot"]["name"], relabelled["position"]) == ("Tea", 0)
    assert day_ids(client, MONDAY) == [b, a, c]


def test_move_date_only_keeps_slot(client: TestClient, slots: dict, lunch_cell: list[int]) -> None:
    a = lunch_cell[0]
    friday = MONDAY + dt.timedelta(days=4)

    moved = client.patch(f"{API}/planned-meals/{a}", json={"date": friday.isoformat()}).json()

    assert (moved["date"], moved["slot_id"], moved["position"]) == (
        friday.isoformat(),
        slots["lunch"]["id"],
        0,
    )


def test_move_into_inactive_slot_is_422_but_meals_already_there_can_be_edited(
    client: TestClient, slots: dict, lunch_cell: list[int]
) -> None:
    a, b, _ = lunch_cell
    client.patch(f"{API}/planned-meals/{b}", json={"slot_id": slots["tea"]["id"]})
    client.patch(f"{API}/meal-slots/{slots['tea']['id']}", json={"active": False})

    assert_problem(
        client.patch(f"{API}/planned-meals/{a}", json={"slot_id": slots["tea"]["id"]}), 422
    )
    assert client.patch(f"{API}/planned-meals/{b}", json={"servings": 5}).json()["servings"] == 5
    back = client.patch(f"{API}/planned-meals/{b}", json={"slot_id": slots["lunch"]["id"]})
    assert back.status_code == 200


def test_patch_servings(client: TestClient, lunch_cell: list[int]) -> None:
    meal = client.patch(f"{API}/planned-meals/{lunch_cell[0]}", json={"servings": 6}).json()
    assert meal["servings"] == 6
    assert meal["position"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"servings": 0},
        {"servings": None},
        {"position": -1},
        {"position": None},
        {"date": None},
        {"slot_id": "lunch"},
        {"recipe_id": 1},
        {"status": "cooked"},
    ],
)
def test_patch_validation(client: TestClient, lunch_cell: list[int], body: dict) -> None:
    assert_problem(client.patch(f"{API}/planned-meals/{lunch_cell[0]}", json=body), 422)


def test_patch_unknown_meal_or_slot_is_404(client: TestClient, lunch_cell: list[int]) -> None:
    assert_problem(client.patch(f"{API}/planned-meals/999999", json={"servings": 2}), 404)
    assert_problem(
        client.patch(f"{API}/planned-meals/{lunch_cell[0]}", json={"slot_id": 999_999}), 404
    )


def test_cooked_meal_can_move_but_not_change_servings(
    client: TestClient, db: Session, user: User, slots: dict, lunch_cell: list[int]
) -> None:
    a = lunch_cell[0]
    mark_cooked(db, a, user)

    assert_problem(client.patch(f"{API}/planned-meals/{a}", json={"servings": 9}), 409)
    moved = client.patch(f"{API}/planned-meals/{a}", json={"slot_id": slots["dinner"]["id"]})
    assert moved.status_code == 200
    assert moved.json()["status"] == "cooked"


# --- planned meals: delete -----------------------------------------------------------------


def test_delete_planned_meal_compacts_the_day(
    client: TestClient, slots: dict, lunch_cell: list[int]
) -> None:
    a, b, c = lunch_cell
    recipe = create_recipe(client, "Soup")
    d = plan(client, MONDAY, slots["dinner"]["id"], recipe["id"])["id"]

    assert client.delete(f"{API}/planned-meals/{b}").status_code == 204

    assert day_ids(client, MONDAY) == [a, c, d]
    assert_problem(client.get(f"{API}/planned-meals/{b}"), 404)
    assert_problem(client.delete(f"{API}/planned-meals/{b}"), 404)


def test_skipped_meal_can_be_deleted(
    client: TestClient, db: Session, lunch_cell: list[int]
) -> None:
    db.execute(
        update(PlannedMeal)
        .where(PlannedMeal.id == lunch_cell[0])
        .values(status=PlannedMealStatus.SKIPPED)
    )
    db.commit()

    assert client.delete(f"{API}/planned-meals/{lunch_cell[0]}").status_code == 204


def test_cooked_meal_cannot_be_deleted(
    client: TestClient, db: Session, user: User, lunch_cell: list[int]
) -> None:
    mark_cooked(db, lunch_cell[0], user)

    body = assert_problem(client.delete(f"{API}/planned-meals/{lunch_cell[0]}"), 409)

    assert "uncook" in body["detail"]
    assert client.get(f"{API}/planned-meals/{lunch_cell[0]}").status_code == 200


def test_database_ties_cooked_at_to_status(db: Session, user: User, lunch_cell: list[int]) -> None:
    with pytest.raises(IntegrityError, match="ck_planned_meals_cooked_at_iff_cooked"):
        db.execute(
            update(PlannedMeal)
            .where(PlannedMeal.id == lunch_cell[0])
            .values(status=PlannedMealStatus.COOKED)
        )


def test_deleting_a_meal_nulls_stock_movement_links(
    client: TestClient, db: Session, user: User, lunch_cell: list[int]
) -> None:
    flour = Ingredient(name="Flour", dimension=Dimension.MASS, default_unit="g")
    db.add(flour)
    db.flush()
    movement = StockMovement(
        ingredient_id=flour.id,
        delta_base=0,
        reason=StockReason.MANUAL,
        planned_meal_id=lunch_cell[0],
        user_id=user.id,
        source=StockSource.UI,
    )
    db.add(movement)
    db.commit()

    assert client.delete(f"{API}/planned-meals/{lunch_cell[0]}").status_code == 204

    db.expire_all()
    assert db.get(StockMovement, movement.id).planned_meal_id is None


def test_stock_movement_planned_meal_fk_is_enforced(db: Session, user: User) -> None:
    flour = Ingredient(name="Flour", dimension=Dimension.MASS, default_unit="g")
    db.add(flour)
    db.flush()
    db.add(
        StockMovement(
            ingredient_id=flour.id,
            delta_base=0,
            reason=StockReason.MANUAL,
            planned_meal_id=999_999,
            user_id=user.id,
            source=StockSource.UI,
        )
    )
    with pytest.raises(IntegrityError, match="fk_stock_movements_planned_meal_id_planned_meals"):
        db.flush()


# --- recipes delete hook -------------------------------------------------------------------


def test_deleting_a_planned_recipe_archives_it(
    client: TestClient, db: Session, slots: dict
) -> None:
    recipe = create_recipe(client)
    meal = plan(client, MONDAY, slots["lunch"]["id"], recipe["id"])

    response = client.delete(f"{API}/recipes/{recipe['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] is not None
    kept = client.get(f"{API}/planned-meals/{meal['id']}").json()
    assert kept["recipe"]["archived_at"] is not None


def test_deleting_an_unplanned_recipe_still_hard_deletes(client: TestClient, db: Session) -> None:
    recipe = create_recipe(client)

    assert client.delete(f"{API}/recipes/{recipe['id']}").status_code == 204
    db.expire_all()
    assert db.scalar(select(Recipe).where(Recipe.id == recipe["id"])) is None


def test_slot_lookup_matches_seed_table(db: Session) -> None:
    names = db.scalars(select(MealSlot.name).order_by(MealSlot.position)).all()
    assert names == ["Lunch", "Dinner", "Tea"]
