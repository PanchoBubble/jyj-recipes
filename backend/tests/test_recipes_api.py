from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import Ingredient, Recipe, RecipeIngredient, User
from jyj.services import auth as auth_service
from jyj.services import recipes as recipes_service
from jyj.units import Dimension

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


def ingredient(client: TestClient, name: str, dimension: str = "mass", **fields) -> dict:
    response = client.post(
        f"{API}/ingredients", json={"name": name, "dimension": dimension, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def line(ingredient_id: int, amount: str | None, unit: str, **fields) -> dict:
    return {"ingredient_id": ingredient_id, "amount_per_person": amount, "unit": unit, **fields}


def create_recipe(client: TestClient, name: str = "Pancakes", **fields) -> dict:
    response = client.post(f"{API}/recipes", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def pantry(client: TestClient) -> dict[str, dict]:
    return {
        "flour": ingredient(client, "Flour"),
        "milk": ingredient(client, "Milk", "volume", grams_per_ml="1.03"),
        "egg": ingredient(client, "Egg", "count", grams_per_piece="50"),
        "salt": ingredient(client, "Salt"),
    }


# --- auth and CSRF -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/recipes"),
        ("POST", "/recipes"),
        ("GET", "/recipes/1"),
        ("PATCH", "/recipes/1"),
        ("DELETE", "/recipes/1"),
        ("GET", "/recipes/1/scaled?servings=2"),
    ],
)
def test_endpoints_require_a_session(anon: TestClient, method: str, path: str) -> None:
    assert_problem(anon.request(method, API + path, headers=CSRF, json={}), 401)


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/recipes"), ("PATCH", "/recipes/1"), ("DELETE", "/recipes/1")],
)
def test_mutations_require_csrf_header(client: TestClient, method: str, path: str) -> None:
    client.headers.pop("X-Requested-With")
    assert_problem(client.request(method, API + path, json={}), 403)


# --- create / read -------------------------------------------------------------------------


def test_nested_create_keeps_order_and_returns_lines(
    client: TestClient, pantry: dict, user: User
) -> None:
    recipe = create_recipe(
        client,
        name="  Crêpes  ",
        description="Thin ones",
        default_servings=4,
        ingredients=[
            line(pantry["flour"]["id"], "62.5", "g"),
            line(pantry["egg"]["id"], "0.5", "piece", note="  beaten  "),
            line(pantry["milk"]["id"], "125", "ml"),
            line(pantry["salt"]["id"], None, "pinch"),
        ],
    )

    assert recipe["name"] == "Crêpes"
    assert recipe["default_servings"] == 4
    assert recipe["created_by"] == user.id
    assert recipe["photo_path"] is None
    assert recipe["archived_at"] is None
    assert recipe["ingredient_count"] == 4
    lines = recipe["ingredients"]
    assert [row["position"] for row in lines] == [0, 1, 2, 3]
    assert [row["ingredient_name"] for row in lines] == ["Flour", "Egg", "Milk", "Salt"]
    assert lines[0]["amount_per_person"] == "62.500"
    assert lines[1]["note"] == "beaten"
    assert lines[3]["amount_per_person"] is None
    assert lines[3]["unit_dimension"] == "none"

    fetched = client.get(f"{API}/recipes/{recipe['id']}").json()
    assert fetched == recipe


def test_default_servings_is_two(client: TestClient) -> None:
    assert create_recipe(client)["default_servings"] == 2


