"""Localhost transport and application authentication boundary."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


class TokenStorageUnavailable(RuntimeError):
    pass


class LocalRequestRejected(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TokenStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, token: str) -> None: ...


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...


class KeyringTokenStore:
    """Store the long-lived local token in the operating-system keyring."""

    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        service_name: str = "ppt-agent",
        username: str = "local-access-token",
    ) -> None:
        if backend is None:
            try:
                import keyring as imported_backend  # type: ignore[import-not-found]
            except ImportError as exc:
                raise TokenStorageUnavailable("system keyring package is unavailable") from exc
            backend = imported_backend
        self._backend = backend
        self._service_name = service_name
        self._username = username

    def load(self) -> str | None:
        return self._backend.get_password(self._service_name, self._username)

    def save(self, token: str) -> None:
        self._backend.set_password(self._service_name, self._username, token)


class SecretFileTokenStore:
    """Read a pre-provisioned Docker/Kubernetes secret without rewriting it."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        try:
            token = self._path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TokenStorageUnavailable("local access secret cannot be read") from exc
        if len(token) < 43:
            raise TokenStorageUnavailable("local access secret must contain at least 43 characters")
        return token

    def save(self, token: str) -> None:
        raise TokenStorageUnavailable("secret file token store is read-only")


class AccessTokenManager:
    def __init__(self, store: TokenStore) -> None:
        self._store = store
        self._cached: str | None = None

    def load_or_create(self) -> str:
        if self._cached is not None:
            return self._cached
        token = self._store.load()
        if token is None:
            token = secrets.token_urlsafe(32)
            self._store.save(token)
        if len(token) < 43:
            raise TokenStorageUnavailable("stored local access token is too short")
        self._cached = token
        return token


@dataclass(frozen=True, slots=True)
class LocalHTTPConfig:
    allowed_authorities: frozenset[str] = frozenset(
        {"localhost:8000", "127.0.0.1:8000", "[::1]:8000"}
    )
    allowed_origins: frozenset[str] = frozenset(
        {
            "http://localhost:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
        }
    )

    def __post_init__(self) -> None:
        if not self.allowed_authorities:
            raise ValueError("allowed_authorities must not be empty")


@dataclass(frozen=True, slots=True)
class InputLimits:
    """Hard ceilings shared by future upload and parser adapters."""

    max_file_bytes: int = 50 * 1024 * 1024
    max_project_upload_bytes: int = 500 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_image_frames: int = 1
    max_pdf_pages: int = 200
    max_pdf_objects: int = 200_000
    max_markdown_characters: int = 2_000_000
    max_markdown_nesting: int = 32
    max_external_resources: int = 100
    max_compression_ratio: float = 100.0
    parser_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        values = (
            self.max_file_bytes,
            self.max_project_upload_bytes,
            self.max_image_pixels,
            self.max_image_frames,
            self.max_pdf_pages,
            self.max_pdf_objects,
            self.max_markdown_characters,
            self.max_markdown_nesting,
            self.max_external_resources,
            self.max_compression_ratio,
            self.parser_timeout_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("all input limits must be positive")
        if self.max_file_bytes > self.max_project_upload_bytes:
            raise ValueError("single-file limit cannot exceed project upload limit")


class LocalHTTPSecurity:
    """Validate localhost transport metadata and an explicit Bearer token."""

    def __init__(
        self,
        *,
        token: str | None = None,
        config: LocalHTTPConfig | None = None,
    ) -> None:
        if token is not None and len(token) < 32:
            raise ValueError("local access token must be at least 32 characters")
        self._token = token
        self.config = config or LocalHTTPConfig()

    @property
    def authentication_enabled(self) -> bool:
        return self._token is not None

    def validate_transport(self, request: Request) -> None:
        authority = request.headers.get("host", "").casefold()
        if not _valid_authority(authority) or authority not in {
            item.casefold() for item in self.config.allowed_authorities
        }:
            raise LocalRequestRejected(400, "request host is not allowed")
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            normalized = _normalize_origin(origin) if origin is not None else None
            allowed = {
                _normalize_origin(item) for item in self.config.allowed_origins
            }
            if normalized is None or normalized not in allowed:
                raise LocalRequestRejected(403, "request origin is not allowed")

    async def authenticate(self, request: Request) -> None:
        if self._token is None:
            raise HTTPException(status_code=503, detail="local authentication unavailable")
        authorization = request.headers.get("authorization", "")
        scheme, separator, candidate = authorization.partition(" ")
        if (
            separator != " "
            or scheme.casefold() != "bearer"
            or not candidate
            or not secrets.compare_digest(candidate, self._token)
        ):
            raise HTTPException(
                status_code=401,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )


def _valid_authority(authority: str) -> bool:
    if not authority or any(character.isspace() for character in authority):
        return False
    if any(character in authority for character in (",", "@", "/", "\\")):
        return False
    try:
        parsed = urlsplit(f"//{authority}")
        return parsed.hostname is not None and parsed.port is not None
    except ValueError:
        return False


def _normalize_origin(origin: str) -> str | None:
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        host = f"[{parsed.hostname.casefold()}]" if ":" in parsed.hostname else parsed.hostname.casefold()
        authority = host if parsed.port is None else f"{host}:{parsed.port}"
        return f"{parsed.scheme.casefold()}://{authority}"
    except ValueError:
        return None
