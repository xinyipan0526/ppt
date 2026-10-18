import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.storage.database import DatabaseConfig, create_sqlite_engine
from ppt_agent.storage.maintenance import (
    DatabaseIntegrityError,
    create_online_backup,
    restore_backup,
    run_migrations,
    verify_database_integrity,
)
from ppt_agent.storage.locking import WorkspaceInstanceLock, WorkspaceLockUnavailable
from ppt_agent.storage.workspace import WorkspacePaths


def test_run_migrations_preserves_existing_application_loggers(tmp_path: Path) -> None:
    logger = logging.getLogger("ppt_agent.operations.text_runner")
    previous_disabled = logger.disabled
    logger.disabled = False
    try:
        run_migrations(tmp_path / "logging-state.db")
        assert logger.disabled is False
    finally:
        logger.disabled = previous_disabled


def test_online_backup_captures_active_wal_and_restores_verified_copy() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    database = directory / "state.db"
    run_migrations(database)
    engine = create_sqlite_engine(DatabaseConfig(path=database))
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, name) VALUES ('p1', 'one')"))
        backup, manifest = create_online_backup(database, directory / "backups")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, name) VALUES ('p2', 'two')"))
    finally:
        engine.dispose()

    restored = directory / "restored.db"
    restore_backup(backup, restored, workspace=directory)
    restored_engine = create_sqlite_engine(DatabaseConfig(path=restored))
    try:
        verify_database_integrity(restored_engine)
        with restored_engine.connect() as connection:
            projects = connection.execute(text("SELECT id FROM projects ORDER BY id")).scalars().all()
    finally:
        restored_engine.dispose()

    assert manifest.exists()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["artifact_count"] == 0
    assert len(manifest_payload["artifact_inventory_sha256"]) == 64
    assert projects == ["p1"]


def test_online_backup_rejects_foreign_key_corruption_without_publishing_bundle() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    database = directory / "state.db"
    run_migrations(database)
    engine = create_sqlite_engine(DatabaseConfig(path=database))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('p1', 'one')")
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_runs "
                    "(id, project_id, workflow_name, workflow_version, status, next_event_seq) "
                    "VALUES ('run1', 'p1', 'test', 1, 'queued', 1)"
                )
            )
    finally:
        engine.dispose()

    # Simulate a damaged file received from an external SQLite writer.
    import sqlite3

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM projects WHERE id = 'p1'")
        connection.commit()
    finally:
        connection.close()

    backups = directory / "backups"
    with pytest.raises(DatabaseIntegrityError, match="foreign keys"):
        create_online_backup(database, backups)

    assert not tuple(backups.glob("backup-*.db"))
    assert not tuple(backups.glob("backup-*.manifest.json"))


def test_restore_rejects_tampered_backup_without_replacing_target() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    run_migrations(target)
    backup, _ = create_online_backup(source, directory / "backups")
    backup.write_bytes(backup.read_bytes() + b"tampered")

    with pytest.raises(DatabaseIntegrityError, match="checksum"):
        restore_backup(backup, target, workspace=directory)

    assert target.exists()


def test_restore_refuses_an_active_workspace() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    backup, _ = create_online_backup(source, directory / "backups")
    active_lock = WorkspaceInstanceLock(WorkspacePaths(directory))
    active_lock.acquire()
    try:
        with pytest.raises(WorkspaceLockUnavailable):
            restore_backup(backup, target, workspace=directory)
    finally:
        active_lock.release()


def test_restore_dry_run_validates_without_writing_workspace() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    run_migrations(target)
    source_engine = create_sqlite_engine(DatabaseConfig(path=source))
    target_engine = create_sqlite_engine(DatabaseConfig(path=target))
    try:
        with source_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('new', 'new')")
            )
        with target_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('old', 'old')")
            )
    finally:
        source_engine.dispose()
        target_engine.dispose()
    backup, _ = create_online_backup(source, directory / "backups")
    before = target.read_bytes()
    before_names = {path.name for path in directory.iterdir()}

    rollback = restore_backup(
        backup,
        target,
        workspace=directory,
        dry_run=True,
    )

    assert rollback is None
    assert target.read_bytes() == before
    assert {path.name for path in directory.iterdir()} == before_names


def test_restore_dry_run_rejects_corrupt_manifest_without_writing() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    run_migrations(target)
    backup, manifest = create_online_backup(source, directory / "backups")
    manifest.write_text("{}", encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(DatabaseIntegrityError, match="manifest"):
        restore_backup(
            backup,
            target,
            workspace=directory,
            dry_run=True,
        )

    assert target.read_bytes() == before


def test_restore_dry_run_does_not_create_a_missing_workspace() -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    run_migrations(source)
    backup, _ = create_online_backup(source, directory / "backups")
    missing_workspace = directory / "missing-workspace"

    assert restore_backup(
        backup,
        missing_workspace / "state.db",
        workspace=missing_workspace,
        dry_run=True,
    ) is None

    assert not missing_workspace.exists()


def test_failed_restore_before_artifact_reconciliation_rolls_back_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    run_migrations(target)
    source_engine = create_sqlite_engine(DatabaseConfig(path=source))
    target_engine = create_sqlite_engine(DatabaseConfig(path=target))
    try:
        with source_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('new', 'new')")
            )
        with target_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('old', 'old')")
            )
    finally:
        source_engine.dispose()
        target_engine.dispose()
    backup, _ = create_online_backup(source, directory / "backups")

    def fail_migration(_database: Path) -> None:
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(
        "ppt_agent.storage.maintenance.run_migrations", fail_migration
    )
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        restore_backup(backup, target, workspace=directory)

    restored_engine = create_sqlite_engine(DatabaseConfig(path=target))
    try:
        with restored_engine.connect() as connection:
            project_ids = connection.execute(
                text("SELECT id FROM projects ORDER BY id")
            ).scalars().all()
    finally:
        restored_engine.dispose()
    assert project_ids == ["old"]


def test_reconcile_failure_keeps_restored_database_as_filesystem_commit_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = Path(".cache/test-maintenance") / str(uuid4())
    directory.mkdir(parents=True)
    source = directory / "source.db"
    target = directory / "target.db"
    run_migrations(source)
    run_migrations(target)
    source_engine = create_sqlite_engine(DatabaseConfig(path=source))
    target_engine = create_sqlite_engine(DatabaseConfig(path=target))
    try:
        with source_engine.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, name) VALUES ('new', 'new')"))
        with target_engine.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, name) VALUES ('old', 'old')"))
    finally:
        source_engine.dispose()
        target_engine.dispose()
    backup, _ = create_online_backup(source, directory / "backups")

    def fail_after_filesystem_commit(_store: ArtifactStore) -> None:
        (directory / "artifacts-touched").write_text("restored", encoding="utf-8")
        raise OSError("simulated reconciliation failure")

    monkeypatch.setattr(ArtifactStore, "reconcile", fail_after_filesystem_commit)
    with pytest.raises(DatabaseIntegrityError, match="restored database retained"):
        restore_backup(backup, target, workspace=directory)

    restored_engine = create_sqlite_engine(DatabaseConfig(path=target))
    try:
        with restored_engine.connect() as connection:
            project_ids = connection.execute(text("SELECT id FROM projects")).scalars().all()
    finally:
        restored_engine.dispose()
    assert project_ids == ["new"]
    assert (directory / "artifacts-touched").read_text(encoding="utf-8") == "restored"
