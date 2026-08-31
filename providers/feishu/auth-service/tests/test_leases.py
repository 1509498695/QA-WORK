from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_auth_service.config import Settings
from feishu_auth_service.leases import LocalLeaseBroker
from feishu_protocol import (
    DOCX_MEDIA_READ_CAPABILITY,
    DOCX_READ_CAPABILITY,
    SHEETS_MEDIA_READ_CAPABILITY,
    SHEETS_TYPED_VALUES_WRITE_CAPABILITY,
    SHEETS_READ_CAPABILITY,
    WIKI_CHILD_LIST_CAPABILITY,
    WIKI_NODE_CREATE_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_auth_service.models import TokenGrant
from feishu_auth_service.profiles import LocalProfileVault


PROFILE_REF = "profile_0123456789abcdef0123"


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


class FakeRefresher:
    def __init__(
        self,
        scopes: tuple[str, ...] = ("offline_access", "docx:document:readonly"),
    ) -> None:
        self.calls = 0
        self.scopes = scopes

    async def refresh_access_token(self, refresh_token: str) -> TokenGrant:
        assert refresh_token in {"initial-refresh", "rotated-refresh"}
        self.calls += 1
        return TokenGrant(
            access_token="leased-access-token-must-not-be-logged",
            expires_in=3600,
            scopes=self.scopes,
            token_type="Bearer",
            refresh_token="rotated-refresh",
            refresh_token_expires_in=None,
        )


def _vault(tmp_path: Path, *, scopes: tuple[str, ...]) -> LocalProfileVault:
    vault = LocalProfileVault(tmp_path / "profiles", FakeProtector())
    vault.save_authorization(
        profile_ref=PROFILE_REF,
        app_id="cli_test",
        tenant_key="tenant-a",
        open_id="ou_test",
        union_id=None,
        refresh_token="initial-refresh",
        refresh_token_expires_in=None,
        scopes=scopes,
    )
    return vault


def test_lease_broker_refreshes_rotates_and_reuses_access_token(tmp_path: Path) -> None:
    vault = _vault(
        tmp_path,
        scopes=("offline_access", "docx:document:readonly"),
    )
    refresher = FakeRefresher()
    now = [100.0]
    wall = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=vault,
        token_refresher=refresher,
        clock=lambda: now[0],
        wall_clock=lambda: wall,
    )

    first = asyncio.run(
        broker.issue(
            task_ref="task-one",
            profile_ref=PROFILE_REF,
            capabilities=(DOCX_READ_CAPABILITY,),
        )
    )
    second = asyncio.run(
        broker.issue(
            task_ref="task-two",
            profile_ref=PROFILE_REF,
            capabilities=(DOCX_READ_CAPABILITY,),
        )
    )

    assert first.task_ref == "task-one"
    assert second.task_ref == "task-two"
    assert first.lease_ref != second.lease_ref
    assert first.expires_at == "2026-08-24T00:10:00+00:00"
    assert first.token_expires_at == "2026-08-24T01:00:00+00:00"
    assert "leased-access-token-must-not-be-logged" not in repr(first)
    assert refresher.calls == 1
    assert vault.load(PROFILE_REF).refresh_token == "rotated-refresh"


def test_lease_broker_requires_capability_scope(tmp_path: Path) -> None:
    vault = _vault(tmp_path, scopes=("offline_access",))
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=vault,
        token_refresher=FakeRefresher(),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-one",
                profile_ref=PROFILE_REF,
                capabilities=(DOCX_READ_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED


def test_lease_broker_requires_wiki_scope_for_wiki_node_capability(
    tmp_path: Path,
) -> None:
    vault = _vault(
        tmp_path,
        scopes=("offline_access", "docx:document:readonly"),
    )
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=vault,
        token_refresher=FakeRefresher(),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-one",
                profile_ref=PROFILE_REF,
                capabilities=(DOCX_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {"capabilities": [WIKI_NODE_READ_CAPABILITY]}


def test_lease_broker_issues_one_lease_for_docx_and_wiki_scopes(
    tmp_path: Path,
) -> None:
    scopes = (
        "offline_access",
        "docx:document:readonly",
        "wiki:node:read",
    )
    vault = _vault(tmp_path, scopes=scopes)
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=vault,
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-one",
            profile_ref=PROFILE_REF,
            capabilities=(DOCX_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY),
        )
    )

    assert lease.capabilities == (
        DOCX_READ_CAPABILITY,
        WIKI_NODE_READ_CAPABILITY,
    )
    assert "wiki:node:read" in lease.scopes


def test_lease_broker_requires_dedicated_wiki_create_scope(tmp_path: Path) -> None:
    scopes = ("offline_access", "wiki:node:read")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-wiki-create",
                profile_ref=PROFILE_REF,
                capabilities=(WIKI_NODE_CREATE_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "capabilities": [WIKI_NODE_CREATE_CAPABILITY]
    }