def test_new_ingredient_is_created_inline(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(
        client,
        ingredients=[
            line(pantry["flour"]["id"], "100", "g"),
            {
                "new_ingredient": {"name": "Buttermilk", "dimension": "volume"},
                "amount_per_person": "0.5",
                "unit": "cup",
            },
        ],
    )

    created = recipe["ingredients"][1]
    assert created["ingredient_name"] == "Buttermilk"
    assert created["dimension"] == "volume"
    listed = client.get(f"{API}/ingredients", params={"q": "buttermilk"}).json()
    assert [i["id"] for i in listed] == [created["ingredient_id"]]
    assert listed[0]["default_unit"] == "ml"


def test_inline_ingredient_rolls_back_when_a_later_row_fails(
    client: TestClient, pantry: dict
) -> None:
    response = client.post(
        f"{API}/recipes",
        json={
            "name": "Broken",
            "ingredients": [
                {
                    "new_ingredient": {"name": "Ghee", "dimension": "mass"},
                    "amount_per_person": "10",
                    "unit": "g",
                },
                line(pantry["flour"]["id"], "1", "ml"),
            ],
        },
    )

    problem = assert_problem(response, 422)
    assert problem["row"] == 1
    assert problem["detail"].startswith("ingredients[1]:")
    assert client.get(f"{API}/ingredients", params={"q": "ghee"}).json() == []
    assert client.get(f"{API}/recipes").json()["total"] == 0


def test_inline_ingredient_name_clash_is_409_naming_the_row(
    client: TestClient, pantry: dict
) -> None:
    response = client.post(
        f"{API}/recipes",
        json={
            "name": "Dup",
            "ingredients": [
                {
                    "new_ingredient": {"name": "flour", "dimension": "mass"},
                    "amount_per_person": "1",
                    "unit": "g",
                }
            ],
        },
    )

    problem = assert_problem(response, 409)
    assert problem["row"] == 0
    assert problem["existing_id"] == pantry["flour"]["id"]


def test_missing_ingredient_is_404_naming_the_row(client: TestClient) -> None:
    problem = assert_problem(
        client.post(f"{API}/recipes", json={"name": "X", "ingredients": [line(999999, "1", "g")]}),
        404,
    )
    assert problem["row"] == 0


# --- unit validation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "amount", "unit"),
    [
        ("flour", "100", "kg"),
        ("milk", "1", "tbsp"),
        ("milk", "100", "g"),  # via grams_per_ml
        ("egg", "100", "g"),  # via grams_per_piece
        ("flour", None, "to_taste"),
        ("flour", "0", "pinch"),
        ("flour", "2", "pinch"),
    ],
)
def test_convertible_units_are_accepted(
    client: TestClient, pantry: dict, key: str, amount: str | None, unit: str
) -> None:
    create_recipe(client, ingredients=[line(pantry[key]["id"], amount, unit)])


@pytest.mark.parametrize(
    ("key", "unit", "reason"),
    [
        ("flour", "ml", "missing_grams_per_ml"),
        ("flour", "piece", "missing_grams_per_piece"),
        ("salt", "cup", "missing_grams_per_ml"),
        ("egg", "cup", "missing_grams_per_ml"),
        ("milk", "piece", "missing_grams_per_piece"),
    ],
)
def test_unconvertible_units_are_422_naming_the_row(
    client: TestClient, pantry: dict, key: str, unit: str, reason: str
) -> None:
    body = {
        "name": "Bad",
        "ingredients": [
            line(pantry["egg"]["id"], "1", "piece"),
            line(pantry[key]["id"], "1", unit),
        ],
    }
    problem = assert_problem(client.post(f"{API}/recipes", json=body), 422)
    assert problem["row"] == 1
    assert problem["reason"] == reason
    assert "ingredients[1]" in problem["detail"]


def test_cross_dimension_needs_the_right_factor(client: TestClient) -> None:
    oil = ingredient(client, "Oil", "volume")
    problem = assert_problem(
        client.post(
            f"{API}/recipes", json={"name": "X", "ingredients": [line(oil["id"], "5", "g")]}
        ),
        422,
    )
    assert problem["reason"] == "missing_grams_per_ml"

    client.patch(f"{API}/ingredients/{oil['id']}", json={"grams_per_ml": "0.92"})
    create_recipe(client, ingredients=[line(oil["id"], "5", "g")])


