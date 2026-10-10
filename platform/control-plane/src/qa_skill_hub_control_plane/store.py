from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qa_skill_hub_control_plane.models import (
    InviteRecord,
    LoginRecord,
    MembershipRecord,
    PlatformAdminRecord,
    SessionRecord,
    TeamMemberRecord,
    TeamRecord,
    UserRecord,
    VerifiedIdentity,
    ViewerState,
)


SCHEMA_VERSION = 1


class ControlPlaneStateError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ControlPlaneStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path.resolve(strict=False)
        self._clock = clock or (lambda: datetime.now(UTC))

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, SCHEMA_VERSION}:
                raise ControlPlaneStateError(
                    "schema_reset_required",
                    "开发状态版本不兼容，请使用显式开发重置后重新 Bootstrap。",
                )
            if version == 0:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def has_platform_admins(self) -> bool:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM platform_admins").fetchone()
            return int(row[0]) > 0

    def issue_bootstrap(self, *, ttl_seconds: int) -> str | None:
        self.initialize()
        token = secrets.token_urlsafe(32)
        now = self._now()
        with self._transaction() as connection:
            if self._platform_admin_count(connection) > 0:
                return None
            connection.execute("DELETE FROM bootstrap_tokens")
            connection.execute(
                "INSERT INTO bootstrap_tokens(token_digest, expires_at, created_at) VALUES (?, ?, ?)",
                (_digest(token), _iso(now + timedelta(seconds=ttl_seconds)), _iso(now)),
            )
        return token

    def bootstrap_is_valid(self, token: str) -> bool:
        self.initialize()
        with self._connect() as connection:
            return self._bootstrap_valid(connection, _digest(token), self._now())

    def begin_login(
        self,
        *,
        intent: str,
        ttl_seconds: int,
        invite_token: str | None = None,
        bootstrap_token: str | None = None,
    ) -> tuple[str, str, str]:
        if intent not in {"account", "create", "join", "bootstrap"}:
            raise ControlPlaneStateError("login_intent_invalid", "登录目标无效。")
        self.initialize()
        now = self._now()
        bootstrap_digest = _digest(bootstrap_token) if bootstrap_token else None
        with self._transaction() as connection:
            if self._platform_admin_count(connection) == 0 and intent != "bootstrap":
                raise ControlPlaneStateError(
                    "platform_bootstrap_required",
                    "当前中央服务尚未完成首次平台管理员 Bootstrap。",
                )
            if intent == "bootstrap":
                if bootstrap_digest is None or not self._bootstrap_valid(
                    connection,
                    bootstrap_digest,
                    now,
                ):
                    raise ControlPlaneStateError(
                        "bootstrap_invalid",
                        "Bootstrap 链接无效或已过期。",
                    )
            if invite_token is not None:
                self._active_invite(connection, invite_token, now)
            state = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            challenge = _pkce_challenge(verifier)
            connection.execute(
                """
                INSERT INTO login_transactions(
                    state_digest, code_verifier, intent, invite_token,
                    bootstrap_digest, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _digest(state),
                    verifier,
                    intent,
                    invite_token,
                    bootstrap_digest,
                    _iso(now + timedelta(seconds=ttl_seconds)),
                    _iso(now),
                ),
            )
            connection.execute(
                "DELETE FROM login_transactions WHERE expires_at <= ?",
                (_iso(now),),
            )
        return state, verifier, challenge

    def consume_login(self, state: str | None) -> LoginRecord:
        if not state:
            raise ControlPlaneStateError("login_state_invalid", "登录状态无效。")
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM login_transactions WHERE state_digest = ?",
                (_digest(state),),
            ).fetchone()
            if row is None:
                raise ControlPlaneStateError(
                    "login_state_invalid",
                    "登录状态无效、已使用或已过期。",
                )
            connection.execute(
                "DELETE FROM login_transactions WHERE state_digest = ?",
                (_digest(state),),
            )
            if _parse_time(row["expires_at"]) <= now:
                raise ControlPlaneStateError("login_state_expired", "登录状态已过期。")
            return LoginRecord(
                code_verifier=row["code_verifier"],
                intent=row["intent"],
                invite_token=row["invite_token"],
                bootstrap_digest=row["bootstrap_digest"],
                expires_at=row["expires_at"],
            )

    def complete_identity(
        self,
        identity: VerifiedIdentity,
        login: LoginRecord,
    ) -> UserRecord:
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            user = self._upsert_user(connection, identity, now)
            if login.intent == "bootstrap":
                if login.bootstrap_digest is None or not self._bootstrap_valid(
                    connection,
                    login.bootstrap_digest,
                    now,
                ):
                    raise ControlPlaneStateError(
                        "bootstrap_invalid",
                        "Bootstrap 链接已过期或已被使用。",
                    )
                if self._platform_admin_count(connection) > 0:
                    raise ControlPlaneStateError(
                        "bootstrap_already_completed",
                        "当前中央服务已经完成 Bootstrap。",
                    )
                connection.execute(
                    "INSERT INTO platform_admins(user_ref, granted_by_user_ref, granted_at) VALUES (?, ?, ?)",
                    (user.user_ref, user.user_ref, _iso(now)),
                )
                connection.execute("DELETE FROM bootstrap_tokens")
                self._audit(
                    connection,
                    actor=user.user_ref,
                    action="platform.bootstrap.completed",
                    target=user.user_ref,
                )
            elif self._platform_admin_count(connection) == 0:
                raise ControlPlaneStateError(
                    "platform_bootstrap_required",
                    "当前中央服务尚未完成 Bootstrap。",
                )
            return user

    def create_session(self, user_ref: str, *, ttl_seconds: int) -> tuple[str, SessionRecord]:
        self.initialize()
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = self._now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._transaction() as connection:
            self._require_user(connection, user_ref)
            connection.execute(
                """
                INSERT INTO sessions(
                    token_digest, user_ref, csrf_token, expires_at, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _digest(token),
                    user_ref,
                    csrf_token,
                    _iso(expires_at),
                    _iso(now),
                    _iso(now),
                ),
            )
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))
        return token, SessionRecord(user_ref, csrf_token, _iso(expires_at))

    def viewer(self, session_token: str | None) -> ViewerState | None:
        if not session_token:
            return None
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_digest = ?",
                (_digest(session_token),),
            ).fetchone()
            if row is None:
                return None
            if _parse_time(row["expires_at"]) <= now:
                connection.execute(
                    "DELETE FROM sessions WHERE token_digest = ?",
                    (_digest(session_token),),
                )
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_digest = ?",
                (_iso(now), _digest(session_token)),
            )
            user = self._user(connection, row["user_ref"])
            membership_row = connection.execute(
                "SELECT * FROM memberships WHERE user_ref = ?",
                (user.user_ref,),
            ).fetchone()
            membership = _membership(membership_row) if membership_row is not None else None
            team = (
                self._team(connection, membership.team_ref)
                if membership is not None
                else None
            )
            platform_admin = connection.execute(
                "SELECT 1 FROM platform_admins WHERE user_ref = ?",
                (user.user_ref,),
            ).fetchone()
            return ViewerState(
                user=user,
                membership=membership,
                team=team,
                is_platform_admin=platform_admin is not None,
                csrf_token=row["csrf_token"],
            )

    def delete_session(self, session_token: str | None) -> None:
        if not session_token:
            return
        self.initialize()
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_digest = ?",
                (_digest(session_token),),
            )

    def update_display_name(self, *, actor_user_ref: str, display_name: str) -> None:
        normalized = _human_text("显示名称", display_name, maximum=80)
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            self._require_user(connection, actor_user_ref)
            connection.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE user_ref = ?",
                (normalized, _iso(now), actor_user_ref),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="identity.display_name.updated",
                target=actor_user_ref,
            )

    def create_team(self, *, actor_user_ref: str, name: str) -> TeamRecord:
        normalized_name = _human_text("团队名称", name, maximum=80)
        self.initialize()
        now = self._now()
        team_ref = f"team_{secrets.token_hex(6)}"
        with self._transaction() as connection:
            user = self._require_user(connection, actor_user_ref)
            if connection.execute(
                "SELECT 1 FROM memberships WHERE user_ref = ?",
                (actor_user_ref,),
            ).fetchone() is not None:
                raise ControlPlaneStateError(
                    "membership_exists",
                    "你已经属于一个团队；请先完成退出或团队切换。",
                )
            try:
                connection.execute(
                    """
                    INSERT INTO teams(team_ref, tenant_ref, name, normalized_name, created_by_user_ref, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        team_ref,
                        user.tenant_ref,
                        normalized_name,
                        normalized_name.casefold(),
                        actor_user_ref,
                        _iso(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ControlPlaneStateError(
                    "team_name_conflict",
                    "当前中央服务中已经存在同名团队。",
                ) from exc
            connection.execute(
                "INSERT INTO memberships(user_ref, team_ref, role, team_remark, joined_at) VALUES (?, ?, 'team_admin', NULL, ?)",
                (actor_user_ref, team_ref, _iso(now)),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="team.created",
                target=team_ref,
                team_ref=team_ref,
            )
            return self._team(connection, team_ref)

    def invitation_target(self, token: str) -> TeamRecord:
        self.initialize()
        with self._connect() as connection:
            row = self._active_invite(connection, token, self._now())
            return self._team(connection, row["team_ref"])

    def join_team_by_invite(self, *, actor_user_ref: str, token: str) -> TeamRecord:
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            user = self._require_user(connection, actor_user_ref)
            invite = self._active_invite(connection, token, now)
            team = self._team(connection, invite["team_ref"])
            if user.tenant_ref != team.tenant_ref:
                raise ControlPlaneStateError(
                    "invite_tenant_mismatch",
                    "该邀请不属于你的飞书租户。",
                )
            existing_row = connection.execute(
                "SELECT * FROM memberships WHERE user_ref = ?",
                (actor_user_ref,),
            ).fetchone()
            if existing_row is not None:
                existing = _membership(existing_row)
                if existing.team_ref == team.team_ref:
                    return team
                if existing.is_team_admin and self._team_admin_count(
                    connection,
                    existing.team_ref,
                ) <= 1:
                    raise ControlPlaneStateError(
                        "last_team_admin",
                        "你是旧团队最后一名管理员，必须先交接管理员后才能切换团队。",
                    )
                connection.execute(
                    "DELETE FROM memberships WHERE user_ref = ?",
                    (actor_user_ref,),
                )
            connection.execute(
                "INSERT INTO memberships(user_ref, team_ref, role, team_remark, joined_at) VALUES (?, ?, 'member', NULL, ?)",
                (actor_user_ref, team.team_ref, _iso(now)),
            )
            connection.execute(
                "UPDATE invites SET used_count = used_count + 1 WHERE invite_ref = ?",
                (invite["invite_ref"],),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="team.joined",
                target=team.team_ref,
                team_ref=team.team_ref,
            )
            return team

    def list_team_members(self, team_ref: str) -> tuple[TeamMemberRecord, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.*, u.*
                FROM memberships AS m
                JOIN users AS u ON u.user_ref = m.user_ref
                WHERE m.team_ref = ?
                ORDER BY CASE m.role WHEN 'team_admin' THEN 0 ELSE 1 END, u.display_name
                """,
                (team_ref,),
            ).fetchall()
            return tuple(
                TeamMemberRecord(user=_user(row), membership=_membership(row))
                for row in rows
            )

    def list_invites(self, team_ref: str) -> tuple[InviteRecord, ...]:
        self.initialize()
        now = self._now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*, u.display_name AS issuer_display_name
                FROM invites AS i
                JOIN users AS u ON u.user_ref = i.issued_by_user_ref
                WHERE i.team_ref = ?
                  AND i.revoked_at IS NULL
                  AND i.expires_at > ?
                  AND i.used_count < i.max_uses
                ORDER BY i.created_at DESC
                """,
                (team_ref, _iso(now)),
            ).fetchall()
            return tuple(_invite(row) for row in rows)

    def create_invite(
        self,
        *,
        actor_user_ref: str,
        expires_in_seconds: int,
        max_uses: int,
    ) -> tuple[str, InviteRecord]:
        if expires_in_seconds not in {86_400, 604_800, 2_592_000}:
            raise ControlPlaneStateError("invite_expiry_invalid", "邀请有效期无效。")
        if max_uses not in {1, 5, 10}:
            raise ControlPlaneStateError("invite_usage_invalid", "邀请使用次数无效。")
        self.initialize()
        token = secrets.token_urlsafe(32)
        invite_ref = f"invite_{secrets.token_hex(6)}"
        now = self._now()
        with self._transaction() as connection:
            membership = self._require_team_admin(connection, actor_user_ref)
            expires_at = now + timedelta(seconds=expires_in_seconds)
            connection.execute(
                """
                INSERT INTO invites(
                    invite_ref, team_ref, token_digest, expires_at, max_uses,
                    used_count, revoked_at, issued_by_user_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (
                    invite_ref,
                    membership.team_ref,
                    _digest(token),
                    _iso(expires_at),
                    max_uses,
                    actor_user_ref,
                    _iso(now),
                ),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="team.invite.created",
                target=invite_ref,
                team_ref=membership.team_ref,
            )
            return token, InviteRecord(
                invite_ref=invite_ref,
                team_ref=membership.team_ref,
                expires_at=_iso(expires_at),
                max_uses=max_uses,
                used_count=0,
                revoked_at=None,
                issuer_display_name=self._user(connection, actor_user_ref).display_name,
            )

    def revoke_invite(self, *, actor_user_ref: str, invite_ref: str) -> None:
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            membership = self._require_team_admin(connection, actor_user_ref)
            result = connection.execute(
                """
                UPDATE invites SET revoked_at = ?
                WHERE invite_ref = ? AND team_ref = ? AND revoked_at IS NULL
                """,
                (_iso(now), invite_ref, membership.team_ref),
            )
            if result.rowcount != 1:
                raise ControlPlaneStateError("invite_not_found", "邀请不存在或已经撤销。")
            self._audit(
                connection,
                actor=actor_user_ref,
                action="team.invite.revoked",
                target=invite_ref,
                team_ref=membership.team_ref,
            )

    def set_team_role(
        self,
        *,
        actor_user_ref: str,
        target_user_ref: str,
        role: str,
    ) -> None:
        if role not in {"member", "team_admin"}:
            raise ControlPlaneStateError("team_role_invalid", "团队角色无效。")
        self.initialize()
        with self._transaction() as connection:
            actor_membership = self._require_team_admin(connection, actor_user_ref)
            target_row = connection.execute(
                "SELECT * FROM memberships WHERE user_ref = ? AND team_ref = ?",
                (target_user_ref, actor_membership.team_ref),
            ).fetchone()
            if target_row is None:
                raise ControlPlaneStateError("member_not_found", "团队成员不存在。")
            target = _membership(target_row)
            if (
                target.role == "team_admin"
                and role == "member"
                and self._team_admin_count(connection, target.team_ref) <= 1
            ):
                raise ControlPlaneStateError(
                    "last_team_admin",
                    "团队必须始终保留至少一名团队管理员。",
                )
            connection.execute(
                "UPDATE memberships SET role = ? WHERE user_ref = ?",
                (role, target_user_ref),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="team.role.updated",
                target=target_user_ref,
                team_ref=target.team_ref,
                details={"role": role},
            )

    def list_platform_admins(self) -> tuple[PlatformAdminRecord, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT u.*, p.granted_at
                FROM platform_admins AS p
                JOIN users AS u ON u.user_ref = p.user_ref
                ORDER BY p.granted_at, u.display_name
                """
            ).fetchall()
            return tuple(
                PlatformAdminRecord(user=_user(row), granted_at=row["granted_at"])
                for row in rows
            )

    def grant_platform_admin(self, *, actor_user_ref: str, target_user_ref: str) -> None:
        self.initialize()
        now = self._now()
        with self._transaction() as connection:
            self._require_platform_admin(connection, actor_user_ref)
            self._require_user(connection, target_user_ref)
            connection.execute(
                "INSERT OR IGNORE INTO platform_admins(user_ref, granted_by_user_ref, granted_at) VALUES (?, ?, ?)",
                (target_user_ref, actor_user_ref, _iso(now)),
            )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="platform.admin.granted",
                target=target_user_ref,
            )

    def revoke_platform_admin(self, *, actor_user_ref: str, target_user_ref: str) -> None:
        self.initialize()
        with self._transaction() as connection:
            self._require_platform_admin(connection, actor_user_ref)
            if self._platform_admin_count(connection) <= 1:
                raise ControlPlaneStateError(
                    "last_platform_admin",
                    "平台必须始终保留至少一名平台管理员。",
                )
            result = connection.execute(
                "DELETE FROM platform_admins WHERE user_ref = ?",
                (target_user_ref,),
            )
            if result.rowcount != 1:
                raise ControlPlaneStateError(
                    "platform_admin_not_found",
                    "平台管理员不存在。",
                )
            self._audit(
                connection,
                actor=actor_user_ref,
                action="platform.admin.revoked",
                target=target_user_ref,
            )

    def counts(self) -> dict[str, int]:
        self.initialize()
        with self._connect() as connection:
            return {
                "users": int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
                "teams": int(connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0]),
                "platform_admins": self._platform_admin_count(connection),
            }

    def reset_and_issue_bootstrap(self, *, ttl_seconds: int) -> str:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            candidate.unlink(missing_ok=True)
        self.initialize()
        token = self.issue_bootstrap(ttl_seconds=ttl_seconds)
        assert token is not None
        return token

    def _upsert_user(
        self,
        connection: sqlite3.Connection,
        identity: VerifiedIdentity,
        now: datetime,
    ) -> UserRecord:
        row = connection.execute(
            "SELECT * FROM users WHERE tenant_ref = ? AND app_id = ? AND open_id = ?",
            (identity.tenant_ref, identity.app_id, identity.open_id),
        ).fetchone()
        if row is None:
            user_ref = _stable_user_ref(identity)
            display_name = identity.verified_name or "飞书用户"
            connection.execute(
                """
                INSERT INTO users(
                    user_ref, tenant_ref, app_id, open_id, union_id, verified_name,
                    display_name, avatar_url, profile_ref, last_verified_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_ref,
                    identity.tenant_ref,
                    identity.app_id,
                    identity.open_id,
                    identity.union_id,
                    identity.verified_name,
                    display_name,
                    identity.avatar_url,
                    identity.profile_ref,
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
            self._audit(
                connection,
                actor=user_ref,
                action="identity.provisioned",
                target=user_ref,
            )
        else:
            user_ref = row["user_ref"]
            connection.execute(
                """
                UPDATE users
                SET union_id = ?, verified_name = ?, avatar_url = ?, profile_ref = ?,
                    last_verified_at = ?, updated_at = ?
                WHERE user_ref = ?
                """,
                (
                    identity.union_id,
                    identity.verified_name,
                    identity.avatar_url,
                    identity.profile_ref,
                    _iso(now),
                    _iso(now),
                    user_ref,
                ),
            )
        return self._user(connection, user_ref)

    def _active_invite(
        self,
        connection: sqlite3.Connection,
        token: str,
        now: datetime,
    ) -> sqlite3.Row:
        if len(token) < 32:
            raise ControlPlaneStateError("invite_invalid", "邀请链接无效。")
        row = connection.execute(
            "SELECT * FROM invites WHERE token_digest = ?",
            (_digest(token),),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"] is not None
            or _parse_time(row["expires_at"]) <= now
            or int(row["used_count"]) >= int(row["max_uses"])
        ):
            raise ControlPlaneStateError(
                "invite_invalid",
                "邀请链接无效、已过期、已撤销或使用次数已耗尽。",
            )
        return row

    def _bootstrap_valid(
        self,
        connection: sqlite3.Connection,
        token_digest: str,
        now: datetime,
    ) -> bool:
        row = connection.execute(
            "SELECT expires_at FROM bootstrap_tokens WHERE token_digest = ?",
            (token_digest,),
        ).fetchone()
        return row is not None and _parse_time(row["expires_at"]) > now

    def _require_user(self, connection: sqlite3.Connection, user_ref: str) -> UserRecord:
        row = connection.execute(
            "SELECT * FROM users WHERE user_ref = ?",
            (user_ref,),
        ).fetchone()
        if row is None:
            raise ControlPlaneStateError("user_not_found", "用户不存在。")
        return _user(row)

    def _user(self, connection: sqlite3.Connection, user_ref: str) -> UserRecord:
        return self._require_user(connection, user_ref)

    def _team(self, connection: sqlite3.Connection, team_ref: str) -> TeamRecord:
        row = connection.execute(
            "SELECT * FROM teams WHERE team_ref = ?",
            (team_ref,),
        ).fetchone()
        if row is None:
            raise ControlPlaneStateError("team_not_found", "团队不存在。")
        return _team(row)

    def _require_team_admin(
        self,
        connection: sqlite3.Connection,
        user_ref: str,
    ) -> MembershipRecord:
        row = connection.execute(
            "SELECT * FROM memberships WHERE user_ref = ? AND role = 'team_admin'",
            (user_ref,),
        ).fetchone()
        if row is None:
            raise ControlPlaneStateError("team_admin_required", "需要团队管理员权限。")
        return _membership(row)

    def _require_platform_admin(self, connection: sqlite3.Connection, user_ref: str) -> None:
        if connection.execute(
            "SELECT 1 FROM platform_admins WHERE user_ref = ?",
            (user_ref,),
        ).fetchone() is None:
            raise ControlPlaneStateError("platform_admin_required", "需要平台管理员权限。")

    @staticmethod
    def _platform_admin_count(connection: sqlite3.Connection) -> int:
        return int(connection.execute("SELECT COUNT(*) FROM platform_admins").fetchone()[0])

    @staticmethod
    def _team_admin_count(connection: sqlite3.Connection, team_ref: str) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM memberships WHERE team_ref = ? AND role = 'team_admin'",
                (team_ref,),
            ).fetchone()[0]
        )

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        target: str,
        team_ref: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                event_ref, actor_user_ref, action, target_ref, team_ref, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"audit_{secrets.token_hex(8)}",
                actor,
                action,
                target,
                team_ref,
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                _iso(self._now()),
            ),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
        finally:
            connection.close()


