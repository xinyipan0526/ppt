import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import ppt_agent.cli as cli
from ppt_agent.cli import build_parser, main
from ppt_agent.storage.locking import WorkspaceInstanceLock
from ppt_agent.storage.workspace import WorkspacePaths


def test_doctor_and_migrate_work_without_provider_keys(tmp_path: Path, capsys) -> None:
    config = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    config.write_text(f'workspace="{workspace.as_posix()}"\n[providers]\nmode="deterministic"\n', encoding="utf-8")
    assert main(["--config", str(config), "doctor", "--skip-browser-check"]) == 0
    assert "deterministic" in capsys.readouterr().out
    assert main(["--config", str(config), "migrate"]) == 0
    assert (workspace / "state.db").exists()


def test_serve_supports_browser_opt_out() -> None:
    args = build_parser().parse_args(["serve", "--no-open"])
    assert args.command == "serve"
    assert args.no_open is True


def test_debug_flag_is_available_after_a_subcommand() -> None:
    args = build_parser().parse_args(["doctor", "--debug"])
    assert args.debug is True


def test_backup_and_restore_arguments_default_to_safe_validation() -> None:
    backup = build_parser().parse_args(["backup"])
    restore = build_parser().parse_args(["restore", "snapshot.db"])
    confirmed = build_parser().parse_args(
        ["restore", "snapshot.db", "--confirm"]
    )

    assert backup.command == "backup"
    assert backup.output_directory is None
    assert restore.command == "restore"
    assert restore.dry_run is True
    assert restore.confirm is False
    assert confirmed.dry_run is False
    assert confirmed.confirm is True

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["restore", "snapshot.db", "--dry-run", "--confirm"]
        )


