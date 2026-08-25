from __future__ import annotations

from capability_contracts import (
    CapabilityError,
    CapabilityErrorCode,
    CapabilityManifest,
    OperationEvidence,
    OperationStatus,
)


def test_provider_neutral_models_serialize_stable_values() -> None:
    manifest = CapabilityManifest(
        provider_id="example-provider",
        provider_version="1.2.3",
        contract_versions=("1.0",),
        operations=("example.read",),
        resource_types=("example_document",),
    )
    evidence = OperationEvidence(
        observed_at="2026-08-25T00:00:00+00:00",
        content_hash="sha256:test",
        retrieval_complete=True,
    )

    assert manifest.model_dump(mode="json")["resource_types"] == [
        "example_document"
    ]
    assert evidence.retrieval_complete is True
    assert OperationStatus.OK.value == "ok"


def test_capability_error_exposes_only_structured_public_fields() -> None:
    error = CapabilityError(
        CapabilityErrorCode.PROVIDER_UNAVAILABLE,
        "Provider is temporarily unavailable.",
        retryable=True,
        details={"operation": "example.read"},
    )

    assert error.to_payload() == {
        "status": "provider_unavailable",
        "message": "Provider is temporarily unavailable.",
        "retryable": True,
        "details": {"operation": "example.read"},
    }
