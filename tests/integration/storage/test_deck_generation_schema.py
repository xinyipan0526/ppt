from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_deck_generation_migration_adds_immutable_snapshot_table(tmp_path: Path) -> None:
    path = tmp_path / "deck-generations.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inspector = inspect(engine)
        columns = {item["name"] for item in inspector.get_columns("deck_generations")}
        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("deck_generations")
        }
    finally:
        engine.dispose()

    assert {
        "id", "project_id", "planning_run_id", "production_run_id", "generation",
        "status", "source_generation_id", "instruction", "page_generation_ids",
    } <= columns
    assert ("planning_run_id", "generation") in uniques
