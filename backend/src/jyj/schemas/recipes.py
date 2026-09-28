from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jyj.models import Recipe, RecipeIngredient
from jyj.schemas.common import AmountIn, DecimalStr
from jyj.schemas.ingredients import IngredientCreate
from jyj.schemas.stock import DisplayQuantity
from jyj.services.photos import photo_url, thumb_url
from jyj.services.recipes import (
    DESCRIPTION_MAX,
    LINES_MAX,
    NAME_MAX,
    NOTE_MAX,
    SERVINGS_MAX,
    ScaledLine,
)
from jyj.units import Dimension

Name = Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
Description = Annotated[str, Field(max_length=DESCRIPTION_MAX)]
Servings = Annotated[int, Field(ge=1, le=SERVINGS_MAX)]


class RecipeIngredientIn(BaseModel):
    """One line: an existing ingredient by id, or a new one created in the same transaction."""

    model_config = ConfigDict(extra="forbid")

    ingredient_id: int | None = None
    new_ingredient: IngredientCreate | None = None
    # Required for measurable units; may be omitted or 0 for "to taste" units such as pinch.
    amount_per_person: Annotated[AmountIn, Field(ge=0)] | None = None
    unit: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @model_validator(mode="after")
    def _exactly_one_ingredient(self) -> Self:
        if (self.ingredient_id is None) == (self.new_ingredient is None):
            raise ValueError("provide exactly one of ingredient_id or new_ingredient")
        return self


Lines = Annotated[list[RecipeIngredientIn], Field(max_length=LINES_MAX)]


class RecipeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description | None = None
    default_servings: Servings = 2
    ingredients: Lines = []


class RecipeUpdate(BaseModel):
    """Partial update: fields present in the body change; ``ingredients`` replaces the list."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Description | None = None
    default_servings: Servings | None = None
    ingredients: Lines | None = None
    archived: bool | None = None


class RecipeIngredientOut(BaseModel):
    id: int
    position: int
    ingredient_id: int
    ingredient_name: str
    dimension: Dimension
    amount_per_person: DecimalStr | None
    unit: str
    unit_dimension: Dimension
    note: str | None

    @classmethod
    def build(cls, line: RecipeIngredient) -> Self:
        return cls(
            id=line.id,
            position=line.position,
            ingredient_id=line.ingredient_id,
            ingredient_name=line.ingredient.name,
            dimension=line.ingredient.dimension,
            amount_per_person=line.amount_per_person,
            unit=line.unit_code,
            unit_dimension=line.unit_dimension,
            note=line.note,
        )


class PhotoCreditOut(BaseModel):
    provider: Literal["pexels", "openverse"]
    photographer: str
    photographer_url: str | None
    page_url: str | None
    title: str | None = None
    license: str | None = None
    license_url: str | None = None

    @classmethod
    def build(cls, raw: object) -> Self | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls.model_validate(raw)
        except ValueError:
            return None


class RecipeSummary(BaseModel):
    id: int
    name: str
    description: str | None
    photo_url: str | None
    photo_thumb_url: str | None
    photo_credit: PhotoCreditOut | None
    default_servings: int
    ingredient_count: int
    created_by: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    @classmethod
    def build(cls, recipe: Recipe) -> Self:
        return cls(
            id=recipe.id,
            name=recipe.name,
            description=recipe.description,
            photo_url=photo_url(recipe.photo_path),
            photo_thumb_url=thumb_url(recipe.photo_path),
            photo_credit=PhotoCreditOut.build(recipe.photo_credit) if recipe.photo_path else None,
            default_servings=recipe.default_servings,
            ingredient_count=len(recipe.ingredients),
            created_by=recipe.created_by,
            created_at=recipe.created_at,
            updated_at=recipe.updated_at,
            archived_at=recipe.archived_at,
        )


class RecipeOut(RecipeSummary):
    ingredients: list[RecipeIngredientOut]

    @classmethod
    def build(cls, recipe: Recipe) -> Self:
        return cls(
            **RecipeSummary.build(recipe).model_dump(),
            ingredients=[RecipeIngredientOut.build(line) for line in recipe.ingredients],
        )


class RecipePageOut(BaseModel):
    items: list[RecipeSummary]
    total: int
    page: int
    page_size: int


class ScaledIngredientOut(BaseModel):
    position: int
    ingredient_id: int
    ingredient_name: str
    amount_per_person: DecimalStr | None
    unit: str
    # amount_per_person x servings in ``unit``; null for "to taste" units.
    amount: DecimalStr | None
    display: DisplayQuantity | None
    note: str | None

    @classmethod
    def build(cls, scaled: ScaledLine) -> Self:
        line = scaled.line
        return cls(
            position=line.position,
            ingredient_id=line.ingredient_id,
            ingredient_name=line.ingredient.name,
            amount_per_person=line.amount_per_person,
            unit=line.unit_code,
            amount=scaled.amount,
            display=(
                DisplayQuantity(amount=scaled.display.amount, unit=scaled.display.unit.code)
                if scaled.display
                else None
            ),
            note=line.note,
        )


class ScaledRecipeOut(BaseModel):
    recipe_id: int
    name: str
    servings: int
    ingredients: list[ScaledIngredientOut]
