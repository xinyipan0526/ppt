"""Native command-line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ppt_agent.config.settings import SettingsError, load_settings
from ppt_agent.runtime.composition import build_application
from ppt_agent.storage.maintenance import run_migrations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppt-agent")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--reload", action="store_true")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--skip-browser-check", action="store_true")
    commands.add_parser("migrate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config, overrides={
            "workspace": args.workspace, "host": args.host, "port": args.port,
        })
        if args.command == "migrate":
            settings.workspace.mkdir(parents=True, exist_ok=True)
            run_migrations(settings.workspace / "state.db")
            print("Database migrations complete.")
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
        app = build_application(settings)
        print(f"Local access token: {app.state.access_token}")
        uvicorn.run(app, host=settings.host, port=settings.port, reload=args.reload)
        return 0
    except (SettingsError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