@pytest.mark.parametrize(
    ("amount", "unit", "status"),
    [
        (None, "g", 422),
        ("0", "g", 422),
        ("-1", "g", 422),
        ("-1", "pinch", 422),
        ("NaN", "g", 422),
        ("1e12", "g", 422),
        ("1", "furlong", 422),
    ],
)
def test_line_amount_validation(
    client: TestClient, pantry: dict, amount: str | None, unit: str, status: int
) -> None:
    body = {"name": "X", "ingredients": [line(pantry["flour"]["id"], amount, unit)]}
    assert_problem(client.post(f"{API}/recipes", json=body), status)


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "X", "default_servings": 0},
        {"name": "X", "default_servings": 101},
        {"name": "X", "photo_path": "/etc/passwd"},
        {"name": "X", "ingredients": [{"amount_per_person": "1", "unit": "g"}]},
        {
            "name": "X",
            "ingredients": [
                {
                    "ingredient_id": 1,
                    "new_ingredient": {"name": "A", "dimension": "mass"},
                    "amount_per_person": "1",
                    "unit": "g",
                }
            ],
        },
    ],
)
def test_create_validation(client: TestClient, body: dict) -> None:
    assert_problem(client.post(f"{API}/recipes", json=body), 422)


def test_database_check_rejects_zero_amount_for_measurable_units(db: Session, user: User) -> None:
    flour = Ingredient(name="Rye", dimension=Dimension.MASS, default_unit="g")
    recipe = Recipe(name="Raw", created_by=user.id)
    db.add_all([flour, recipe])
    db.flush()
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            ingredient_id=flour.id,
            amount_per_person=Decimal(0),
            unit_code="g",
            unit_dimension=Dimension.MASS,
            position=0,
        )
    )
    with pytest.raises(IntegrityError, match="amount_positive_unless_dimensionless"):
        db.flush()
    db.rollback()


def test_database_fk_keeps_unit_dimension_honest(db: Session, user: User) -> None:
    flour = Ingredient(name="Spelt", dimension=Dimension.MASS, default_unit="g")
    recipe = Recipe(name="Raw", created_by=user.id)
    db.add_all([flour, recipe])
    db.flush()
    db.add(
        RecipeIngredient(
            recipe_id=recipe.id,
            ingredient_id=flour.id,
            amount_per_person=None,
            unit_code="g",
            unit_dimension=Dimension.NONE,
            position=0,
        )
    )
    with pytest.raises(IntegrityError, match="fk_recipe_ingredients_unit_code_units"):
        db.flush()
    db.rollback()


# --- update / reorder ----------------------------------------------------------------------


def test_patch_replaces_and_reorders_lines(client: TestClient, pantry: dict, db: Session) -> None:
    recipe = create_recipe(
        client,
        ingredients=[
            line(pantry["flour"]["id"], "100", "g"),
            line(pantry["milk"]["id"], "200", "ml"),
            line(pantry["egg"]["id"], "1", "piece"),
        ],
    )

    response = client.patch(
        f"{API}/recipes/{recipe['id']}",
        json={
            "ingredients": [
                line(pantry["egg"]["id"], "2", "piece"),
                line(pantry["flour"]["id"], "0.1", "kg"),
            ]
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert [(r["ingredient_name"], r["position"]) for r in updated["ingredients"]] == [
        ("Egg", 0),
        ("Flour", 1),
    ]
    assert updated["ingredients"][1]["unit"] == "kg"
    assert updated["name"] == "Pancakes"
    assert updated["updated_at"] >= recipe["updated_at"]
    db.expire_all()
    count = db.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == recipe["id"])
    )
    assert count == 2


def test_patch_without_ingredients_keeps_them(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["flour"]["id"], "100", "g")])

    updated = client.patch(
        f"{API}/recipes/{recipe['id']}",
        json={"name": "Waffles", "description": None, "default_servings": 3},
    ).json()

    assert updated["name"] == "Waffles"
    assert updated["default_servings"] == 3
    assert [r["id"] for r in updated["ingredients"]] == [r["id"] for r in recipe["ingredients"]]


def test_patch_can_clear_ingredients(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["flour"]["id"], "100", "g")])
    updated = client.patch(f"{API}/recipes/{recipe['id']}", json={"ingredients": []}).json()
    assert updated["ingredients"] == []


