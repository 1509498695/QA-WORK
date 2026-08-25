from __future__ import annotations

import asyncio

import httpx
import pytest

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.models import OperationStatus, ResourceType
from feishu_auth_service.leases import (
    SHEETS_READ_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_provider.lease_client import ProviderTokenLease
from feishu_provider.sheets import FeishuSheetsClient


SHEET_TOKEN = "shtcn1234567890"
WIKI_TOKEN = "EzhywOSQIiE92ZkHZmBcG0E9njg"
PROFILE_REF = "profile_0123456789abcdef0123"


class FakeLeaseClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.closed = False

    async def issue(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        capabilities: tuple[str, ...],
    ) -> ProviderTokenLease:
        self.calls.append((task_ref, profile_ref, capabilities))
        return ProviderTokenLease(
            lease_ref="lease_sheet_test",
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
            scopes=("sheets:spreadsheet:readonly",),
            access_token="sheet-access-token-must-not-render",
            issued_at="2026-08-24T00:00:00+00:00",
            expires_at="2026-08-24T00:10:00+00:00",
            token_expires_at="2026-08-24T01:00:00+00:00",
        )

    async def aclose(self) -> None:
        self.closed = True


def _metadata() -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "spreadsheet": {
                "title": "真实表格",
                "owner_id": "ou_owner",
                "token": SHEET_TOKEN,
                "url": f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
            }
        },
    }


