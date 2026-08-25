"""Stable public contracts shared by Provider and business Skill boundaries."""

from capability_contracts.errors import (
    CapabilityError,
    CapabilityErrorCode,
    CapabilityFailure,
)
from capability_contracts.locator import classify_locator, resolve_feishu_docx
from capability_contracts.models import (
    CapabilityManifest,
    OperationEvidence,
    OperationStatus,
    ResourceLocator,
    ResourceType,
    TargetKind,
)

__all__ = [
    "CapabilityError",
    "CapabilityErrorCode",
    "CapabilityFailure",
    "CapabilityManifest",
    "OperationEvidence",
    "OperationStatus",
    "ResourceLocator",
    "ResourceType",
    "TargetKind",
    "classify_locator",
    "resolve_feishu_docx",
]