def test_lease_broker_requires_dedicated_wiki_child_list_scope(
    tmp_path: Path,
) -> None:
    scopes = ("offline_access", "wiki:node:read", "wiki:node:create")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-wiki-list",
                profile_ref=PROFILE_REF,
                capabilities=(WIKI_CHILD_LIST_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "capabilities": [WIKI_CHILD_LIST_CAPABILITY]
    }


def test_lease_broker_issues_wiki_create_capability(tmp_path: Path) -> None:
    scopes = ("offline_access", "wiki:node:create")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-wiki-create",
            profile_ref=PROFILE_REF,
            capabilities=(WIKI_NODE_CREATE_CAPABILITY,),
        )
    )

    assert lease.capabilities == (WIKI_NODE_CREATE_CAPABILITY,)
    assert "wiki:node:create" in lease.scopes


def test_lease_broker_issues_wiki_child_list_capability(tmp_path: Path) -> None:
    scopes = ("offline_access", "wiki:node:retrieve")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-wiki-list",
            profile_ref=PROFILE_REF,
            capabilities=(WIKI_CHILD_LIST_CAPABILITY,),
        )
    )

    assert lease.capabilities == (WIKI_CHILD_LIST_CAPABILITY,)
    assert "wiki:node:retrieve" in lease.scopes


def test_lease_broker_requires_media_scope_for_docx_asset_capability(
    tmp_path: Path,
) -> None:
    scopes = ("offline_access", "docx:document:readonly")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-one",
                profile_ref=PROFILE_REF,
                capabilities=(DOCX_MEDIA_READ_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "capabilities": [DOCX_MEDIA_READ_CAPABILITY]
    }


def test_lease_broker_issues_media_lease_with_minimum_scope(
    tmp_path: Path,
) -> None:
    scopes = (
        "offline_access",
        "docs:document.media:download",
    )
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-one",
            profile_ref=PROFILE_REF,
            capabilities=(DOCX_MEDIA_READ_CAPABILITY,),
        )
    )

    assert lease.capabilities == (DOCX_MEDIA_READ_CAPABILITY,)
    assert "docs:document.media:download" in lease.scopes


def test_lease_broker_requires_media_scope_for_sheets_image_capability(
    tmp_path: Path,
) -> None:
    scopes = ("offline_access", "sheets:spreadsheet:readonly")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-sheet-image",
                profile_ref=PROFILE_REF,
                capabilities=(SHEETS_MEDIA_READ_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "capabilities": [SHEETS_MEDIA_READ_CAPABILITY]
    }


def test_lease_broker_issues_sheets_media_lease_with_minimum_scope(
    tmp_path: Path,
) -> None:
    scopes = ("offline_access", "docs:document.media:download")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-sheet-image",
            profile_ref=PROFILE_REF,
            capabilities=(SHEETS_MEDIA_READ_CAPABILITY,),
        )
    )

    assert lease.capabilities == (SHEETS_MEDIA_READ_CAPABILITY,)
    assert "docs:document.media:download" in lease.scopes


def test_lease_broker_requires_sheets_scope_for_sheets_capability(
    tmp_path: Path,
) -> None:
    scopes = ("offline_access", "wiki:node:read")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-one",
                profile_ref=PROFILE_REF,
                capabilities=(SHEETS_READ_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {"capabilities": [SHEETS_READ_CAPABILITY]}


def test_lease_broker_issues_sheets_and_wiki_lease_with_readonly_scope(
    tmp_path: Path,
) -> None:
    scopes = (
        "offline_access",
        "wiki:node:read",
        "sheets:spreadsheet:readonly",
    )
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-one",
            profile_ref=PROFILE_REF,
            capabilities=(SHEETS_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY),
        )
    )

    assert lease.capabilities == (
        SHEETS_READ_CAPABILITY,
        WIKI_NODE_READ_CAPABILITY,
    )
    assert "sheets:spreadsheet:readonly" in lease.scopes


def test_lease_broker_requires_typed_values_write_scope(tmp_path: Path) -> None:
    scopes = ("offline_access", "sheets:spreadsheet")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            broker.issue(
                task_ref="task-typed-write",
                profile_ref=PROFILE_REF,
                capabilities=(SHEETS_TYPED_VALUES_WRITE_CAPABILITY,),
            )
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    assert error.value.details == {
        "capabilities": [SHEETS_TYPED_VALUES_WRITE_CAPABILITY]
    }


def test_lease_broker_issues_typed_values_write_scope(tmp_path: Path) -> None:
    scopes = ("offline_access", "sheets:spreadsheet:write_only")
    broker = LocalLeaseBroker(
        settings=Settings(
            app_id="cli_test",
            app_secret="secret",
            allowed_tenant_key="tenant-a",
        ),
        profile_vault=_vault(tmp_path, scopes=scopes),
        token_refresher=FakeRefresher(scopes),
    )

    lease = asyncio.run(
        broker.issue(
            task_ref="task-typed-write",
            profile_ref=PROFILE_REF,
            capabilities=(SHEETS_TYPED_VALUES_WRITE_CAPABILITY,),
        )
    )

    assert lease.capabilities == (SHEETS_TYPED_VALUES_WRITE_CAPABILITY,)
    assert "sheets:spreadsheet:write_only" in lease.scopes