def test_sheets_read_preserves_metadata_merges_formulas_and_values() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == (
            "Bearer sheet-access-token-must-not-render"
        )
        if request.url.path.endswith(f"/spreadsheets/{SHEET_TOKEN}"):
            return httpx.Response(200, json=_metadata())
        if request.url.path.endswith("/sheets/query"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "sheets": [
                            {
                                "sheet_id": "sheet-one",
                                "title": "主表",
                                "index": 0,
                                "hidden": False,
                                "resource_type": "sheet",
                                "grid_properties": {
                                    "frozen_row_count": 1,
                                    "frozen_column_count": 1,
                                    "row_count": 2,
                                    "column_count": 3,
                                },
                                "merges": [
                                    {
                                        "start_row_index": 0,
                                        "end_row_index": 1,
                                        "start_column_index": 0,
                                        "end_column_index": 2,
                                    },
                                    {
                                        "start_row_index": 1,
                                        "end_row_index": 1,
                                        "start_column_index": 0,
                                        "end_column_index": 2,
                                    },
                                ],
                            },
                            {
                                "sheet_id": "sheet-two",
                                "title": "隐藏表",
                                "index": 1,
                                "hidden": True,
                                "resource_type": "sheet",
                                "grid_properties": {
                                    "row_count": 1,
                                    "column_count": 2,
                                },
                            },
                        ]
                    },
                },
            )
        assert request.url.path.endswith("/values_batch_get")
        assert request.url.params["ranges"] == (
            "sheet-one!A1:C2,sheet-two!A1:B1"
        )
        assert request.url.params["valueRenderOption"] == "Formula"
        assert request.url.params["dateTimeRenderOption"] == "FormattedString"
        assert request.url.params["user_id_type"] == "open_id"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "revision": 17,
                    "totalCells": 8,
                    "valueRanges": [
                        {
                            "majorDimension": "ROWS",
                            "range": "sheet-one!A1:C2",
                            "revision": 17,
                            "values": [
                                ["A", "B", "=SUM(A2:B2)"],
                                [1, 2, 3],
                            ],
                        },
                        {
                            "majorDimension": "ROWS",
                            "range": "sheet-two!A1:B1",
                            "revision": 17,
                            "values": [
                                [
                                    [
                                        {
                                            "type": "text",
                                            "text": "富文本",
                                            "segmentStyle": {"bold": True},
                                        },
                                        {
                                            "type": "mention",
                                            "text": "测试用户",
                                            "token": "ou_test",
                                        },
                                    ],
                                    None,
                                ]
                            ],
                        },
                    ],
                },
            },
        )

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuSheetsClient(lease_client=lease_client, http_client=http)
    locator = f"https://example.feishu.cn/sheets/{SHEET_TOKEN}"

    first = asyncio.run(
        client.read(
            locator=locator,
            task_ref="task-sheet-one",
            profile_ref=PROFILE_REF,
        )
    )
    second = asyncio.run(
        client.read(
            locator=locator,
            task_ref="task-sheet-two",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(client.aclose())
    asyncio.run(http.aclose())

    assert first.status is OperationStatus.OK
    assert first.source.resource_type is ResourceType.FEISHU_SHEET
    assert first.title == "真实表格"
    assert first.revision == "17"
    assert first.sheet_count == first.returned_sheet_count == 2
    assert first.requested_cell_count == 8
    assert first.returned_value_count == 8
    assert first.worksheets[0].grid_properties.frozen_row_count == 1
    assert first.worksheets[0].merges[0].end_column_index == 2
    assert len(first.worksheets[0].merges) == 2
    assert first.worksheets[0].values[0][2] == "=SUM(A2:B2)"
    assert first.worksheets[1].hidden is True
    assert first.worksheets[1].values[0][0][1]["type"] == "mention"
    assert first.evidence.retrieval_complete is True
    assert first.evidence.content_hash == second.evidence.content_hash
    assert lease_client.calls == [
        ("task-sheet-one", PROFILE_REF, (SHEETS_READ_CAPABILITY,)),
        ("task-sheet-two", PROFILE_REF, (SHEETS_READ_CAPABILITY,)),
    ]
    assert lease_client.closed is True
    assert len(requests) == 6


def test_wiki_sheet_read_resolves_online_and_uses_one_combined_lease() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/spaces/get_node"):
            assert request.url.params["token"] == WIKI_TOKEN
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "node": {
                            "node_token": WIKI_TOKEN,
                            "space_id": "space-one",
                            "obj_type": "sheet",
                            "obj_token": SHEET_TOKEN,
                            "title": "Wiki 表格",
                            "has_child": False,
                        }
                    },
                },
            )
        if request.url.path.endswith(f"/spreadsheets/{SHEET_TOKEN}"):
            return httpx.Response(200, json=_metadata())
        if request.url.path.endswith("/sheets/query"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "sheets": [
                            {
                                "sheet_id": "sheet-one",
                                "title": "主表",
                                "index": 0,
                                "hidden": False,
                                "resource_type": "sheet",
                                "grid_properties": {
                                    "row_count": 1,
                                    "column_count": 1,
                                },
                            }
                        ]
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "revision": "18",
                    "valueRanges": [
                        {
                            "majorDimension": "ROWS",
                            "range": "sheet-one!A1:A1",
                            "revision": "18",
                            "values": [["内容"]],
                        }
                    ],
                },
            },
        )

    lease_client = FakeLeaseClient()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuSheetsClient(lease_client=lease_client, http_client=http)

    result = asyncio.run(
        client.read(
            locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
            task_ref="task-wiki-sheet",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.OK
    assert result.source.resource_type is ResourceType.FEISHU_WIKI
    assert result.wiki_resolution is not None
    assert result.wiki_resolution.object_type == "sheet"
    assert result.spreadsheet_token == SHEET_TOKEN
    assert result.worksheets[0].values == [["内容"]]
    assert lease_client.calls == [
        (
            "task-wiki-sheet",
            PROFILE_REF,
            (SHEETS_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY),
        )
    ]


def test_wiki_non_sheet_is_rejected_before_sheet_apis() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": WIKI_TOKEN,
                        "obj_type": "docx",
                        "obj_token": "doxcn1234567890",
                    }
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuSheetsClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=f"https://example.feishu.cn/wiki/{WIKI_TOKEN}",
                task_ref="task-wrong-type",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.UNSUPPORTED_RESOURCE
    assert error.value.details == {"wiki_object_type": "docx"}
    assert len(requests) == 1


def test_sheets_read_reports_limits_and_unsupported_worksheet_types() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/spreadsheets/{SHEET_TOKEN}"):
            return httpx.Response(200, json=_metadata())
        if request.url.path.endswith("/sheets/query"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "sheets": [
                            {
                                "sheet_id": "large-sheet",
                                "title": "大表",
                                "index": 0,
                                "hidden": False,
                                "resource_type": "sheet",
                                "grid_properties": {
                                    "row_count": 100,
                                    "column_count": 30,
                                },
                            },
                            {
                                "sheet_id": "embedded-bitable",
                                "title": "多维表格",
                                "index": 1,
                                "hidden": False,
                                "resource_type": "bitable",
                                "grid_properties": {
                                    "row_count": 10,
                                    "column_count": 5,
                                },
                            },
                        ]
                    },
                },
            )
        assert request.url.params["ranges"] == "large-sheet!A1:B1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "revision": 19,
                    "valueRanges": [
                        {
                            "majorDimension": "ROWS",
                            "range": "large-sheet!A1:B1",
                            "revision": 19,
                            "values": [["A", "B"]],
                        }
                    ],
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuSheetsClient(
        lease_client=FakeLeaseClient(),
        http_client=http,
        max_rows_per_worksheet=2,
        max_columns_per_worksheet=2,
        max_total_cells=3,
    )
    result = asyncio.run(
        client.read(
            locator=f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
            task_ref="task-limited",
            profile_ref=PROFILE_REF,
        )
    )
    asyncio.run(http.aclose())

    assert result.status is OperationStatus.RETRIEVAL_INCOMPLETE
    assert result.requested_cell_count == 2
    assert result.worksheets[0].requested_range == "large-sheet!A1:B1"
    assert result.worksheets[1].requested_range is None
    assert "total_cell_limit_exceeded:3" in result.evidence.warnings
    assert any("row_limit_exceeded" in item for item in result.evidence.warnings)
    assert any("column_limit_exceeded" in item for item in result.evidence.warnings)
    assert any(
        "unsupported_worksheet_resource_type:bitable" in item
        for item in result.evidence.warnings
    )


def test_sheets_permission_error_is_safely_mapped() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"code": 1310213, "msg": "sensitive provider detail"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FeishuSheetsClient(lease_client=FakeLeaseClient(), http_client=http)

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            client.read(
                locator=f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
                task_ref="task-denied",
                profile_ref=PROFILE_REF,
            )
        )

    asyncio.run(http.aclose())
    assert error.value.code is CapabilityErrorCode.PERMISSION_DENIED
    assert error.value.details == {"platform_code": 1310213}
    assert "sensitive provider detail" not in str(error.value)
