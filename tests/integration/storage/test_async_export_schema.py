import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_async_export_migration_adds_execution_metadata_and_state_constraint(tmp_path: Path) -> None:
    path = tmp_path / "async-exports.db"
    config = _config(path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inspector = inspect(engine)
        columns = {item["name"] for item in inspector.get_columns("exports")}
        checks = {item["name"] for item in inspector.get_check_constraints("exports")}
    finally:
        engine.dispose()

    assert {"attempt_count", "started_at", "finished_at"} <= columns
    assert "ck_exports_status" in checks


def test_async_export_migration_normalizes_legacy_stale_rows(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.db"
    config = _config(path)
    command.upgrade(config, "0012_deck_generations")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, name) VALUES ('p', 'P')"))
            connection.execute(text(
                "INSERT INTO workflow_runs (id, project_id, workflow_name, workflow_version, status, next_event_seq) "
                "VALUES ('r', 'p', 'deck_production', 1, 'succeeded', 1)"
            ))
            connection.execute(text(
                "INSERT INTO exports (id, project_id, run_id, format, status, manifest_fingerprint, manifest, forced) "
                "VALUES ('e', 'p', 'r', 'pptx', 'stale', 'fp', '{}', 0)"
            ))
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT status, error FROM exports WHERE id='e'" )).one()
    finally:
        engine.dispose()
    assert row.status == "failed"
    assert json.loads(row.error) == {"code": "pages_changed"}
