"""Meal slots: the configurable, ordered columns of the meal calendar."""

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jyj.models import MealSlot, PlannedMeal, StockSource, User
from jyj.services.errors import ConflictError, InvalidError, NotFoundError

NAME_MAX = 50
UPDATABLE_FIELDS = frozenset({"name", "active"})


def list_slots(db: Session) -> list[MealSlot]:
    return list(db.scalars(select(MealSlot).order_by(MealSlot.position, MealSlot.id)))


def get_slot(db: Session, slot_id: int, *, lock: bool = False) -> MealSlot:
    stmt = select(MealSlot).where(MealSlot.id == slot_id)
    if lock:
        stmt = stmt.with_for_update(key_share=True)
    slot = db.scalar(stmt)
    if slot is None:
        raise NotFoundError(f"meal slot {slot_id} not found")
    return slot


def create_slot(
    db: Session, user: User, source: StockSource, *, name: str, active: bool = True
) -> MealSlot:
    cleaned = _clean_name(name)
    _ensure_name_free(db, cleaned)
    position = db.scalar(select(func.coalesce(func.max(MealSlot.position) + 1, 0)))
    slot = MealSlot(name=cleaned, position=position, active=bool(active))
    db.add(slot)
    _flush_unique(db, cleaned)
    db.refresh(slot)
    return slot


def update_slot(
    db: Session, user: User, source: StockSource, slot_id: int, changes: Mapping[str, Any]
) -> MealSlot:
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidError(f"unknown fields: {', '.join(sorted(unknown))}")
    nulled = sorted(f for f in UPDATABLE_FIELDS & set(changes) if changes[f] is None)
    if nulled:
        raise InvalidError(f"fields cannot be null: {', '.join(nulled)}")

    slot = get_slot(db, slot_id)
    if "name" in changes:
        name = _clean_name(changes["name"])
        if name.lower() != slot.name.lower():
            _ensure_name_free(db, name)
        slot.name = name
    if "active" in changes:
        slot.active = bool(changes["active"])
    _flush_unique(db, slot.name)
    db.refresh(slot)
    return slot


def delete_slot(db: Session, user: User, source: StockSource, slot_id: int) -> None:
    slot = get_slot(db, slot_id, lock=True)
    if db.scalar(select(exists().where(PlannedMeal.slot_id == slot.id))):
        raise ConflictError(
            "meal slot has planned meals and cannot be deleted; deactivate it instead",
            references=["planned_meals"],
        )
    db.delete(slot)
    db.flush()
    _renumber(list_slots(db))
    db.flush()


def reorder_slots(
    db: Session, user: User, source: StockSource, ids: Sequence[int]
) -> list[MealSlot]:
    """Set the order of every slot at once; ``ids`` must list each existing slot exactly once."""
    slots = {
        s.id: s
        for s in db.scalars(select(MealSlot).order_by(MealSlot.id).with_for_update(key_share=True))
    }
    if len(set(ids)) != len(ids):
        raise InvalidError("ids must not contain duplicates")
    missing, unknown = sorted(set(slots) - set(ids)), sorted(set(ids) - set(slots))
    if missing or unknown:
        raise InvalidError(
            "ids must list every meal slot exactly once", missing=missing, unknown=unknown
        )
    ordered = [slots[i] for i in ids]
    _renumber(ordered)
    db.flush()
    return ordered


def _renumber(slots: Sequence[MealSlot]) -> None:
    for position, slot in enumerate(slots):
        if slot.position != position:
            slot.position = position


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise InvalidError("name must not be blank")
    if len(cleaned) > NAME_MAX:
        raise InvalidError(f"name must be at most {NAME_MAX} characters")
    return cleaned


def _ensure_name_free(db: Session, name: str) -> None:
    taken = db.scalar(select(MealSlot.id).where(func.lower(MealSlot.name) == name.lower()))
    if taken is not None:
        raise ConflictError(f"a meal slot named {name!r} already exists", existing_id=taken)


def _flush_unique(db: Session, name: str) -> None:
    # The pre-check races with concurrent writes; the unique index is the real guard.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if "uq_meal_slots_name_lower" in str(exc.orig):
            raise ConflictError(f"a meal slot named {name!r} already exists") from None
        raise
