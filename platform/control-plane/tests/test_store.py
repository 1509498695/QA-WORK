from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from qa_skill_hub_control_plane.models import VerifiedIdentity
from qa_skill_hub_control_plane.store import ControlPlaneStateError, ControlPlaneStore


def _identity(name: str, open_id: str) -> VerifiedIdentity:
    return VerifiedIdentity(
        app_id="cli_test",
        tenant_ref="tenant-a",
        open_id=open_id,
        union_id=f"union-{open_id}",
        verified_name=name,
        avatar_url=None,
        profile_ref=f"profile-{open_id}",
    )


def _bootstrap_user(store: ControlPlaneStore, identity: VerifiedIdentity) -> str:
    token = store.issue_bootstrap(ttl_seconds=900)
    assert token is not None
    state, _, _ = store.begin_login(
        intent="bootstrap",
        ttl_seconds=300,
        bootstrap_token=token,
    )
    user = store.complete_identity(identity, store.consume_login(state))
    return user.user_ref


def _login_user(store: ControlPlaneStore, identity: VerifiedIdentity) -> str:
    state, _, _ = store.begin_login(intent="account", ttl_seconds=300)
    return store.complete_identity(identity, store.consume_login(state)).user_ref


def test_identity_is_stable_and_persists_without_oauth_tokens(tmp_path: Path) -> None:
    store = ControlPlaneStore(tmp_path / "state.sqlite3")
    first = _bootstrap_user(store, _identity("测试用户", "ou_one"))
    second = _login_user(store, _identity("测试用户新姓名", "ou_one"))

    assert first == second
    session_token, _ = store.create_session(first, ttl_seconds=3600)
    viewer = store.viewer(session_token)
    assert viewer is not None
    assert viewer.user.verified_name == "测试用户新姓名"
    assert viewer.user.display_name == "测试用户"
    database = (tmp_path / "state.sqlite3").read_bytes()
    assert b"access_token" not in database
    assert b"refresh_token" not in database


def test_one_membership_and_last_team_admin_invariants_are_transactional(
    tmp_path: Path,
) -> None:
    store = ControlPlaneStore(tmp_path / "state.sqlite3")
    first_ref = _bootstrap_user(store, _identity("甲", "ou_one"))
    first_team = store.create_team(actor_user_ref=first_ref, name="团队一")
    invite_token, _ = store.create_invite(
        actor_user_ref=first_ref,
        expires_in_seconds=604_800,
        max_uses=5,
    )
    second_ref = _login_user(store, _identity("乙", "ou_two"))
    store.join_team_by_invite(actor_user_ref=second_ref, token=invite_token)

    store.set_team_role(
        actor_user_ref=first_ref,
        target_user_ref=second_ref,
        role="team_admin",
    )
    store.set_team_role(
        actor_user_ref=first_ref,
        target_user_ref=first_ref,
        role="member",
    )
    assert len(store.list_team_members(first_team.team_ref)) == 2

    with pytest.raises(ControlPlaneStateError, match="至少一名团队管理员"):
        store.set_team_role(
            actor_user_ref=second_ref,
            target_user_ref=second_ref,
            role="member",
        )


def test_platform_admins_are_independent_and_can_never_reach_zero(tmp_path: Path) -> None:
    store = ControlPlaneStore(tmp_path / "state.sqlite3")
    first_ref = _bootstrap_user(store, _identity("甲", "ou_one"))
    second_ref = _login_user(store, _identity("乙", "ou_two"))

    store.grant_platform_admin(actor_user_ref=first_ref, target_user_ref=second_ref)
    store.revoke_platform_admin(actor_user_ref=first_ref, target_user_ref=first_ref)
    admins = store.list_platform_admins()
    assert [admin.user.user_ref for admin in admins] == [second_ref]

    with pytest.raises(ControlPlaneStateError, match="至少一名平台管理员"):
        store.revoke_platform_admin(actor_user_ref=second_ref, target_user_ref=second_ref)


def test_expired_bootstrap_and_login_state_fail_closed(tmp_path: Path) -> None:
    now = [datetime(2026, 9, 1, tzinfo=UTC)]
    store = ControlPlaneStore(tmp_path / "state.sqlite3", clock=lambda: now[0])
    token = store.issue_bootstrap(ttl_seconds=60)
    assert token is not None
    now[0] = datetime(2026, 9, 1, 0, 2, tzinfo=UTC)

    with pytest.raises(ControlPlaneStateError, match="无效或已过期"):
        store.begin_login(
            intent="bootstrap",
            ttl_seconds=300,
            bootstrap_token=token,
        )
