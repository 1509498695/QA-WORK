from __future__ import annotations

import pytest
from pydantic import ValidationError

from feishu_protocol import (
    DOCX_READ_CAPABILITY,
    LeaseDelivery,
    LeaseRequest,
    LocalClientIdentity,
)


def test_lease_request_normalizes_wire_fields() -> None:
    request = LeaseRequest(
        task_ref="  task-one  ",
        capabilities=(DOCX_READ_CAPABILITY, DOCX_READ_CAPABILITY),
    )

    assert request.task_ref == "task-one"
    assert request.capabilities == (DOCX_READ_CAPABILITY,)
    assert request.profile_ref is None


def test_lease_contracts_reject_unknown_fields_and_hide_secrets() -> None:
    with pytest.raises(ValidationError):
        LeaseRequest(
            task_ref="task-one",
            capabilities=(DOCX_READ_CAPABILITY,),
            unexpected=True,
        )

    delivery = LeaseDelivery(
        lease_ref="lease_test",
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        capabilities=(DOCX_READ_CAPABILITY,),
        scopes=("docx:document:readonly",),
        access_token="token-must-not-render",
        issued_at="2026-08-25T00:00:00+00:00",
        expires_at="2026-08-25T00:10:00+00:00",
        token_expires_at="2026-08-25T01:00:00+00:00",
    )
    identity = LocalClientIdentity(
        client_ref="client_test",
        client_secret="secret-must-not-render",
    )

    assert "token-must-not-render" not in repr(delivery)
    assert "secret-must-not-render" not in repr(identity)
