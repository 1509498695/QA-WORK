from __future__ import annotations

import pytest

from capability_contracts import (
    CapabilityError,
    CapabilityErrorCode,
    ResourceType,
    TargetKind,
    classify_locator,
    resolve_feishu_docx,
)


def test_locator_classifies_local_and_feishu_targets() -> None:
    local = classify_locator(r"D:\project\input\plan.docx")
    docx = classify_locator("https://example.feishu.cn/docx/doxcnAbCdEf123456")
    sheet = classify_locator(
        "https://example.feishu.cn/sheets/shtcnAbCdEf123456?sheet=abc123"
    )
    wiki = classify_locator(
        "https://example.feishu.cn/wiki/KhbDwPjf9iovDnkD3yscx9M8nAb"
    )

    assert local.target is TargetKind.LOCAL
    assert local.resource_type is ResourceType.LOCAL_FILE
    assert docx.target is TargetKind.FEISHU
    assert docx.resource_type is ResourceType.FEISHU_DOCX
    assert docx.resource_id == "doxcnAbCdEf123456"
    assert docx.canonical_url == "https://example.feishu.cn/docx/doxcnAbCdEf123456"
    assert sheet.resource_type is ResourceType.FEISHU_SHEET
    assert wiki.resource_type is ResourceType.FEISHU_WIKI
    assert wiki.resource_id == "KhbDwPjf9iovDnkD3yscx9M8nAb"


def test_docx_resolver_accepts_token_and_rejects_other_resources() -> None:
    assert resolve_feishu_docx("doxcnAbCdEf123456").resource_id == "doxcnAbCdEf123456"

    with pytest.raises(CapabilityError) as error:
        resolve_feishu_docx("https://example.feishu.cn/wiki/wikcnAbCdEf123456")

    assert error.value.code is CapabilityErrorCode.UNSUPPORTED_RESOURCE


def test_locator_does_not_guess_an_unrelated_url() -> None:
    locator = classify_locator("https://example.com/docx/doxcnAbCdEf123456")

    assert locator.target is TargetKind.UNKNOWN
    assert locator.resource_type is ResourceType.UNKNOWN