def test_failed_patch_leaves_lines_untouched(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["flour"]["id"], "100", "g")])

    response = client.patch(
        f"{API}/recipes/{recipe['id']}",
        json={
            "name": "Renamed",
            "ingredients": [
                {
                    "new_ingredient": {"name": "Yeast", "dimension": "mass"},
                    "amount_per_person": "1",
                    "unit": "g",
                },
                line(pantry["flour"]["id"], "1", "piece"),
            ],
        },
    )

    assert_problem(response, 422)
    fetched = client.get(f"{API}/recipes/{recipe['id']}").json()
    assert fetched == recipe
    assert client.get(f"{API}/ingredients", params={"q": "yeast"}).json() == []


@pytest.mark.parametrize(
    "body",
    [{"name": None}, {"default_servings": None}, {"ingredients": None}, {"archived": None}],
)
def test_patch_rejects_null_required_fields(client: TestClient, body: dict) -> None:
    recipe = create_recipe(client)
    assert_problem(client.patch(f"{API}/recipes/{recipe['id']}", json=body), 422)


def test_archive_and_unarchive_via_patch(client: TestClient) -> None:
    recipe = create_recipe(client)

    archived = client.patch(f"{API}/recipes/{recipe['id']}", json={"archived": True}).json()
    assert archived["archived_at"] is not None
    assert client.get(f"{API}/recipes").json()["total"] == 0
    assert client.get(f"{API}/recipes", params={"include_archived": True}).json()["total"] == 1

    restored = client.patch(f"{API}/recipes/{recipe['id']}", json={"archived": False}).json()
    assert restored["archived_at"] is None


def test_missing_recipe_is_404(client: TestClient) -> None:
    assert_problem(client.get(f"{API}/recipes/999999"), 404)
    assert_problem(client.patch(f"{API}/recipes/999999", json={"name": "X"}), 404)
    assert_problem(client.delete(f"{API}/recipes/999999"), 404)
    assert_problem(client.get(f"{API}/recipes/999999/scaled", params={"servings": 2}), 404)


# --- delete --------------------------------------------------------------------------------


def test_delete_unreferenced_recipe_is_hard_delete(
    client: TestClient, pantry: dict, db: Session
) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["flour"]["id"], "100", "g")])

    response = client.delete(f"{API}/recipes/{recipe['id']}")

    assert response.status_code == 204
    assert_problem(client.get(f"{API}/recipes/{recipe['id']}"), 404)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(RecipeIngredient)) == 0


def test_delete_referenced_recipe_archives_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = create_recipe(client)
    monkeypatch.setitem(recipes_service.REFERENCE_CHECKS, "planned_meals", lambda db, rid: True)

    response = client.delete(f"{API}/recipes/{recipe['id']}")

    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] is not None
    assert client.get(f"{API}/recipes/{recipe['id']}").json()["archived_at"] is not None


def test_deleting_an_ingredient_used_by_a_recipe_is_409(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["egg"]["id"], "1", "piece")])

    problem = assert_problem(client.delete(f"{API}/ingredients/{pantry['egg']['id']}"), 409)
    assert problem["references"] == ["recipe_ingredients"]
    dimension_change = client.patch(
        f"{API}/ingredients/{pantry['egg']['id']}", json={"dimension": "mass"}
    )
    assert_problem(dimension_change, 409)

    client.delete(f"{API}/recipes/{recipe['id']}")
    assert client.delete(f"{API}/ingredients/{pantry['egg']['id']}").status_code == 204


# --- scaling -------------------------------------------------------------------------------


