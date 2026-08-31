from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_auth_service.config import Settings
from feishu_auth_service.feishu import FeishuApiError, TokenRefresher
from feishu_auth_service.profiles import ProfileAuthorization, ProfileError, ProfileVault
from feishu_protocol import (
    DOCX_MEDIA_READ_CAPABILITY,
    DOCX_READ_CAPABILITY,
    SHEETS_EXPORT_VERIFY_CAPABILITY,
    SHEETS_MANAGED_WRITE_CAPABILITY,
    SHEETS_MEDIA_READ_CAPABILITY,
    SHEETS_READ_CAPABILITY,
    SHEETS_TYPED_VALUES_WRITE_CAPABILITY,
    WIKI_CHILD_LIST_CAPABILITY,
    WIKI_NODE_CREATE_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
    LeaseDelivery,
)


MAX_LEASE_SECONDS = 600
DOCX_READ_SCOPES = ("docx:document:readonly", "docx:document")
WIKI_NODE_READ_SCOPES = ("wiki:node:read", "wiki:wiki:readonly", "wiki:wiki")
WIKI_CHILD_LIST_SCOPES = ("wiki:node:retrieve", "wiki:wiki:readonly", "wiki:wiki")
WIKI_NODE_CREATE_SCOPES = ("wiki:node:create", "wiki:wiki")
DOCX_MEDIA_READ_SCOPES = (
    "docs:document.media:download",
    "drive:drive:readonly",
    "drive:drive",
    "docs:doc:readonly",
    "docs:doc",
)
SHEETS_MEDIA_READ_SCOPES = DOCX_MEDIA_READ_SCOPES
SHEETS_READ_SCOPES = (
    "sheets:spreadsheet:readonly",
    "sheets:spreadsheet",
    "drive:drive:readonly",
    "drive:drive",
)
SHEETS_MANAGED_WRITE_SCOPES = (
    "sheets:spreadsheet",
    "drive:drive",
)
SHEETS_TYPED_VALUES_WRITE_SCOPES = ("sheets:spreadsheet:write_only",)
SHEETS_EXPORT_VERIFY_SCOPES = (
    "drive:export:readonly",
    "docs:document:export",
)
_CAPABILITY_SCOPE_ALTERNATIVES = {
    DOCX_READ_CAPABILITY: DOCX_READ_SCOPES,
    WIKI_NODE_READ_CAPABILITY: WIKI_NODE_READ_SCOPES,
    WIKI_CHILD_LIST_CAPABILITY: WIKI_CHILD_LIST_SCOPES,
    WIKI_NODE_CREATE_CAPABILITY: WIKI_NODE_CREATE_SCOPES,
    DOCX_MEDIA_READ_CAPABILITY: DOCX_MEDIA_READ_SCOPES,
    SHEETS_MEDIA_READ_CAPABILITY: SHEETS_MEDIA_READ_SCOPES,
    SHEETS_READ_CAPABILITY: SHEETS_READ_SCOPES,
    SHEETS_MANAGED_WRITE_CAPABILITY: SHEETS_MANAGED_WRITE_SCOPES,
    SHEETS_TYPED_VALUES_WRITE_CAPABILITY: SHEETS_TYPED_VALUES_WRITE_SCOPES,
    SHEETS_EXPORT_VERIFY_CAPABILITY: SHEETS_EXPORT_VERIFY_SCOPES,
}


@dataclass(frozen=True, slots=True)
class TokenLease:
    lease_ref: str
    task_ref: str
    profile_ref: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    access_token: str = field(repr=False)
    issued_at: str
    expires_at: str
    token_expires_at: str

    def delivery(self) -> LeaseDelivery:
        return LeaseDelivery(
            lease_ref=self.lease_ref,
            task_ref=self.task_ref,
            profile_ref=self.profile_ref,
            capabilities=self.capabilities,
            scopes=self.scopes,
            access_token=self.access_token,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            token_expires_at=self.token_expires_at,
        )


@dataclass(frozen=True, slots=True)
class _CachedAccessToken:
    access_token: str = field(repr=False)
    scopes: tuple[str, ...]
    expires_at_monotonic: float
    expires_at_wall: datetime


