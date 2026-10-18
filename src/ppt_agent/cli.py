"""Native command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Mapping, Sequence
import webbrowser
from uuid import uuid4

from ppt_agent.config.settings import SettingsError, load_settings
from ppt_agent.runtime.composition import build_application
from ppt_agent.observability.logging import configure_file_logging, shutdown_file_logging
from ppt_agent.storage.maintenance import (
    create_online_backup,
    restore_backup,
    run_migrations,
)
from ppt_agent.storage.locking import WorkspaceInstanceLock
from ppt_agent.storage.workspace import WorkspacePaths


_RELOAD_CONFIG_ENV = "PPT_AGENT_RELOAD_CONFIG"


class _RestoreModeAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del parser, values, option_string
        confirmed = bool(self.const)
        setattr(namespace, "confirm", confirmed)
        setattr(namespace, "dry_run", not confirmed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppt-agent")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--debug", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--no-open", action="store_true")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
    doctor.add_argument("--skip-browser-check", action="store_true")
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
    backup = commands.add_parser("backup")
    backup.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
    backup.add_argument("--output-directory", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--debug", action="store_true", default=argparse.SUPPRESS)
    restore.add_argument("backup_path", type=Path)
    restore.set_defaults(dry_run=True, confirm=False)
    restore_mode = restore.add_mutually_exclusive_group()
    restore_mode.add_argument(
        "--dry-run",
        nargs=0,
        action=_RestoreModeAction,
        const=False,
    )
    restore_mode.add_argument(
        "--confirm",
        nargs=0,
        action=_RestoreModeAction,
        const=True,
    )
    return parser


def create_reload_application():
    """Rebuild the complete composition root inside Uvicorn's reload child."""
    raw = os.environ.get(_RELOAD_CONFIG_ENV)
    if not raw:
        raise SettingsError("reload configuration is missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettingsError("reload configuration is invalid") from exc
    if not isinstance(payload, dict):
        raise SettingsError("reload configuration is invalid")
    config_value = payload.get("config")
    override_values = payload.get("overrides", {})
    if config_value is not None and not isinstance(config_value, str):
        raise SettingsError("reload configuration is invalid")
    if not isinstance(override_values, dict):
        raise SettingsError("reload configuration is invalid")
    overrides: dict[str, Any] = dict(override_values)
    if "workspace" in overrides:
        overrides["workspace"] = Path(overrides["workspace"])
    settings = load_settings(
        Path(config_value) if config_value is not None else None,
        overrides=overrides,
    )
    return build_application(settings)


def _reload_configuration(
    config_path: Path | None, overrides: Mapping[str, Any]
) -> str:
    config = str(config_path.resolve()) if config_path is not None else None
    serialized_overrides = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in overrides.items()
        if value is not None
    }
    return json.dumps({"config": config, "overrides": serialized_overrides})


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    file_log_handler: logging.Handler | None = None
    cli_logger = logging.getLogger("ppt_agent.cli")
    correlation_id = uuid4().hex
    try:
        settings = load_settings(args.config, overrides={
            "workspace": args.workspace, "host": args.host, "port": args.port,
        })
        settings.workspace.mkdir(parents=True, exist_ok=True)
        file_log_handler = configure_file_logging(
            settings.workspace, max_bytes=10 * 1024 * 1024, backup_count=3
        )
        cli_logger.disabled = False
        cli_logger.info(
            "cli command started",
            extra={"command": args.command, "correlation_id": correlation_id},
        )
        if args.command == "migrate":
            settings.workspace.mkdir(parents=True, exist_ok=True)
            run_migrations(settings.workspace / "state.db")
            print("Database migrations complete.")
            return 0
        if args.command == "backup":
            database_path = settings.workspace / "state.db"
            output_directory = args.output_directory or settings.workspace / "backups"
            paths = WorkspacePaths(settings.workspace, create=True)
            with WorkspaceInstanceLock(paths):
                backup_path, manifest_path = create_online_backup(
                    database_path,
                    output_directory,
                )
            print(f"Backup complete: {backup_path}")
            print(f"Manifest: {manifest_path}")
            return 0
        if args.command == "restore":
            target_path = settings.workspace / "state.db"
            restore_backup(
                args.backup_path,
                target_path,
                workspace=settings.workspace,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print("Restore validation complete; no Workspace files were changed.")
            else:
                print("Restore complete.")
            return 0
        if args.command == "doctor":
            report = settings.redacted()
            from ppt_agent.rendering.fonts import FontRegistry
            fonts = FontRegistry(settings.font_roots).scan()
            report["fonts"] = {
                "status": "ok", "chinese_count": len(fonts.chinese),
                "fingerprint": fonts.fingerprint,
            }
            if not args.skip_browser_check:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=str(settings.chromium_executable) if settings.chromium_executable else None,
                        headless=True,
                    )
                    browser.close()
                report["chromium"] = "ok"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        import uvicorn
        url = f"http://localhost:{settings.port}/"
        print(f"PPT Agent is available at {url}")
        if not args.no_open:
            timer = threading.Timer(1.0, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        if args.reload:
            previous_reload_config = os.environ.get(_RELOAD_CONFIG_ENV)
            os.environ[_RELOAD_CONFIG_ENV] = _reload_configuration(
                args.config,
                {
                    "workspace": args.workspace,
                    "host": args.host,
                    "port": args.port,
                },
            )
            try:
                uvicorn.run(
                    "ppt_agent.cli:create_reload_application",
                    factory=True,
                    host=settings.host,
                    port=settings.port,
                    reload=True,
                    reload_dirs=[str(Path(__file__).resolve().parent)],
                )
            finally:
                if previous_reload_config is None:
                    os.environ.pop(_RELOAD_CONFIG_ENV, None)
                else:
                    os.environ[_RELOAD_CONFIG_ENV] = previous_reload_config
        else:
            app = build_application(settings)
            uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
        return 0
    except Exception:
        if file_log_handler is not None:
            cli_logger.exception(
                "cli command failed",
                extra={"command": args.command, "correlation_id": correlation_id},
            )
        if args.debug:
            raise
        print("error: command failed; rerun with --debug for details", file=sys.stderr)
        return 2
    finally:
        if file_log_handler is not None:
            shutdown_file_logging(file_log_handler)


if __name__ == "__main__":
    raise SystemExit(main())