def test_scaled_multiplies_per_person_amounts(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(
        client,
        ingredients=[
            line(pantry["flour"]["id"], "0.1", "kg"),
            line(pantry["milk"]["id"], "0.333", "l"),
            line(pantry["egg"]["id"], "0.5", "piece"),
            line(pantry["milk"]["id"], "1.5", "tbsp", note="for the pan"),
            line(pantry["flour"]["id"], "333.333", "g"),
            line(pantry["salt"]["id"], None, "pinch"),
        ],
    )

    response = client.get(f"{API}/recipes/{recipe['id']}/scaled", params={"servings": 3})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["servings"] == 3
    rows = [(r["amount"], r["unit"], r["display"]) for r in body["ingredients"]]
    assert rows == [
        ("0.300", "kg", {"amount": "300.000", "unit": "g"}),
        ("0.999", "l", {"amount": "999.000", "unit": "ml"}),
        ("1.500", "piece", {"amount": "1.500", "unit": "piece"}),
        ("4.500", "tbsp", {"amount": "67.500", "unit": "ml"}),
        ("999.999", "g", {"amount": "999.999", "unit": "g"}),
        (None, "pinch", None),
    ]
    assert body["ingredients"][3]["note"] == "for the pan"
    assert body["ingredients"][0]["amount_per_person"] == "0.100"


def test_scaled_display_switches_to_large_units(client: TestClient, pantry: dict) -> None:
    recipe = create_recipe(client, ingredients=[line(pantry["flour"]["id"], "250", "g")])

    body = client.get(f"{API}/recipes/{recipe['id']}/scaled", params={"servings": 6}).json()

    assert body["ingredients"][0]["amount"] == "1500.000"
    assert body["ingredients"][0]["display"] == {"amount": "1.500", "unit": "kg"}


def test_scale_recipe_is_exact_decimal(client: TestClient, pantry: dict, db: Session) -> None:
    recipe_id = create_recipe(client, ingredients=[line(pantry["milk"]["id"], "0.1", "ml")])["id"]

    scaled = recipes_service.scale_recipe(db.get(Recipe, recipe_id), 3)

    assert scaled[0].amount == Decimal("0.300")
    assert isinstance(scaled[0].amount, Decimal)


@pytest.mark.parametrize("servings", ["0", "-1", "101", "two", ""])
def test_scaled_rejects_bad_servings(client: TestClient, servings: str) -> None:
    recipe = create_recipe(client)
    response = client.get(f"{API}/recipes/{recipe['id']}/scaled", params={"servings": servings})
    assert_problem(response, 422)


def test_scaled_requires_servings(client: TestClient) -> None:
    recipe = create_recipe(client)
    assert_problem(client.get(f"{API}/recipes/{recipe['id']}/scaled"), 422)


# --- list, search, pagination --------------------------------------------------------------


def test_search_is_case_insensitive_and_escapes_wildcards(client: TestClient) -> None:
    for name in ("Banana bread", "banana split", "Carrot cake", "100% rye"):
        create_recipe(client, name=name)

    names = [
        r["name"] for r in client.get(f"{API}/recipes", params={"q": "BANANA"}).json()["items"]
    ]
    assert names == ["Banana bread", "banana split"]
    wildcard = client.get(f"{API}/recipes", params={"q": "%"}).json()["items"]
    assert [r["name"] for r in wildcard] == ["100% rye"]


def test_pagination(client: TestClient) -> None:
    for i in range(5):
        create_recipe(client, name=f"Recipe {i}")

    first = client.get(f"{API}/recipes", params={"page_size": 2}).json()
    third = client.get(f"{API}/recipes", params={"page": 3, "page_size": 2}).json()
    beyond = client.get(f"{API}/recipes", params={"page": 4, "page_size": 2}).json()

    assert first["total"] == 5
    assert [r["name"] for r in first["items"]] == ["Recipe 0", "Recipe 1"]
    assert (first["page"], first["page_size"]) == (1, 2)
    assert [r["name"] for r in third["items"]] == ["Recipe 4"]
    assert beyond["items"] == []
    assert "ingredients" not in first["items"][0]


@pytest.mark.parametrize(
    "params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"q": "x" * 101}]
)
def test_list_rejects_bad_params(client: TestClient, params: dict) -> None:
    assert_problem(client.get(f"{API}/recipes", params=params), 422)
