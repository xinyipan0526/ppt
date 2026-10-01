"""Provider credential persistence without database or API disclosure."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4


PROVIDERS = frozenset({"deepseek", "seedream", "qwen"})


class ProviderSecretError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderSecretStatus:
    configured: bool
    backend: str
    masked_suffix: str | None = None


class FileProviderSecretStore:
    """Atomically store provider keys below a protected Workspace directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, provider: str) -> str | None:
        path = self._path(provider)
        self._reject_link(path)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ProviderSecretError("provider secret cannot be read") from exc
        if not value:
            raise ProviderSecretError("provider secret is empty")
        return value

    def status(self, provider: str) -> ProviderSecretStatus:
        value = self.load(provider)
        return ProviderSecretStatus(
            configured=value is not None,
            backend="workspace_secret_file",
            masked_suffix=value[-4:] if value is not None else None,
        )

    def replace(self, provider: str, value: str) -> None:
        path = self._path(provider)
        normalized = value.strip()
        if len(normalized) < 8 or len(normalized.encode("utf-8")) > 64 * 1024:
            raise ProviderSecretError("provider secret is too short or too large")
        self.root.mkdir(parents=True, exist_ok=True)
        self._reject_link(path)
        temporary = self.root / f".{provider}.{uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                stream.write(normalized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._fsync_directory()
        except OSError as exc:
            raise ProviderSecretError("provider secret cannot be stored") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def delete(self, provider: str) -> bool:
        path = self._path(provider)
        self._reject_link(path)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ProviderSecretError("provider secret cannot be deleted") from exc
        self._fsync_directory()
        return True

    def _path(self, provider: str) -> Path:
        if provider not in PROVIDERS:
            raise ProviderSecretError("unsupported provider secret")
        return self.root / f"{provider}.key"

    @staticmethod
    def _reject_link(path: Path) -> None:
        if path.is_symlink():
            raise ProviderSecretError("provider secret path cannot be a symbolic link")

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
