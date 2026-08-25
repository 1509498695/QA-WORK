from __future__ import annotations

import asyncio

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.models import OperationEvidence, OperationStatus
from feishu_provider.docx import DocxReadResult
from feishu_provider.locator import classify_locator, resolve_feishu_docx
from feishu_provider.mcp_server import build_server
from feishu_provider.sheets import SheetsReadResult


class FakeDocxReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str,
    ) -> DocxReadResult:
        self.calls.append((locator, task_ref, profile_ref))
        source = resolve_feishu_docx(locator)
        return DocxReadResult(
            status=OperationStatus.OK,
            task_ref=task_ref,
            profile_ref=profile_ref,
            source=source,
            document_id=source.resource_id or "",
            title="测试文档",
            revision_id="42",
            blocks=[{"block_id": "root", "block_type": 1}],
            block_count=1,
            page_count=1,
            evidence=OperationEvidence(
                observed_at="2026-08-24T00:00:00+00:00",
                content_hash="sha256:test",
                provider_revision="42",
                retrieval_complete=True,
            ),
        )


class FakeSheetsReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str,
    ) -> SheetsReadResult:
        self.calls.append((locator, task_ref, profile_ref))
        source = classify_locator(locator)
        return SheetsReadResult(
            status=OperationStatus.OK,
            task_ref=task_ref,
            profile_ref=profile_ref,
            source=source,
            spreadsheet_token=source.resource_id or "",
            title="测试表格",
            revision="9",
            sheet_count=0,
            returned_sheet_count=0,
            worksheets=[],
            requested_cell_count=0,
            returned_value_count=0,
            evidence=OperationEvidence(
                observed_at="2026-08-24T00:00:00+00:00",
                content_hash="sha256:sheet-test",
                provider_revision="9",
                retrieval_complete=True,
            ),
        )


class AuthRequiredReader:
    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str,
    ):  # type: ignore[no-untyped-def]
        del locator, task_ref, profile_ref
        raise CapabilityError(
            CapabilityErrorCode.AUTH_REQUIRED,
            "The requested Profile needs OAuth.",
            details={"capabilities": ["feishu.sheets.read"]},
        )


def test_mcp_server_exposes_typed_read_only_contracts() -> None:
    async def exercise():  # type: ignore[no-untyped-def]
        reader = FakeDocxReader()
        sheets_reader = FakeSheetsReader()
        server = build_server(reader, sheets_reader)
        tools = await server.list_tools()
        manifest_result = await server.call_tool("feishu_provider_manifest", {})
        resolve_result = await server.call_tool(
            "feishu_resource_resolve",
            {"locator": r"D:\requirements\plan.docx"},
        )
        docx_result = await server.call_tool(
            "feishu_docx_read",
            {
                "locator": "doxcn1234567890",
                "task_ref": "task-one",
                "profile_ref": "profile_0123456789abcdef0123",
            },
        )
        sheets_result = await server.call_tool(
            "feishu_sheets_read",
            {
                "locator": "https://example.feishu.cn/sheets/shtcn1234567890",
                "task_ref": "task-two",
                "profile_ref": "profile_0123456789abcdef0123",
            },
        )
        return (
            reader,
            sheets_reader,
            tools,
            manifest_result,
            resolve_result,
            docx_result,
            sheets_result,
        )

    (
        reader,
        sheets_reader,
        tools,
        manifest_result,
        resolve_result,
        docx_result,
        sheets_result,
    ) = asyncio.run(exercise())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "feishu_provider_manifest",
        "feishu_resource_resolve",
        "feishu_docx_read",
        "feishu_sheets_read",
    }
    for tool in by_name.values():
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.outputSchema is not None
    assert by_name["feishu_docx_read"].inputSchema["required"] == [
        "locator",
        "task_ref",
    ]
    assert by_name["feishu_sheets_read"].inputSchema["required"] == [
        "locator",
        "task_ref",
    ]
    assert by_name["feishu_docx_read"].inputSchema["properties"]["profile_ref"][
        "default"
    ] is None
    assert by_name["feishu_sheets_read"].inputSchema["properties"]["profile_ref"][
        "default"
    ] is None

    assert manifest_result[1]["development_only"] is True
    assert manifest_result[1]["provider_version"] == "0.4.1"
    assert manifest_result[1]["resource_types"] == [
        "feishu_docx",
        "feishu_wiki",
        "feishu_sheet",
    ]
    assert resolve_result[1]["target"] == "local"
    assert docx_result[1]["status"] == "ok"
    assert docx_result[1]["block_count"] == 1
    assert docx_result[1]["assets"] == []
    assert docx_result[1]["asset_count"] == 0
    assert docx_result[1]["asset_total_bytes"] == 0
    assert sheets_result[1]["status"] == "ok"
    assert sheets_result[1]["title"] == "测试表格"
    assert reader.calls == [
        (
            "doxcn1234567890",
            "task-one",
            "profile_0123456789abcdef0123",
        )
    ]
    assert sheets_reader.calls == [
        (
            "https://example.feishu.cn/sheets/shtcn1234567890",
            "task-two",
            "profile_0123456789abcdef0123",
        )
    ]


def test_mcp_read_returns_structured_auth_recovery_link() -> None:
    async def exercise():  # type: ignore[no-untyped-def]
        reader = AuthRequiredReader()
        server = build_server(reader, reader)  # type: ignore[arg-type]
        return await server.call_tool(
            "feishu_sheets_read",
            {
                "locator": "https://example.feishu.cn/sheets/shtcn1234567890",
                "task_ref": "task-auth",
                "profile_ref": "profile_0123456789abcdef0123",
            },
        )

    result = asyncio.run(exercise())

    assert result[1] == {
        "status": "auth_required",
        "message": "The requested Profile needs OAuth.",
        "retryable": False,
        "details": {
            "capabilities": ["feishu.sheets.read"],
            "authorization_url": "http://localhost:3000/oauth/start",
        },
    }