def _stable_user_ref(identity: VerifiedIdentity) -> str:
    source = f"{identity.tenant_ref}\x1f{identity.app_id}\x1f{identity.open_id}".encode()
    return f"user_{hashlib.sha256(source).hexdigest()[:12]}"


def _digest(value: str | None) -> str:
    if value is None:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _human_text(label: str, value: str, *, maximum: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ControlPlaneStateError("input_required", f"{label}不能为空。")
    if len(normalized) > maximum or any(ord(character) < 32 for character in normalized):
        raise ControlPlaneStateError("input_invalid", f"{label}包含不支持的字符或过长。")
    return normalized


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        user_ref=row["user_ref"],
        tenant_ref=row["tenant_ref"],
        app_id=row["app_id"],
        open_id=row["open_id"],
        union_id=row["union_id"],
        verified_name=row["verified_name"],
        display_name=row["display_name"],
        avatar_url=row["avatar_url"],
        profile_ref=row["profile_ref"],
        last_verified_at=row["last_verified_at"],
        created_at=row["created_at"],
    )


def _team(row: sqlite3.Row) -> TeamRecord:
    return TeamRecord(
        team_ref=row["team_ref"],
        tenant_ref=row["tenant_ref"],
        name=row["name"],
        created_at=row["created_at"],
    )


