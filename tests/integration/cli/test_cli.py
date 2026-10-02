from pathlib import Path

from ppt_agent.cli import build_parser, main


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
