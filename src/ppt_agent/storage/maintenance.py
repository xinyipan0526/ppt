"""Database migration, integrity, online backup, and restore primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from ppt_agent import __version__
from ppt_agent.artifacts.store import ArtifactStore
from ppt_agent.storage.database import (
    DatabaseConfig,
    create_session_factory,
    create_sqlite_engine,
)
from ppt_agent.storage.locking import WorkspaceInstanceLock
from ppt_agent.storage.workspace import WorkspacePaths


class DatabaseIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupManifest:
    schema_revision: str
    application_version: str
    created_at: str
    database_sha256: str
    artifact_count: int
    artifact_bytes: int
    artifact_inventory_sha256: str


def run_migrations(database_path: Path) -> None:
    repository_root = _repository_root()
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    )
    command.upgrade(config, "head")


def verify_database_integrity(engine: Engine) -> None:
    with engine.connect() as connection:
        quick = connection.execute(text("PRAGMA quick_check")).scalars().all()
        foreign_keys = connection.execute(text("PRAGMA foreign_key_check")).all()
    if quick != ["ok"] or foreign_keys:
        raise DatabaseIntegrityError("SQLite integrity verification failed")


def create_online_backup(
    database_path: Path,
    backup_directory: Path,
) -> tuple[Path, Path]:
    backup_directory.mkdir(parents=True, exist_ok=True)
    identity = f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    final_database = backup_directory / f"{identity}.db"
    temporary = backup_directory / f".{identity}.tmp"
    source = sqlite3.connect(database_path)
    destination = sqlite3.connect(temporary)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchall()
        if result != [("ok",)]:
            raise DatabaseIntegrityError("backup integrity verification failed")
        revision_row = destination.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        revision = str(revision_row[0]) if revision_row else "unversioned"
        artifact_rows = destination.execute(
            "SELECT id, content_hash, relative_path, size_bytes "
            "FROM artifacts ORDER BY id"
        ).fetchall()
        artifacts = [tuple(row) for row in artifact_rows]
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()
    try:
        os.replace(temporary, final_database)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = BackupManifest(
        schema_revision=revision,
        application_version=__version__,
        created_at=datetime.now(UTC).isoformat(),
        database_sha256=_sha256(final_database),
        artifact_count=len(artifacts),
        artifact_bytes=sum(int(row[3] or 0) for row in artifacts),
        artifact_inventory_sha256=hashlib.sha256(
            json.dumps(artifacts, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )
    manifest_path = final_database.with_suffix(".manifest.json")
    manifest_temp = manifest_path.with_name(f".{manifest_path.name}.tmp")
    try:
        manifest_temp.write_text(
            json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(manifest_temp, manifest_path)
    except BaseException:
        manifest_temp.unlink(missing_ok=True)
        final_database.unlink(missing_ok=True)
        raise
    return final_database, manifest_path


def restore_backup(
    backup_path: Path,
    target_path: Path,
    *,
    workspace: Path,
) -> Path | None:
    """Restore an offline Workspace and reconcile it before releasing its lock."""

    paths = WorkspacePaths(workspace, create=True)
    target_path = paths.verify(target_path, must_exist=False)
    workspace_lock = WorkspaceInstanceLock(paths)
    workspace_lock.acquire()
    try:
        return _restore_locked(backup_path, target_path, paths)
    finally:
        workspace_lock.release()


def _restore_locked(
    backup_path: Path,
    target_path: Path,
    paths: WorkspacePaths,
) -> Path | None:
    manifest_path = backup_path.with_suffix(".manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatabaseIntegrityError("backup manifest is missing or invalid") from exc
    if manifest.get("database_sha256") != _sha256(backup_path):
        raise DatabaseIntegrityError("backup checksum mismatch")
    _validate_manifest(manifest)
    sidecars = tuple(
        Path(f"{target_path}{suffix}") for suffix in ("-wal", "-shm", "-journal")
    )
    if any(path.exists() for path in sidecars):
        raise DatabaseIntegrityError(
            "database sidecars exist; checkpoint and stop the runtime before restore"
        )

    prepared = target_path.with_name(f".{target_path.name}.restore-{uuid4().hex}.tmp")
    source = sqlite3.connect(backup_path)
    destination = sqlite3.connect(prepared)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise DatabaseIntegrityError("restored database integrity verification failed")
        if destination.execute("PRAGMA foreign_key_check").fetchall():
            raise DatabaseIntegrityError("restored database foreign keys are invalid")
        count, size, inventory_hash = _artifact_inventory(destination)
        if (
            count != manifest["artifact_count"]
            or size != manifest["artifact_bytes"]
            or inventory_hash != manifest["artifact_inventory_sha256"]
        ):
            raise DatabaseIntegrityError("backup artifact inventory mismatch")
    except BaseException:
        prepared.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()

    rollback = None
    if target_path.exists():
        rollback = target_path.with_name(
            f"{target_path.name}.pre-restore-{uuid4().hex}"
        )
        os.replace(target_path, rollback)
    try:
        os.replace(prepared, target_path)
    except BaseException:
        if rollback is not None and rollback.exists():
            os.replace(rollback, target_path)
        raise
    finally:
        prepared.unlink(missing_ok=True)
    engine: Engine | None = None
    try:
        run_migrations(target_path)
        engine = create_sqlite_engine(
            DatabaseConfig(path=target_path, journal_mode="DELETE")
        )
        verify_database_integrity(engine)
    except BaseException:
        if engine is not None:
            engine.dispose()
            engine = None
        failed = target_path.with_name(
            f"{target_path.name}.failed-restore-{uuid4().hex}"
        )
        if target_path.exists():
            os.replace(target_path, failed)
        if rollback is not None and rollback.exists():
            os.replace(rollback, target_path)
        raise
    assert engine is not None
    sessions = create_session_factory(engine)
    try:
        ArtifactStore(sessions, paths.root).reconcile()
    except BaseException as exc:
        # Reconciliation is the filesystem commit point. Never put the old
        # database back after Artifact files may have moved; retaining the
        # restored DB keeps metadata aligned and allows startup to retry.
        raise DatabaseIntegrityError(
            "artifact reconciliation incomplete; restored database retained"
        ) from exc
    finally:
        engine.dispose()
    return rollback


def retain_recent_backups(
    backup_directory: Path,
    trash_directory: Path,
    *,
    keep: int,
) -> tuple[Path, ...]:
    if keep < 1:
        raise ValueError("keep must be positive")
    backups = sorted(
        backup_directory.glob("backup-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    trash_directory.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for database in backups[keep:]:
        bundle = trash_directory / f"{database.stem}-{uuid4().hex}"
        bundle.mkdir()
        manifest = database.with_suffix(".manifest.json")
        moved_manifest: Path | None = None
        if manifest.exists():
            moved_manifest = bundle / manifest.name
            os.replace(manifest, moved_manifest)
        destination = bundle / database.name
        try:
            os.replace(database, destination)
        except BaseException:
            if moved_manifest is not None and moved_manifest.exists():
                os.replace(moved_manifest, manifest)
            raise
        moved.append(destination)
    return tuple(moved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise DatabaseIntegrityError("backup manifest is missing or invalid")
    required = {
        "schema_revision": str,
        "application_version": str,
        "database_sha256": str,
        "artifact_count": int,
        "artifact_bytes": int,
        "artifact_inventory_sha256": str,
    }
    if any(not isinstance(manifest.get(key), kind) for key, kind in required.items()):
        raise DatabaseIntegrityError("backup manifest fields are invalid")
    if manifest["application_version"].split(".", 1)[0] != __version__.split(".", 1)[0]:
        raise DatabaseIntegrityError("backup application version is incompatible")
    repository_root = _repository_root()
    revision = manifest["schema_revision"]
    revision_files = (repository_root / "migrations" / "versions").glob("*.py")
    if not any(path.stem == revision for path in revision_files):
        raise DatabaseIntegrityError("backup schema revision is unknown")


def _artifact_inventory(connection: sqlite3.Connection) -> tuple[int, int, str]:
    rows = [
        tuple(row)
        for row in connection.execute(
            "SELECT id, content_hash, relative_path, size_bytes "
            "FROM artifacts ORDER BY id"
        ).fetchall()
    ]
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return len(rows), sum(int(row[3] or 0) for row in rows), digest


def _repository_root() -> Path:
    """Locate migration resources in both source and wheel layouts.

    In a source checkout ``maintenance.py`` is nested under ``src/ppt_agent``;
    an installed wheel places it directly under ``site-packages/ppt_agent``.
    The former needs ``parents[3]`` while the latter needs ``parents[2]``.
    Choose the first candidate that contains the Alembic configuration and
    version directory so startup never depends on the interpreter's ``Lib``
    directory layout.
    """

    module_path = Path(__file__).resolve()
    for index in (3, 2):
        candidate = module_path.parents[index]
        if (candidate / "alembic.ini").is_file() and (
            candidate / "migrations" / "versions"
        ).is_dir():
            return candidate
    raise FileNotFoundError(
        "PPT Agent migration resources are missing; reinstall the package "
        "from a complete source distribution"
    )
