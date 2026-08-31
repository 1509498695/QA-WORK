from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilityErrorCode(StrEnum):
    INVALID_LOCATOR = "invalid_locator"
    UNSUPPORTED_RESOURCE = "unsupported_resource"
    CONFIGURATION_REQUIRED = "configuration_required"
    AUTH_REQUIRED = "auth_required"
    PROFILE_SELECTION_REQUIRED = "profile_selection_required"
    CLIENT_UNAUTHORIZED = "client_unauthorized"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    RETRIEVAL_INCOMPLETE = "retrieval_incomplete"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_CONTRACT_ERROR = "provider_contract_error"
    LEASE_EXPIRED = "lease_expired"
    AMBIGUOUS_WRITE = "ambiguous_write"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    UNSUPPORTED_DELIVERY_SPEC = "unsupported_delivery_spec"
    PREVIEW_EXPIRED = "preview_expired"
    PRECONDITION_FAILED = "precondition_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    WRITE_CONFLICT = "write_conflict"
    VERIFICATION_INCOMPLETE = "verification_incomplete"
    BASE_SPEC_REQUIRED = "base_spec_required"
    BASELINE_VERIFICATION_INCOMPLETE = "baseline_verification_incomplete"


class CapabilityFailure(BaseModel):
    """Structured public failure returned by semantic Provider tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CapabilityErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityError(RuntimeError):
    """Safe structured error that never embeds credentials or provider payloads."""

    def __init__(
        self,
        code: CapabilityErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(f"{code.value}: {message}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

    def to_failure(
        self,
        *,
        extra_details: dict[str, Any] | None = None,
    ) -> CapabilityFailure:
        details = dict(self.details)
        if extra_details:
            details.update(extra_details)
        return CapabilityFailure(
            status=self.code,
            message=self.message,
            retryable=self.retryable,
            details=details,
        )
