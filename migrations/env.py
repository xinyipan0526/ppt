"""Alembic environment for the local SQLite database."""

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ppt_agent.storage.models import Base


config = context.config
root_logger = logging.getLogger()
workspace_file_logging = any(
    getattr(handler, "_ppt_agent_file_log", False)
    for handler in root_logger.handlers
)
if config.config_file_name is not None and not workspace_file_logging:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
elif workspace_file_logging:
    # ``fileConfig`` globally clears/closes existing handlers before installing
    # Alembic's generic stderr handler.  The native CLI already owns a redacting
    # Workspace JSONL handler, so retain that owner and configure only levels.
    alembic_logger = logging.getLogger("alembic")
    alembic_logger.disabled = False
    alembic_logger.setLevel(logging.INFO)
    sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
    sqlalchemy_logger.disabled = False
    sqlalchemy_logger.setLevel(logging.WARNING)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
