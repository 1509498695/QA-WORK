from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OperationStatus(StrEnum):
    OK = "ok"
    RETRIEVAL_INCOMPLETE = "retrieval_incomplete"
    PREVIEW_READY = "preview_ready"
    DELIVERED = "delivered"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"
    VERIFICATION_INCOMPLETE = "verification_incomplete"
    NO_CHANGE = "no_change"


class OperationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: str
    content_hash: str
    provider_revision: str | None = None
    retrieval_complete: bool
    warnings: tuple[str, ...] = ()


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    provider_version: str
    contract_versions: tuple[str, ...]
    operations: tuple[str, ...]
    resource_types: tuple[str, ...]
    development_only: bool = True
    notes: tuple[str, ...] = Field(default=())
