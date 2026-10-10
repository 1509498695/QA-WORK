from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


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
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class AuthResult:
    status: str
    message: str
    tenant_key: str | None = None
    profile_ref: str | None = None
    open_id_hint: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformIdentityClaim:
    """Verified identity attributes handed to the platform control plane.

    This deliberately excludes OAuth access and refresh tokens. The Provider
    remains the only component that owns those credentials.
    """

    app_id: str
    tenant_key: str
    open_id: str
    union_id: str | None
    name: str | None
    avatar_url: str | None
    profile_ref: str


class PlatformIdentityRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(min_length=32, max_length=512)
    code_verifier: str = Field(min_length=43, max_length=128)
