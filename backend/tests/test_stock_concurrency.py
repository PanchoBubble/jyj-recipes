"""Row locking needs real commits across connections, so these tests commit and clean up."""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from jyj.models import Ingredient, StockItem, StockMovement, StockSource, User
from jyj.services import stock
from jyj.units import Dimension


@pytest.fixture
def factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def seeded(factory: sessionmaker[Session]) -> Iterator[tuple[User, Ingredient]]:
    tag = uuid.uuid4().hex[:12]
    with factory() as db:
        user = User(username=f"conc-{tag}", display_name="Conc", password_hash="x")
        ingredient = Ingredient(name=f"Conc {tag}", dimension=Dimension.COUNT, default_unit="piece")
        db.add_all([user, ingredient])
        db.commit()
    try:
        yield user, ingredient
    finally:
        with factory() as db:
            db.execute(delete(StockMovement).where(StockMovement.ingredient_id == ingredient.id))
            db.execute(delete(StockItem).where(StockItem.ingredient_id == ingredient.id))
            db.execute(delete(Ingredient).where(Ingredient.id == ingredient.id))
            db.execute(delete(User).where(User.id == user.id))
            db.commit()


def _state(factory: sessionmaker[Session], ingredient_id: int) -> tuple[Decimal, Decimal, int]:
    with factory() as db:
        qty = db.scalar(
            select(StockItem.quantity_base).where(StockItem.ingredient_id == ingredient_id)
        )
        total, count = db.execute(
            select(func.coalesce(func.sum(StockMovement.delta_base), 0), func.count()).where(
                StockMovement.ingredient_id == ingredient_id
            )
        ).one()
        return qty, total, count


def _run_concurrently(n: int, work) -> list:
    barrier = threading.Barrier(n)

    def task(i: int):
        barrier.wait(timeout=10)
        return work(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result(timeout=30) for f in [pool.submit(task, i) for i in range(n)]]


def test_concurrent_first_adjustments_do_not_lose_updates(
    factory: sessionmaker[Session], seeded: tuple[User, Ingredient]
) -> None:
    user, ingredient = seeded
    n = 12

    def add_one(_: int) -> None:
        with factory() as db:
            stock.adjust_stock(db, user, StockSource.UI, ingredient.id, Decimal(1), "piece")
            db.commit()

    _run_concurrently(n, add_one)

    assert _state(factory, ingredient.id) == (Decimal(n), Decimal(n), n)


def test_concurrent_withdrawals_floor_once(
    factory: sessionmaker[Session], seeded: tuple[User, Ingredient]
) -> None:
    user, ingredient = seeded
    with factory() as db:
        stock.set_stock(db, user, StockSource.UI, ingredient.id, Decimal(3), "piece")
        db.commit()

    def take_two(_: int) -> tuple[Decimal, Decimal]:
        with factory() as db:
            change = stock.adjust_stock(
                db, user, StockSource.CHAT, ingredient.id, Decimal(-2), "piece"
            )
            db.commit()
            return change.movement.delta_base, change.movement.shortfall_base

    results = sorted(_run_concurrently(2, take_two))

    assert results == [(Decimal(-2), Decimal(0)), (Decimal(-1), Decimal(1))]
    assert _state(factory, ingredient.id) == (Decimal(0), Decimal(0), 3)


def test_second_writer_blocks_until_first_commits(
    factory: sessionmaker[Session], seeded: tuple[User, Ingredient]
) -> None:
    user, ingredient = seeded
    first = factory()
    stock.adjust_stock(first, user, StockSource.UI, ingredient.id, Decimal(5), "piece")
    first.flush()
    done = threading.Event()

    def second() -> Decimal:
        with factory() as db:
            change = stock.adjust_stock(
                db, user, StockSource.UI, ingredient.id, Decimal(1), "piece"
            )
            db.commit()
            done.set()
            return change.item.quantity_base

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(second)
        assert not done.wait(timeout=0.5), "second writer did not wait for the row lock"
        first.commit()
        first.close()
        assert future.result(timeout=10) == Decimal(6)

    assert _state(factory, ingredient.id) == (Decimal(6), Decimal(6), 2)
