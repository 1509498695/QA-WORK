from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    app_id: str
    tenant_ref: str
    open_id: str
    union_id: str | None
    verified_name: str | None
    avatar_url: str | None
    profile_ref: str


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_ref: str
    tenant_ref: str
    app_id: str
    open_id: str
    union_id: str | None
    verified_name: str | None
    display_name: str
    avatar_url: str | None
    profile_ref: str
    last_verified_at: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TeamRecord:
    team_ref: str
    tenant_ref: str
    name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    user_ref: str
    team_ref: str
    role: str
    team_remark: str | None
    joined_at: str

    @property
    def is_team_admin(self) -> bool:
        return self.role == "team_admin"


@dataclass(frozen=True, slots=True)
class ViewerState:
    user: UserRecord
    membership: MembershipRecord | None
    team: TeamRecord | None
    is_platform_admin: bool
    csrf_token: str

    @property
    def is_team_admin(self) -> bool:
        return self.membership is not None and self.membership.is_team_admin


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_ref: str
    csrf_token: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class LoginRecord:
    code_verifier: str
    intent: str
    invite_token: str | None
    bootstrap_digest: str | None
    expires_at: str


@dataclass(frozen=True, slots=True)
class InviteRecord:
    invite_ref: str
    team_ref: str
    expires_at: str
    max_uses: int
    used_count: int
    revoked_at: str | None
    issuer_display_name: str

    @property
    def remaining_uses(self) -> int:
        return max(self.max_uses - self.used_count, 0)


@dataclass(frozen=True, slots=True)
class TeamMemberRecord:
    user: UserRecord
    membership: MembershipRecord


@dataclass(frozen=True, slots=True)
class PlatformAdminRecord:
    user: UserRecord
    granted_at: str


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    reachable: bool
    configured: bool
    app_id: str | None = None
    tenant_ref: str | None = None
    authorized_profiles: int = 0
    callback_uri: str | None = None
    message: str | None = None
