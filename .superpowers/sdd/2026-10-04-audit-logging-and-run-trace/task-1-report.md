# Task 1 report

## Changed files

- `src/ppt_agent/observability/__init__.py`
- `src/ppt_agent/observability/logging.py`
- `src/ppt_agent/runtime/application.py`
- `tests/unit/observability/test_logging.py`
- `tests/integration/runtime/test_application_logging.py`

The logger writes one bounded, parseable JSON object per line to
`<workspace>/logs/ppt-agent.jsonl`, rotates through the standard-library
`RotatingFileHandler`, recursively redacts credential-like/raw-content keys,
sanitizes exception text, and detaches/closes cleanly during runtime shutdown.
Runtime setup occurs after Alembic migration configuration so migration
`fileConfig` cannot remove the handler; readiness semantics are unchanged.

## Verification

- `pytest tests/unit/observability/test_logging.py -q --basetemp=...` -> `2 passed`
- `pytest tests/integration/runtime/test_application.py tests/integration/runtime/test_application_logging.py -q --basetemp=...` -> `6 passed`
- `pytest tests/unit/observability/test_logging.py tests/integration/runtime/test_application.py tests/integration/runtime/test_application_logging.py -q --basetemp=...` -> `8 passed`
- `pytest tests/integration/runtime -q --basetemp=...` -> `22 passed, 1 warning`
- `python -m compileall -q src/ppt_agent/observability src/ppt_agent/runtime/application.py` -> passed

## Concerns

- Runtime defaults are currently fixed at 10 MiB per log file and 3 backups; the public logger API accepts explicit rotation settings, but runtime configuration does not yet expose those values.
- The existing FastAPI/Starlette `httpx` deprecation warning remains unrelated to this task.
