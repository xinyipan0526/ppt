from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from ppt_agent.storage.models import Base


def new_database_path() -> Path:
    directory = Path(".cache/test-migrations")
    directory.mkdir(parents=True, exist_ok=True)
    return (directory / f"{uuid4()}.db").resolve()


def migrated_engine():
    database_path = new_database_path()
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return create_engine(database_url)


def test_initial_migration_creates_workflow_runtime_tables() -> None:
    engine = migrated_engine()
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert tables == {
        "alembic_version",
        "projects",
        "workflow_runs",
        "workflow_steps",
        "pages",
        "workflow_interrupts",
        "workflow_events",
    }
    assert set(Base.metadata.tables) == tables - {"alembic_version"}


def test_event_sequence_is_unique_within_a_run() -> None:
    engine = migrated_engine()
    project_id = str(uuid4())
    run_id = str(uuid4())

    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES (:id, :name)"),
                {"id": project_id, "name": "Test project"},
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_runs "
                    "(id, project_id, workflow_name, workflow_version, status) "
                    "VALUES (:id, :project_id, 'deck_generation', 1, 'queued')"
                ),
                {"id": run_id, "project_id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_events "
                    "(event_id, run_id, run_seq, event_type, payload) "
                    "VALUES (:event_id, :run_id, 1, 'run.queued', '{}')"
                ),
                {"event_id": str(uuid4()), "run_id": run_id},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workflow_events "
                        "(event_id, run_id, run_seq, event_type, payload) "
                        "VALUES (:event_id, :run_id, 1, 'run.duplicate', '{}')"
                    ),
                    {"event_id": str(uuid4()), "run_id": run_id},
                )
    finally:
        engine.dispose()


def test_run_status_constraint_rejects_unknown_status() -> None:
    engine = migrated_engine()
    project_id = str(uuid4())

    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES (:id, :name)"),
                {"id": project_id, "name": "Test project"},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO workflow_runs "
                        "(id, project_id, workflow_name, workflow_version, status) "
                        "VALUES (:id, :project_id, 'deck_generation', 1, 'mystery')"
                    ),
                    {"id": str(uuid4()), "project_id": project_id},
                )
    finally:
        engine.dispose()
