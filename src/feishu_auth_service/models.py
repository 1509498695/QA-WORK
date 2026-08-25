from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...]
    token_type: str
    refresh_token: str | None = field(default=None, repr=False)
    refresh_token_expires_in: int | None = None


@dataclass(frozen=True, slots=True)
class UserIdentity:
    tenant_key: str
    open_id: str
    union_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthResult:
    status: str
    message: str
    tenant_key: str | None = None
    profile_ref: str | None = None
    open_id_hint: str | None = None
    display_name: str | None = None
