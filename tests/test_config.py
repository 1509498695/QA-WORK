from __future__ import annotations

import pytest

from feishu_auth_service.binding import ProviderBinding
from feishu_auth_service.config import ConfigurationError, Settings


def test_settings_accept_minimal_local_configuration() -> None:
    settings = Settings(app_id="cli_test", app_secret="secret")

    assert settings.redirect_uri == "http://localhost:3000/callback"
    assert settings.scopes == (
        "auth:user.id:read",
        "offline_access",
        "docx:document:readonly",
        "wiki:node:read",
        "docs:document.media:download",
        "sheets:spreadsheet:readonly",
    )
    assert settings.allowed_tenant_key is None
    assert settings.authorization_url == "http://localhost:3000/oauth/start"
    assert settings.safe_status()["authorization_url"] == settings.authorization_url


def test_settings_loads_zero_input_local_binding() -> None:
    settings = Settings.from_binding(
        ProviderBinding(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
            local_client_ref="client_test",
            local_client_secret="client-secret",
            created_at="2026-08-20T00:00:00+00:00",
            updated_at="2026-08-20T00:00:00+00:00",
        )
    )

    assert settings.allowed_tenant_key == "tenant-a"
    assert settings.configuration_source == "local_binding"
    assert settings.local_client_ref == "client_test"
    assert settings.local_client_secret == "client-secret"
    assert settings.safe_status()["configuration_source"] == "local_binding"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("redirect_uri", "https://example.com/callback"),
        ("redirect_uri", "http://localhost:3000/wrong"),
        ("scopes", ()),
        ("host", "0.0.0.0"),
    ],
)
def test_settings_reject_out_of_scope_first_slice_configuration(
    field: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {"app_id": "cli_test", "app_secret": "secret"}
    kwargs[field] = value

    with pytest.raises(ConfigurationError):
        Settings(**kwargs)  # type: ignore[arg-type]