def _membership(row: sqlite3.Row) -> MembershipRecord:
    return MembershipRecord(
        user_ref=row["user_ref"],
        team_ref=row["team_ref"],
        role=row["role"],
        team_remark=row["team_remark"],
        joined_at=row["joined_at"],
    )


def _invite(row: sqlite3.Row) -> InviteRecord:
    return InviteRecord(
        invite_ref=row["invite_ref"],
        team_ref=row["team_ref"],
        expires_at=row["expires_at"],
        max_uses=int(row["max_uses"]),
        used_count=int(row["used_count"]),
        revoked_at=row["revoked_at"],
        issuer_display_name=row["issuer_display_name"],
    )


_SCHEMA = """
CREATE TABLE users (
    user_ref TEXT PRIMARY KEY,
    tenant_ref TEXT NOT NULL,
    app_id TEXT NOT NULL,
    open_id TEXT NOT NULL,
    union_id TEXT,
    verified_name TEXT,
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    profile_ref TEXT NOT NULL,
    last_verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_ref, app_id, open_id)
);

CREATE TABLE teams (
    team_ref TEXT PRIMARY KEY,
    tenant_ref TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_by_user_ref TEXT NOT NULL REFERENCES users(user_ref),
    created_at TEXT NOT NULL
);

CREATE TABLE memberships (
    user_ref TEXT PRIMARY KEY REFERENCES users(user_ref) ON DELETE CASCADE,
    team_ref TEXT NOT NULL REFERENCES teams(team_ref) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('member', 'team_admin')),
    team_remark TEXT,
    joined_at TEXT NOT NULL
);
CREATE INDEX memberships_team_idx ON memberships(team_ref);

CREATE TABLE platform_admins (
    user_ref TEXT PRIMARY KEY REFERENCES users(user_ref) ON DELETE CASCADE,
    granted_by_user_ref TEXT NOT NULL REFERENCES users(user_ref),
    granted_at TEXT NOT NULL
);

CREATE TABLE sessions (
    token_digest TEXT PRIMARY KEY,
    user_ref TEXT NOT NULL REFERENCES users(user_ref) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_ref);

CREATE TABLE login_transactions (
    state_digest TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    intent TEXT NOT NULL CHECK(intent IN ('account', 'create', 'join', 'bootstrap')),
    invite_token TEXT,
    bootstrap_digest TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE bootstrap_tokens (
    token_digest TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE invites (
    invite_ref TEXT PRIMARY KEY,
    team_ref TEXT NOT NULL REFERENCES teams(team_ref) ON DELETE CASCADE,
    token_digest TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    max_uses INTEGER NOT NULL CHECK(max_uses > 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
    revoked_at TEXT,
    issued_by_user_ref TEXT NOT NULL REFERENCES users(user_ref),
    created_at TEXT NOT NULL
);
CREATE INDEX invites_team_idx ON invites(team_ref);

CREATE TABLE audit_events (
    event_ref TEXT PRIMARY KEY,
    actor_user_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    team_ref TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX audit_events_created_idx ON audit_events(created_at);
"""
