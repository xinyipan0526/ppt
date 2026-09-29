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


def migration_config(database_path: Path) -> Config:
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_initial_migration_creates_workflow_runtime_tables() -> None:
    engine = migrated_engine()
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        reservation_uniques = inspector.get_unique_constraints("cost_reservations")
        usage_foreign_keys = inspector.get_foreign_keys("usage_records")
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
        "budget_accounts",
        "cost_reservations",
        "usage_records",
        "provider_operations",
        "provider_attempts",
        "provider_retry_authorizations",
        "provider_text_results",
        "artifacts",
        "artifact_edges",
        "artifact_references",
        "artifact_orphans",
        "planning_inputs",
        "source_materials",
        "provider_binary_results",
        "provider_vision_results",
        "production_inputs",
        "page_generations",
        "quality_reports",
        "exports",
        "page_edits",
        "material_attempts",
        "provider_retry_schedules",
    }
    assert set(Base.metadata.tables) == tables - {"alembic_version"}
    assert {tuple(item["column_names"]) for item in reservation_uniques} == {
        ("reservation_key",)
    }
    assert any(
        foreign_key["referred_table"] == "cost_reservations"
        for foreign_key in usage_foreign_keys
    )
    event_columns = {
        column["name"] for column in inspector.get_columns("workflow_events")
    }
    assert {
        "schema_version",
        "occurred_at",
        "recorded_at",
        "step_id",
        "page_id",
        "operation_id",
        "trace_id",
        "durability",
    } <= event_columns
    run_columns = {column["name"] for column in inspector.get_columns("workflow_runs")}
    step_columns = {column["name"] for column in inspector.get_columns("workflow_steps")}
    operation_columns = {
        column["name"] for column in inspector.get_columns("provider_operations")
    }
    assert {"retry_at", "recovery_metadata"} <= run_columns
    assert {"retry_at", "recovery_metadata"} <= step_columns
    assert {"run_id", "step_id", "page_id"} <= operation_columns
    planning_uniques = inspector.get_unique_constraints("planning_inputs")
    assert {tuple(item["column_names"]) for item in planning_uniques} == {
        ("run_id", "generation")
    }
    artifact_uniques = inspector.get_unique_constraints("artifacts")
    assert ("id", "project_id") in {
        tuple(item["column_names"]) for item in artifact_uniques
    }
    material_foreign_keys = inspector.get_foreign_keys("source_materials")
    assert sum(
        foreign_key["referred_table"] == "artifacts"
        and tuple(foreign_key["referred_columns"]) == ("id", "project_id")
        for foreign_key in material_foreign_keys
    ) == 2


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


def test_event_migration_preserves_existing_time_and_next_sequence() -> None:
    database_path = new_database_path()
    config = migration_config(database_path)
    command.upgrade(config, "0004_artifact_lifecycle")
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    project_id = str(uuid4())
    run_id = str(uuid4())
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES (:id, 'Migration')"),
                {"id": project_id},
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
                    "(event_id, run_id, run_seq, event_type, payload, created_at) "
                    "VALUES (:event_id, :run_id, 7, 'legacy.event', '{}', :created_at)"
                ),
                {
                    "event_id": str(uuid4()),
                    "run_id": run_id,
                    "created_at": "2024-01-02 03:04:05",
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            event = connection.execute(
                text(
                    "SELECT occurred_at, recorded_at FROM workflow_events "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).one()
            next_event_seq = connection.scalar(
                text("SELECT next_event_seq FROM workflow_runs WHERE id = :run_id"),
                {"run_id": run_id},
            )
    finally:
        engine.dispose()

    assert str(event.occurred_at) == "2024-01-02 03:04:05"
    assert str(event.recorded_at) == "2024-01-02 03:04:05"
    assert next_event_seq == 8
