import datetime as dt
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jyj.models import ShoppingItemKind, ShoppingList, ShoppingListItem, ShoppingListStatus
from jyj.schemas.common import AmountIn, DecimalStr
from jyj.schemas.stock import DisplayQuantity
from jyj.shopping.aggregate import (
    CheckHaveItem,
    Contribution,
    MealRef,
    ShoppingItem,
    ShoppingPlan,
    UnconvertedLine,
)
from jyj.units import Dimension, base_unit, display_quantity, from_base, get_unit


class ShoppingRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: dt.date = Field(alias="from")
    end: dt.date = Field(alias="to")


class MealRefOut(BaseModel):
    planned_meal_id: int
    date: dt.date
    slot_id: int | None
    slot_name: str | None
    recipe_id: int
    recipe_name: str
    servings: int

    @classmethod
    def build(cls, ref: MealRef) -> Self:
        return cls(
            planned_meal_id=ref.planned_meal_id,
            date=ref.date,
            slot_id=ref.slot_id,
            slot_name=ref.slot_name,
            recipe_id=ref.recipe_id,
            recipe_name=ref.recipe_name,
            servings=ref.servings,
        )


class ContributionOut(BaseModel):
    meal: MealRefOut
    amount: DecimalStr
    unit: str
    amount_base: DecimalStr

    @classmethod
    def build(cls, c: Contribution) -> Self:
        return cls(
            meal=MealRefOut.build(c.meal),
            amount=c.amount,
            unit=c.unit_code,
            amount_base=c.amount_base,
        )


class UnconvertedOut(BaseModel):
    amount: DecimalStr
    unit: str
    reason: str
    recipe_names: list[str]


class PreviewUnconvertedOut(UnconvertedOut):
    meals: list[MealRefOut]

    @classmethod
    def build(cls, line: UnconvertedLine) -> Self:
        return cls(
            amount=line.amount,
            unit=line.unit_code,
            reason=line.reason.value,
            recipe_names=list(line.recipe_names),
            meals=[MealRefOut.build(m) for m in line.meals],
        )


class PreviewItemOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    category: str | None
    dimension: Dimension
    base_unit: str
    required_base: DecimalStr
    stock_base: DecimalStr
    reserved_base: DecimalStr
    available_base: DecimalStr
    to_buy_base: DecimalStr
    display: DisplayQuantity
    nothing_to_buy: bool
    contributions: list[ContributionOut]
    unconverted: list[PreviewUnconvertedOut]

    @classmethod
    def build(cls, item: ShoppingItem) -> Self:
        return cls(
            ingredient_id=item.ingredient_id,
            ingredient_name=item.name,
            category=item.category,
            dimension=item.dimension,
            base_unit=item.base_unit,
            required_base=item.required_base,
            stock_base=item.stock_base,
            reserved_base=item.reserved_base,
            available_base=item.available_base,
            to_buy_base=item.to_buy_base,
            display=DisplayQuantity(amount=item.display.amount, unit=item.display.unit.code),
            nothing_to_buy=item.nothing_to_buy,
            contributions=[ContributionOut.build(c) for c in item.contributions],
            unconverted=[PreviewUnconvertedOut.build(u) for u in item.unconverted],
        )


class CheckHaveOut(BaseModel):
    ingredient_id: int
    ingredient_name: str
    category: str | None
    recipe_names: list[str]
    meals: list[MealRefOut]

    @classmethod
    def build(cls, item: CheckHaveItem) -> Self:
        return cls(
            ingredient_id=item.ingredient_id,
            ingredient_name=item.name,
            category=item.category,
            recipe_names=list(item.recipe_names),
            meals=[MealRefOut.build(m) for m in item.meals],
        )


class ShoppingPreviewOut(BaseModel):
    start_date: dt.date
    end_date: dt.date
    today: dt.date
    items: list[PreviewItemOut]
    check_have: list[CheckHaveOut]

    @classmethod
    def build(cls, plan: ShoppingPlan) -> Self:
        return cls(
            start_date=plan.start,
            end_date=plan.end,
            today=plan.today,
            items=[PreviewItemOut.build(i) for i in plan.items],
            check_have=[CheckHaveOut.build(c) for c in plan.check_have],
        )


class ShoppingListItemOut(BaseModel):
    id: int
    list_id: int
    position: int
    kind: ShoppingItemKind
    ingredient_id: int
    ingredient_name: str
    category: str | None
    base_unit: str
    required_base: DecimalStr
    available_base: DecimalStr
    to_buy_base: DecimalStr
    display: DisplayQuantity
    checked: bool
    bought_base: DecimalStr | None
    bought_display: DisplayQuantity | None
    unconverted: list[UnconvertedOut]
    recipe_names: list[str]

    @classmethod
    def build(cls, item: ShoppingListItem) -> Self:
        display_unit = get_unit(item.display_unit)
        dimension = display_unit.dimension
        bought = None
        if item.bought_base is not None:
            shown = display_quantity(item.bought_base, dimension)
            bought = DisplayQuantity(amount=shown.amount, unit=shown.unit.code)
        return cls(
            id=item.id,
            list_id=item.list_id,
            position=item.position,
            kind=item.kind,
            ingredient_id=item.ingredient_id,
            ingredient_name=item.ingredient.name,
            category=item.category,
            base_unit=base_unit(dimension).code,
            required_base=item.required_base,
            available_base=item.available_base,
            to_buy_base=item.to_buy_base,
            display=DisplayQuantity(
                amount=from_base(item.to_buy_base, display_unit), unit=display_unit.code
            ),
            checked=item.checked,
            bought_base=item.bought_base,
            bought_display=bought,
            unconverted=[UnconvertedOut(**u) for u in item.unconverted],
            recipe_names=list(item.recipe_names),
        )


class ShoppingListSummary(BaseModel):
    id: int
    start_date: dt.date
    end_date: dt.date
    status: ShoppingListStatus
    created_by: int
    created_at: dt.datetime
    completed_at: dt.datetime | None
    item_count: int
    checked_count: int

    @classmethod
    def build(cls, shopping_list: ShoppingList, item_count: int, checked_count: int) -> Self:
        return cls(
            id=shopping_list.id,
            start_date=shopping_list.start_date,
            end_date=shopping_list.end_date,
            status=shopping_list.status,
            created_by=shopping_list.created_by,
            created_at=shopping_list.created_at,
            completed_at=shopping_list.completed_at,
            item_count=item_count,
            checked_count=checked_count,
        )


class ShoppingListOut(ShoppingListSummary):
    items: list[ShoppingListItemOut]

    @classmethod
    def build(cls, shopping_list: ShoppingList) -> Self:  # type: ignore[override]
        items = shopping_list.items
        summary = ShoppingListSummary.build(
            shopping_list, len(items), sum(1 for i in items if i.checked)
        )
        return cls(**summary.model_dump(), items=[ShoppingListItemOut.build(i) for i in items])


class ShoppingListItemUpdate(BaseModel):
    """Partial update. ``bought_quantity: null`` clears it (complete then buys ``to_buy``)."""

    model_config = ConfigDict(extra="forbid")

    checked: bool | None = None
    bought_quantity: AmountIn | None = Field(default=None, ge=0)
    # Defaults to the item's display unit.
    unit: str | None = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if "checked" in self.model_fields_set and self.checked is None:
            raise ValueError("checked must be true or false")
        if self.unit is not None and self.bought_quantity is None:
            raise ValueError("unit only applies together with bought_quantity")
        return self