class LocalLeaseBroker:
    """Local-v0 broker: refreshes Profiles and issues task-scoped in-memory leases."""

    def __init__(
        self,
        *,
        settings: Settings,
        profile_vault: ProfileVault,
        token_refresher: TokenRefresher,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_lease_seconds: int = MAX_LEASE_SECONDS,
    ) -> None:
        if not 1 <= max_lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("Lease lifetime must be between 1 and 600 seconds")
        self._settings = settings
        self._profile_vault = profile_vault
        self._token_refresher = token_refresher
        self._clock = clock
        self._wall_clock = wall_clock
        self._max_lease_seconds = max_lease_seconds
        self._cache: dict[str, _CachedAccessToken] = {}
        self._lock = threading.Lock()

    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> TokenLease:
        normalized_capabilities = _validate_capabilities(capabilities)
        try:
            profile = self._profile_vault.load(profile_ref)
        except ProfileError as exc:
            raise CapabilityError(
                CapabilityErrorCode.AUTH_REQUIRED,
                "The requested Feishu Profile is not authorized on this control plane.",
            ) from exc
        self._validate_profile(profile, normalized_capabilities)
        cached = self._cached_token(profile_ref, normalized_capabilities)
        if cached is None:
            cached = await self._refresh(profile, normalized_capabilities)

        now_mono = self._clock()
        remaining_seconds = int(cached.expires_at_monotonic - now_mono)
        lease_seconds = min(self._max_lease_seconds, remaining_seconds)
        if lease_seconds <= 0:
            raise CapabilityError(
                CapabilityErrorCode.LEASE_EXPIRED,
                "The refreshed Feishu access token cannot support a task lease.",
            )
        issued_wall = self._wall_clock()
        return TokenLease(
            lease_ref=f"lease_{secrets.token_urlsafe(18)}",
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=normalized_capabilities,
            scopes=cached.scopes,
            access_token=cached.access_token,
            issued_at=issued_wall.isoformat(timespec="seconds"),
            expires_at=(issued_wall + timedelta(seconds=lease_seconds)).isoformat(
                timespec="seconds"
            ),
            token_expires_at=cached.expires_at_wall.isoformat(timespec="seconds"),
        )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _validate_profile(
        self,
        profile: ProfileAuthorization,
        capabilities: tuple[str, ...],
    ) -> None:
        if (
            profile.app_id != self._settings.app_id
            or profile.tenant_key != self._settings.allowed_tenant_key
        ):
            raise CapabilityError(
                CapabilityErrorCode.AUTH_REQUIRED,
                "The Feishu Profile does not match the current deployment binding.",
            )
        _ensure_scope_coverage(profile.scopes, capabilities)
        if profile.refresh_token_expires_at is not None:
            try:
                expires_at = datetime.fromisoformat(profile.refresh_token_expires_at)
            except ValueError as exc:
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "The Feishu Profile Refresh Token lifetime is invalid.",
                ) from exc
            if expires_at <= self._wall_clock():
                raise CapabilityError(
                    CapabilityErrorCode.AUTH_REQUIRED,
                    "The Feishu Profile authorization has expired and requires OAuth again.",
                )

    def _cached_token(
        self,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> _CachedAccessToken | None:
        with self._lock:
            cached = self._cache.get(profile_ref)
        if cached is None or cached.expires_at_monotonic - self._clock() <= 30:
            return None
        try:
            _ensure_scope_coverage(cached.scopes, capabilities)
        except CapabilityError:
            return None
        return cached

    async def _refresh(
        self,
        profile: ProfileAuthorization,
        capabilities: tuple[str, ...],
    ) -> _CachedAccessToken:
        try:
            grant = await self._token_refresher.refresh_access_token(profile.refresh_token)
        except FeishuApiError as exc:
            code = (
                CapabilityErrorCode.PROVIDER_UNAVAILABLE
                if exc.http_status is None or (exc.http_status and exc.http_status >= 500)
                else CapabilityErrorCode.AUTH_REQUIRED
            )
            raise CapabilityError(
                code,
                "Feishu did not issue an access token for this Profile.",
                retryable=code is CapabilityErrorCode.PROVIDER_UNAVAILABLE,
            ) from exc

        scopes = grant.scopes or profile.scopes
        _ensure_scope_coverage(scopes, capabilities)
        if grant.refresh_token:
            try:
                self._profile_vault.rotate_refresh_token(
                    profile.profile_ref,
                    refresh_token=grant.refresh_token,
                    refresh_token_expires_in=grant.refresh_token_expires_in,
                    scopes=scopes,
                )
            except ProfileError as exc:
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "The rotated Feishu authorization could not be saved safely.",
                ) from exc

        now_mono = self._clock()
        now_wall = self._wall_clock()
        cached = _CachedAccessToken(
            access_token=grant.access_token,
            scopes=scopes,
            expires_at_monotonic=now_mono + grant.expires_in,
            expires_at_wall=now_wall + timedelta(seconds=grant.expires_in),
        )
        with self._lock:
            self._cache[profile.profile_ref] = cached
        return cached


def _validate_capabilities(capabilities: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(capability.strip() for capability in capabilities)))
    if not normalized or any(not capability for capability in normalized):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "At least one valid Provider capability is required.",
        )
    unsupported = [
        capability
        for capability in normalized
        if capability not in _CAPABILITY_SCOPE_ALTERNATIVES
    ]
    if unsupported:
        raise CapabilityError(
            CapabilityErrorCode.PERMISSION_DENIED,
            "The local Provider client is not allowed to request these capabilities.",
            details={"unsupported_capabilities": unsupported},
        )
    return normalized


def _ensure_scope_coverage(
    scopes: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> None:
    scope_set = set(scopes)
    missing = [
        capability
        for capability in capabilities
        if not scope_set.intersection(_CAPABILITY_SCOPE_ALTERNATIVES[capability])
    ]
    if missing:
        raise CapabilityError(
            CapabilityErrorCode.AUTH_REQUIRED,
            "The Feishu Profile is missing a required OAuth Scope.",
            details={"capabilities": missing},
        )
