"""Load exactly the rows ``compute_shopping`` needs: one query per table, no lazy loads."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from jyj.models import (
    Ingredient,
    MealSlot,
    PlannedMeal,
    PlannedMealStatus,
    Recipe,
    RecipeIngredient,
    StockItem,
)
from jyj.shopping.aggregate import (
    IngredientInput,
    MealInput,
    RecipeLineInput,
    ShoppingPlan,
    compute_shopping,
)
from jyj.units import IngredientConversions


def load_and_compute(db: Session, start: dt.date, end: dt.date, today: dt.date) -> ShoppingPlan:
    if start > end:
        raise ValueError("start must be on or before end")

    meal_rows = db.execute(
        select(
            PlannedMeal.id,
            PlannedMeal.date,
            PlannedMeal.slot_id,
            MealSlot.name.label("slot_name"),
            MealSlot.position.label("slot_position"),
            PlannedMeal.recipe_id,
            Recipe.name.label("recipe_name"),
            PlannedMeal.servings,
            PlannedMeal.status,
        )
        .join(MealSlot, MealSlot.id == PlannedMeal.slot_id)
        .join(Recipe, Recipe.id == PlannedMeal.recipe_id)
        .where(
            PlannedMeal.status == PlannedMealStatus.PLANNED,
            PlannedMeal.date >= min(today, start),
            PlannedMeal.date <= end,
        )
    ).all()
    meals = [
        MealInput(
            id=r.id,
            date=r.date,
            slot_id=r.slot_id,
            slot_name=r.slot_name,
            slot_position=r.slot_position,
            recipe_id=r.recipe_id,
            recipe_name=r.recipe_name,
            servings=r.servings,
            status=r.status.value,
        )
        for r in meal_rows
    ]
    if not meals:
        return compute_shopping([], [], [], {}, today, start, end)

    recipe_ids = {m.recipe_id for m in meals}
    lines = [
        RecipeLineInput(
            recipe_id=r.recipe_id,
            ingredient_id=r.ingredient_id,
            amount_per_person=r.amount_per_person,
            unit_code=r.unit_code,
            position=r.position,
        )
        for r in db.execute(
            select(
                RecipeIngredient.recipe_id,
                RecipeIngredient.ingredient_id,
                RecipeIngredient.amount_per_person,
                RecipeIngredient.unit_code,
                RecipeIngredient.position,
            ).where(RecipeIngredient.recipe_id.in_(recipe_ids))
        )
    ]

    ingredient_ids = {line.ingredient_id for line in lines}
    ingredients = (
        [
            IngredientInput(
                id=r.id,
                name=r.name,
                category=r.category,
                dimension=r.dimension,
                conversions=IngredientConversions(r.grams_per_ml, r.grams_per_piece),
            )
            for r in db.execute(
                select(
                    Ingredient.id,
                    Ingredient.name,
                    Ingredient.category,
                    Ingredient.dimension,
                    Ingredient.grams_per_ml,
                    Ingredient.grams_per_piece,
                ).where(Ingredient.id.in_(ingredient_ids))
            )
        ]
        if ingredient_ids
        else []
    )
    stock = (
        {
            r.ingredient_id: r.quantity_base
            for r in db.execute(
                select(StockItem.ingredient_id, StockItem.quantity_base).where(
                    StockItem.ingredient_id.in_(ingredient_ids)
                )
            )
        }
        if ingredient_ids
        else {}
    )
    return compute_shopping(meals, lines, ingredients, stock, today, start, end)
