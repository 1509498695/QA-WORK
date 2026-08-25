from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TargetKind(StrEnum):
    LOCAL = "local"
    FEISHU = "feishu"
    UNKNOWN = "unknown"


class ResourceType(StrEnum):
    LOCAL_FILE = "local_file"
    FEISHU_DOCX = "feishu_docx"
    FEISHU_WIKI = "feishu_wiki"
    FEISHU_SHEET = "feishu_sheet"
    UNKNOWN = "unknown"


class OperationStatus(StrEnum):
    OK = "ok"
    RETRIEVAL_INCOMPLETE = "retrieval_incomplete"


class ResourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: TargetKind
    resource_type: ResourceType
    original: str
    resource_id: str | None = None
    canonical_url: str | None = None


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
    resource_types: tuple[ResourceType, ...]
    development_only: bool = True
    notes: tuple[str, ...] = Field(default=())
