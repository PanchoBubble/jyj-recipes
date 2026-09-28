"""Recipe tools: thin wrappers over ``jyj.services.recipes``."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jyj.chat.tools.common import Amount, Id, MeasurableDimension, Query, UnitCode, number
from jyj.chat.tools.registry import Tool, ToolContext
from jyj.models import Recipe
from jyj.services import recipes as recipes_service
from jyj.services.recipes import DESCRIPTION_MAX, LINES_MAX, NAME_MAX, NOTE_MAX, SERVINGS_MAX

SEARCH_LIMIT_MAX = 50
SEARCH_LIMIT_DEFAULT = 20

Servings = Annotated[int, Field(ge=1, le=SERVINGS_MAX)]


class NewIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    dimension: MeasurableDimension
    default_unit: UnitCode | None = None
    category: str | None = Field(default=None, max_length=50)


class RecipeLine(BaseModel):
    """Exactly one of ingredient_id (existing) or new_ingredient (created with the recipe)."""

    model_config = ConfigDict(extra="forbid")

    ingredient_id: Id | None = None
    new_ingredient: NewIngredient | None = None
    amount_per_person: Amount | None = Field(
        default=None, ge=0, description="Per person; null only for 'to taste' units like pinch."
    )
    unit: UnitCode
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @model_validator(mode="after")
    def _exactly_one_ingredient(self) -> Self:
        if (self.ingredient_id is None) == (self.new_ingredient is None):
            raise ValueError("provide exactly one of ingredient_id or new_ingredient")
        return self


Lines = Annotated[list[RecipeLine], Field(max_length=LINES_MAX)]


class SearchRecipesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Query | None = Field(default=None, description="Name substring; null lists all.")
    include_archived: bool | None = None
    limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT_MAX)] | None = None


class GetRecipeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: Id
    servings: Servings | None = Field(default=None, description="Also compute totals for N.")


class CreateRecipeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    default_servings: Servings | None = None
    ingredients: Lines


class UpdateRecipeArgs(BaseModel):
    """Null leaves a field unchanged; ``ingredients`` replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: Id
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    default_servings: Servings | None = None
    ingredients: Lines | None = Field(default=None, description="Replaces all lines, in order.")
    archived: bool | None = None

    def changes(self) -> dict:
        return self.model_dump(exclude={"recipe_id"}, exclude_none=True)

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.changes():
            raise ValueError("nothing to change")
        return self


class RecipeIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: Id


def _summary(recipe: Recipe) -> dict:
    return {
        "id": recipe.id,
        "name": recipe.name,
        "default_servings": recipe.default_servings,
        "ingredient_count": len(recipe.ingredients),
        "archived": recipe.archived_at is not None,
    }


def _lines(args: list[RecipeLine]) -> list[dict]:
    return [line.model_dump() for line in args]


def search_recipes(ctx: ToolContext, args: SearchRecipesArgs) -> dict:
    page = recipes_service.list_recipes(
        ctx.db,
        args.query,
        include_archived=bool(args.include_archived),
        limit=args.limit or SEARCH_LIMIT_DEFAULT,
    )
    return {"total": page.total, "items": [_summary(r) for r in page.items]}


def get_recipe(ctx: ToolContext, args: GetRecipeArgs) -> dict:
    recipe = recipes_service.get_recipe(ctx.db, args.recipe_id)
    lines = [
        {
            "ingredient_id": line.ingredient_id,
            "name": line.ingredient.name,
            "per_person": (
                None if line.amount_per_person is None else number(line.amount_per_person)
            ),
            "unit": line.unit_code,
            "note": line.note,
        }
        for line in recipe.ingredients
    ]
    if args.servings is not None:
        for out, scaled in zip(
            lines, recipes_service.scale_recipe(recipe, args.servings), strict=True
        ):
            out["total"] = None if scaled.amount is None else number(scaled.amount)
    return {
        **_summary(recipe),
        "description": recipe.description,
        "servings": args.servings,
        "ingredients": lines,
    }


def create_recipe(ctx: ToolContext, args: CreateRecipeArgs) -> dict:
    recipe = recipes_service.create_recipe(
        ctx.db,
        ctx.user,
        ctx.source,
        name=args.name,
        description=args.description,
        default_servings=args.default_servings or 2,
        ingredients=_lines(args.ingredients),
    )
    return _summary(recipe)


def update_recipe(ctx: ToolContext, args: UpdateRecipeArgs) -> dict:
    changes = args.changes()
    if args.ingredients is not None:
        changes["ingredients"] = _lines(args.ingredients)
    recipe = recipes_service.update_recipe(ctx.db, ctx.user, ctx.source, args.recipe_id, changes)
    return _summary(recipe)


def preview_delete_recipe(ctx: ToolContext, args: RecipeIdArgs) -> dict:
    recipe = recipes_service.get_recipe(ctx.db, args.recipe_id)
    return {"recipe_id": recipe.id, "name": recipe.name}


def delete_recipe(ctx: ToolContext, args: RecipeIdArgs) -> dict:
    outcome = recipes_service.delete_recipe(ctx.db, ctx.user, ctx.source, args.recipe_id)
    return {"recipe_id": args.recipe_id, "outcome": outcome.value}


TOOLS = (
    Tool(
        name="search_recipes",
        description="Find recipes by name. Returns ids, names and ingredient counts.",
        args_model=SearchRecipesArgs,
        kind="read",
        handler=search_recipes,
    ),
    Tool(
        name="get_recipe",
        description="One recipe with its per-person ingredient lines, optionally scaled.",
        args_model=GetRecipeArgs,
        kind="read",
        handler=get_recipe,
    ),
    Tool(
        name="create_recipe",
        description="Create a recipe; lines reference ingredient ids or create new ingredients.",
        args_model=CreateRecipeArgs,
        kind="write",
        handler=create_recipe,
    ),
    Tool(
        name="update_recipe",
        description="Change a recipe. Null fields stay as they are; ingredients replaces all.",
        args_model=UpdateRecipeArgs,
        kind="write",
        handler=update_recipe,
    ),
    Tool(
        name="delete_recipe",
        description="Delete a recipe (archived instead if meals use it).",
        args_model=RecipeIdArgs,
        kind="write",
        handler=delete_recipe,
        requires_confirmation=True,
        preview=preview_delete_recipe,
    ),
)
