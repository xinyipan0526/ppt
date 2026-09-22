from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
    session_scope,
)


def new_database_path() -> Path:
    directory = Path(".cache/test-databases")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{uuid4()}.db"


def test_sqlite_engine_applies_reliability_pragmas() -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=new_database_path()))

    try:
        with engine.connect() as connection:
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
            journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()
    finally:
        engine.dispose()

    assert foreign_keys == 1
    assert busy_timeout == 5_000
    assert journal_mode.lower() == "wal"
    assert synchronous == 2


def test_database_config_rejects_negative_wal_autocheckpoint() -> None:
    with pytest.raises(ValueError, match="wal_autocheckpoint"):
        DatabaseConfig(path=new_database_path(), wal_autocheckpoint=-1)


def test_sqlite_engine_applies_wal_autocheckpoint() -> None:
    engine = create_sqlite_engine(
        DatabaseConfig(path=new_database_path(), wal_autocheckpoint=321)
    )

    try:
        with engine.connect() as connection:
            actual = connection.execute(text("PRAGMA wal_autocheckpoint")).scalar_one()
    finally:
        engine.dispose()

    assert actual == 321


def test_session_scope_commits_successful_transaction() -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=new_database_path()))
    sessions = create_session_factory(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE records (value TEXT NOT NULL)"))

    try:
        with session_scope(sessions) as session:
            session.execute(text("INSERT INTO records (value) VALUES ('committed')"))

        with engine.connect() as connection:
            count = connection.execute(text("SELECT count(*) FROM records")).scalar_one()
    finally:
        engine.dispose()

    assert count == 1


def test_session_scope_rolls_back_failed_transaction() -> None:
    engine = create_sqlite_engine(DatabaseConfig(path=new_database_path()))
    sessions = create_session_factory(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE records (value TEXT NOT NULL)"))

    try:
        with pytest.raises(RuntimeError, match="stop transaction"):
            with session_scope(sessions) as session:
                session.execute(text("INSERT INTO records (value) VALUES ('discarded')"))
                raise RuntimeError("stop transaction")

        with engine.connect() as connection:
            count = connection.execute(text("SELECT count(*) FROM records")).scalar_one()
    finally:
        engine.dispose()

    assert count == 0
