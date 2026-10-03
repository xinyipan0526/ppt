import json
from pathlib import Path

import pytest

from ppt_agent.config.provider_secrets import FileProviderSecretStore
from ppt_agent.config.settings import Settings, SettingsError
from ppt_agent.runtime.provider_config import ProviderConfigurationService


MULTIMODAL = "doubao-seed-2-1-pro-260915"
IMAGE = "doubao-seedream-5-0-pro-260628"


def service(
    tmp_path: Path,
    *,
    idle: bool = True,
    activate=lambda _: None,
    qualify=lambda _: None,
) -> ProviderConfigurationService:
    return ProviderConfigurationService(
        Settings(workspace=tmp_path),
        FileProviderSecretStore(tmp_path / ".secrets" / "adapters"),
        is_idle=lambda: idle,
        activate=activate,
        qualify=qualify,
        settings_path=tmp_path / "config" / "provider-settings.json",
    )


def test_one_key_and_two_model_ids_qualify_cloud_and_persist_exact_ids(tmp_path: Path) -> None:
    configured = service(tmp_path)
    snapshot = configured.update(
        ark_api_key="ark-secret-value",
        multimodal_model_id=MULTIMODAL,
        image_model_id=IMAGE,
    )

    persisted = json.loads((tmp_path / "config" / "provider-settings.json").read_text())
    assert snapshot.mode == "cloud"
    assert snapshot.adapter_status["configured"] is True
    assert snapshot.multimodal_model.model_id == MULTIMODAL
    assert snapshot.image_model.model_id == IMAGE
    assert persisted == {
        "adapter_id": "volcengine_ark",
        "image_model_id": IMAGE,
        "multimodal_model_id": MULTIMODAL,
        "pricing_overrides": {},
        "schema_version": 2,
    }
    assert "ark-secret-value" not in json.dumps(snapshot.public_projection())


@pytest.mark.parametrize(
    ("multimodal", "image"),
    [(None, IMAGE), (MULTIMODAL, None)],
)
def test_incomplete_v2_configuration_remains_deterministic(
    tmp_path: Path, multimodal: str | None, image: str | None
) -> None:
    settings_path = tmp_path / "config" / "provider-settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "schema_version": 2,
        "adapter_id": "volcengine_ark",
        "multimodal_model_id": multimodal,
        "image_model_id": image,
        "pricing_overrides": {},
    }))
    secrets = FileProviderSecretStore(tmp_path / ".secrets" / "adapters")
    secrets.replace("volcengine_ark", "ark-secret-value")

    configured = ProviderConfigurationService(
        Settings(workspace=tmp_path),
        secrets,
        is_idle=lambda: True,
        activate=lambda _: None,
        settings_path=settings_path,
    )
    assert configured.snapshot().mode == "deterministic"


@pytest.mark.parametrize(
    ("multimodal", "image"),
    [
        (IMAGE, IMAGE),
        (MULTIMODAL, MULTIMODAL),
        ("deepseek-v4-1-flash-260910", IMAGE),
        ("Doubao Seed 2.1 Pro", IMAGE),
    ],
)
def test_invalid_or_experimental_selection_does_not_replace_secret(
    tmp_path: Path, multimodal: str, image: str
) -> None:
    configured = service(tmp_path)
    configured.secrets.replace("volcengine_ark", "previous-secret")

    with pytest.raises((SettingsError, ValueError, KeyError)):
        configured.update(
            ark_api_key="candidate-secret",
            multimodal_model_id=multimodal,
            image_model_id=image,
        )

    assert configured.secrets.load("volcengine_ark") == "previous-secret"
    assert configured.snapshot().mode == "deterministic"


def test_failed_qualification_rolls_back_secret_and_active_snapshot(tmp_path: Path) -> None:
    configured = service(tmp_path)
    active = configured.update(
        ark_api_key="previous-secret",
        multimodal_model_id=MULTIMODAL,
        image_model_id=IMAGE,
    )
    failing = ProviderConfigurationService(
        configured.settings,
        configured.secrets,
        is_idle=lambda: True,
        activate=lambda _: None,
        qualify=lambda _: (_ for _ in ()).throw(SettingsError("qualification failed")),
        settings_path=tmp_path / "config" / "provider-settings.json",
    )

    with pytest.raises(SettingsError, match="qualification failed"):
        failing.update(
            ark_api_key="candidate-secret",
            multimodal_model_id="doubao-seed-2-1-lite-260915",
            image_model_id=IMAGE,
        )

    assert failing.secrets.load("volcengine_ark") == "previous-secret"
    assert failing.snapshot().multimodal_model.model_id == active.multimodal_model.model_id


def test_busy_runtime_saves_pending_configuration(tmp_path: Path) -> None:
    applied: list[Settings] = []
    configured = service(tmp_path, idle=False, activate=applied.append)
    snapshot = configured.update(
        ark_api_key="ark-secret-value",
        multimodal_model_id=MULTIMODAL,
        image_model_id=IMAGE,
    )

    assert snapshot.activation == "pending"
    assert applied == []


def test_legacy_configuration_and_secrets_are_preserved_but_do_not_qualify(tmp_path: Path) -> None:
    path = tmp_path / "config" / "provider-settings.json"
    path.parent.mkdir(parents=True)
    legacy = {"deepseek_model": "deepseek-chat", "seedream_model": "old-seedream"}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    legacy_secret = tmp_path / ".secrets" / "providers" / "deepseek.key"
    legacy_secret.parent.mkdir(parents=True)
    legacy_secret.write_text("legacy-secret", encoding="utf-8")

    configured = service(tmp_path)

    assert configured.snapshot().mode == "deterministic"
    assert json.loads(path.read_text(encoding="utf-8")) == legacy
    assert legacy_secret.read_text(encoding="utf-8") == "legacy-secret"
