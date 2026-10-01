"""Secret indirection helpers."""

from pathlib import Path
import os

from ppt_agent.config.settings import SettingsError


def read_secret_file(path: Path, *, minimum_length: int = 1) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SettingsError(f"secret file cannot be read: {path}") from exc
    if len(value) < minimum_length:
        raise SettingsError(f"secret file is empty or too short: {path}")
    return value


class WorkspaceTokenStore:
    """Persist a generated local token inside the protected Workspace."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace / ".secrets" / "access-token"

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        return read_secret_file(self.path, minimum_length=43)

    def save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if self.load() != token:
                raise SettingsError("access token was concurrently initialized")
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
