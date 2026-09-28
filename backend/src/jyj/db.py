from collections.abc import Callable, Iterator
from datetime import datetime
from functools import lru_cache

from sqlalchemy import DateTime, Engine, MetaData, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from jyj.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def session_scope(factory: Callable[[], Session]) -> Iterator[Session]:
    """Yield one session, committing on success and rolling back on any error."""
    with factory() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def get_db() -> Iterator[Session]:
    yield from session_scope(get_sessionmaker())