def test_cli_backup_and_restore_require_confirmation_before_replacement(
    tmp_path: Path, capsys
) -> None:
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "migrate"]) == 0
    from ppt_agent.storage.database import DatabaseConfig, create_sqlite_engine
    from sqlalchemy import text

    engine = create_sqlite_engine(DatabaseConfig(path=workspace / "state.db"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('backup', 'backup')")
            )
    finally:
        engine.dispose()

    assert main(["--workspace", str(workspace), "backup"]) == 0
    backup = next((workspace / "backups").glob("backup-*.db"))

    engine = create_sqlite_engine(DatabaseConfig(path=workspace / "state.db"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name) VALUES ('later', 'later')")
            )
    finally:
        engine.dispose()

    assert main(
        ["--workspace", str(workspace), "restore", str(backup)]
    ) == 0
    engine = create_sqlite_engine(DatabaseConfig(path=workspace / "state.db"))
    try:
        with engine.connect() as connection:
            dry_run_ids = connection.execute(
                text("SELECT id FROM projects ORDER BY id")
            ).scalars().all()
    finally:
        engine.dispose()
    assert dry_run_ids == ["backup", "later"]

    assert main(
        [
            "--workspace",
            str(workspace),
            "restore",
            str(backup),
            "--confirm",
        ]
    ) == 0
    engine = create_sqlite_engine(DatabaseConfig(path=workspace / "state.db"))
    try:
        with engine.connect() as connection:
            confirmed_ids = connection.execute(
                text("SELECT id FROM projects ORDER BY id")
            ).scalars().all()
    finally:
        engine.dispose()
    assert confirmed_ids == ["backup"]
    assert "validation" in capsys.readouterr().out.casefold()


def test_cli_backup_refuses_a_running_workspace_and_releases_lock(
    tmp_path: Path, capsys
) -> None:
    workspace = tmp_path / "workspace"
    assert main(["--workspace", str(workspace), "migrate"]) == 0
    active_lock = WorkspaceInstanceLock(WorkspacePaths(workspace))
    active_lock.acquire()
    try:
        assert main(["--workspace", str(workspace), "backup"]) == 2
        assert "command failed" in capsys.readouterr().err
        assert not (workspace / "backups").exists()
    finally:
        active_lock.release()

    assert main(["--workspace", str(workspace), "backup"]) == 0


def test_container_defaults_to_production_with_secret_free_readiness_probe() -> None:
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")

    assert "PPT_AGENT_PRODUCTION=true" in dockerfile
    assert "/api/v1/ready" in dockerfile
    assert "PPT_AGENT_PRODUCTION: \"true\"" in compose
    assert "/api/v1/ready" in compose
    probe_text = "\n".join(
        line for line in (dockerfile + "\n" + compose).splitlines()
        if "/api/v1/ready" in line
    ).casefold()
    assert "authorization" not in probe_text
    assert "bearer" not in probe_text
    assert "csrf" not in probe_text


def test_reload_uses_import_factory_without_building_parent_application(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.toml"
    workspace = tmp_path / "workspace"
    config.write_text(
        f'workspace="{workspace.as_posix()}"\n[providers]\nmode="deterministic"\n',
        encoding="utf-8",
    )
    invocation: dict[str, object] = {}

    def fake_run(application: object, **kwargs: object) -> None:
        invocation["application"] = application
        invocation.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(
        cli,
        "build_application",
        lambda settings: (_ for _ in ()).throw(
            AssertionError("reload parent must not build the application")
        ),
    )

    assert main(["--config", str(config), "serve", "--reload", "--no-open"]) == 0
    assert invocation["application"] == "ppt_agent.cli:create_reload_application"
    assert invocation["factory"] is True
    assert invocation["reload"] is True
    assert invocation["host"] == "127.0.0.1"
    assert invocation["port"] == 8000


def test_reload_factory_rebuilds_complete_application_from_cli_settings(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.toml"
    configured_workspace = tmp_path / "configured-workspace"
    overridden_workspace = tmp_path / "overridden-workspace"
    config.write_text(
        f'workspace="{configured_workspace.as_posix()}"\n'
        '[server]\nport=8010\n'
        '[providers]\nmode="deterministic"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "PPT_AGENT_RELOAD_CONFIG",
        json.dumps(
            {
                "config": str(config),
                "overrides": {
                    "workspace": str(overridden_workspace),
                    "port": 8123,
                },
            }
        ),
    )

    app = cli.create_reload_application()

    assert app.state.runtime is not None
    assert app.state.provider_configuration is not None
    assert app.state.settings["workspace"] == str(overridden_workspace)
    assert app.state.settings["server"]["port"] == 8123


def test_cli_hides_unexpected_exception_details_unless_debug(monkeypatch, capsys) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("provider payload ark-secret C:/private/workspace")

    monkeypatch.setattr(cli, "load_settings", fail)

    assert main(["doctor"]) == 2
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    assert "ark-secret" not in output.err
    assert "--debug" in output.err

    with pytest.raises(RuntimeError, match="ark-secret"):
        main(["--debug", "doctor"])


def test_cli_unknown_failure_is_recorded_in_workspace_jsonl_without_raw_stderr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "workspace"
    secret = "ark-secret-from-restore"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"restore payload api_key={secret} C:/private/workspace")

    monkeypatch.setattr(cli, "restore_backup", fail)

    assert main(["--workspace", str(workspace), "restore", "missing.db"]) == 2
    output = capsys.readouterr()
    assert output.err == "error: command failed; rerun with --debug for details\n"
    log_text = (workspace / "logs" / "ppt-agent.jsonl").read_text(encoding="utf-8")
    assert "Traceback" in log_text
    assert secret not in log_text
    assert "C:/private/workspace" not in log_text

    # A second invocation must not write through a closed capture/console
    # stream left by the first command.
    assert main(["--workspace", str(workspace), "restore", "missing.db"]) == 2
    second = capsys.readouterr()
    assert second.err == "error: command failed; rerun with --debug for details\n"
    assert "Logging error" not in second.err


def test_real_migration_failure_preserves_workspace_log_and_sanitizes_stderr(
    tmp_path: Path, capsys
) -> None:
    workspace = tmp_path / "private-workspace"
    database_path = workspace / "state.db"
    database_path.mkdir(parents=True)
    marker = "ark-secret-migration-marker"
    (database_path / marker).write_text("not a database", encoding="utf-8")

    assert main(["--workspace", str(workspace), "migrate"]) == 2

    output = capsys.readouterr()
    assert output.err == "error: command failed; rerun with --debug for details\n"
    log_path = workspace / "logs" / "ppt-agent.jsonl"
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert '"message":"cli command failed"' in log_text
    assert "OperationalError" in log_text
    assert marker not in log_text
    assert str(workspace) not in log_text
