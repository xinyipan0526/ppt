"""SQLite engine and short transaction helpers."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from sqlalchemy import Engine, URL, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


JournalMode: TypeAlias = Literal["WAL", "DELETE"]
SynchronousMode: TypeAlias = Literal["FULL", "NORMAL"]
SessionFactory: TypeAlias = sessionmaker[Session]


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """SQLite settings selected after workspace capability detection."""

    path: Path
    journal_mode: JournalMode = "WAL"
    synchronous: SynchronousMode = "FULL"
    busy_timeout_ms: int = 5_000
    wal_autocheckpoint: int = 1_000

    def __post_init__(self) -> None:
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        if self.wal_autocheckpoint < 0:
            raise ValueError("wal_autocheckpoint must be non-negative")


def create_sqlite_engine(config: DatabaseConfig) -> Engine:
    """Create a SQLite engine with reliability pragmas on every connection."""

    config.path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(config.path.resolve())),
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
            cursor.execute(f"PRAGMA journal_mode={config.journal_mode}")
            cursor.execute(f"PRAGMA wal_autocheckpoint={config.wal_autocheckpoint}")
            cursor.execute(f"PRAGMA synchronous={config.synchronous}")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> SessionFactory:
    """Create sessions optimized for explicit, short-lived transactions."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope(sessions: SessionFactory) -> Iterator[Session]:
    """Commit a unit of work, rolling it back if any exception escapes."""

    session = sessions()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
