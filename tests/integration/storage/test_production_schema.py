from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def config_for(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_production_migration_adds_durable_tables(tmp_path: Path) -> None:
    path = tmp_path / "production.db"
    config = config_for(path)
    command.upgrade(config, "0009_source_materials")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "provider_binary_results",
        "provider_vision_results",
        "production_inputs",
        "page_generations",
        "quality_reports",
        "exports",
        "page_edits",
        "material_attempts",
        "provider_retry_schedules",
    } <= tables


def test_artifact_reference_rejects_cross_project_artifact(tmp_path: Path) -> None:
    path = tmp_path / "foreign-key.db"
    config = config_for(path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    project_a, project_b, artifact_id = str(uuid4()), str(uuid4()), str(uuid4())
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES (:a, 'A'), (:b, 'B')"),
                {"a": project_a, "b": project_b},
            )
            connection.execute(
                text(
                    "INSERT INTO artifacts "
                    "(id, project_id, logical_id, version, kind, status, media_type, "
                    "extension, staging_path, spec_fingerprint, artifact_metadata, pending_edges) "
                    "VALUES (:id, :project, 'logical', 1, 'page_image', 'available', "
                    "'image/png', '.png', 'staging/path', 'fingerprint', '{}', '[]')"
                ),
                {"id": artifact_id, "project": project_a},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("PRAGMA foreign_keys=ON"))
                connection.execute(
                    text(
                        "INSERT INTO artifact_references "
                        "(id, project_id, artifact_id, owner_type, owner_id, role) "
                        "VALUES (:id, :project, :artifact, 'run', 'run-1', 'page')"
                    ),
                    {
                        "id": str(uuid4()),
                        "project": project_b,
                        "artifact": artifact_id,
                    },
                )
    finally:
        engine.dispose()
