from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.locator import classify_locator
from capability_contracts.models import (
    OperationEvidence,
    OperationStatus,
    ResourceLocator,
    ResourceType,
    TargetKind,
)
from feishu_auth_service.leases import (
    SHEETS_READ_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)
from feishu_provider.common import (
    OPEN_API_ORIGIN,
    RESOLVED_OBJECT_TOKEN,
    WIKI_NODE_ENDPOINT,
    WikiNodeResolution,
    data_object,
    http_error,
    optional_text,
    required_text,
)
from feishu_provider.lease_client import LeaseClient, LoopbackLeaseClient


SPREADSHEET_ENDPOINT = "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}"
SHEETS_QUERY_ENDPOINT = (
    "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
)
VALUES_BATCH_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get"
)

DEFAULT_MAX_WORKSHEETS = 100
DEFAULT_MAX_ROWS_PER_WORKSHEET = 5_000
DEFAULT_MAX_COLUMNS_PER_WORKSHEET = 500
DEFAULT_MAX_TOTAL_CELLS = 200_000
DEFAULT_MAX_RANGES_PER_REQUEST = 20
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_SHEET_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_KNOWN_COMPLEX_CELL_TYPES = {"text", "mention", "url", "formula"}


class GridProperties(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    frozen_row_count: int = Field(default=0, ge=0)
    frozen_column_count: int = Field(default=0, ge=0)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)


class MergeRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_row_index: int = Field(ge=0)
    end_row_index: int = Field(ge=0)
    start_column_index: int = Field(ge=0)
    end_column_index: int = Field(ge=0)


class WorksheetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_id: str
    title: str
    index: int = Field(ge=0)
    hidden: bool
    resource_type: str
    grid_properties: GridProperties
    merges: list[MergeRange] = Field(default_factory=list)
    requested_range: str | None = None
    returned_range: str | None = None
    revision: str | None = None
    values: list[list[Any]] = Field(default_factory=list)
    requested_cell_count: int = Field(default=0, ge=0)
    returned_value_count: int = Field(default=0, ge=0)
    retrieval_complete: bool
    warnings: tuple[str, ...] = ()


class SheetsReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = "feishu"
    provider_version: str = "0.4.1"
    operation_id: str = "feishu_sheets_read"
    status: OperationStatus
    task_ref: str
    profile_ref: str
    source: ResourceLocator
    wiki_resolution: WikiNodeResolution | None = None
    spreadsheet_token: str
    title: str
    owner_id: str | None = None
    url: str | None = None
    revision: str | None = None
    sheet_count: int = Field(ge=0)
    returned_sheet_count: int = Field(ge=0)
    worksheets: list[WorksheetSnapshot]
    requested_cell_count: int = Field(ge=0)
    returned_value_count: int = Field(ge=0)
    evidence: OperationEvidence


class SheetsReader(Protocol):
    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> SheetsReadResult: ...


@dataclass(slots=True)
class _WorksheetPlan:
    sheet_id: str
    title: str
    index: int
    hidden: bool
    resource_type: str
    grid_properties: GridProperties
    merges: list[MergeRange]
    warnings: list[str] = field(default_factory=list)
    requested_range: str | None = None
    requested_cell_count: int = 0


class FeishuSheetsClient:
    def __init__(
        self,
        *,
        lease_client: LeaseClient,
        http_client: httpx.AsyncClient | None = None,
        open_api_origin: str = OPEN_API_ORIGIN,
        timeout_seconds: float = 15.0,
        max_worksheets: int = DEFAULT_MAX_WORKSHEETS,
        max_rows_per_worksheet: int = DEFAULT_MAX_ROWS_PER_WORKSHEET,
        max_columns_per_worksheet: int = DEFAULT_MAX_COLUMNS_PER_WORKSHEET,
        max_total_cells: int = DEFAULT_MAX_TOTAL_CELLS,
        max_ranges_per_request: int = DEFAULT_MAX_RANGES_PER_REQUEST,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        limits = (
            max_worksheets,
            max_rows_per_worksheet,
            max_columns_per_worksheet,
            max_total_cells,
            max_ranges_per_request,
            max_response_bytes,
        )
        if any(limit < 1 for limit in limits):
            raise ValueError("Sheets read limits must be positive")
        self._lease_client = lease_client
        self._origin = open_api_origin.rstrip("/")
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._max_worksheets = max_worksheets
        self._max_rows_per_worksheet = max_rows_per_worksheet
        self._max_columns_per_worksheet = max_columns_per_worksheet
        self._max_total_cells = max_total_cells
        self._max_ranges_per_request = max_ranges_per_request
        self._max_response_bytes = max_response_bytes

    @classmethod
    def default(cls) -> FeishuSheetsClient:
        return cls(lease_client=LoopbackLeaseClient.default())

    async def read(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> SheetsReadResult:
        source = classify_locator(locator)
        if source.target is not TargetKind.FEISHU:
            raise CapabilityError(
                CapabilityErrorCode.INVALID_LOCATOR,
                "A Feishu Sheet or Wiki URL is required.",
            )
        if source.resource_type not in {
            ResourceType.FEISHU_SHEET,
            ResourceType.FEISHU_WIKI,
        }:
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                "Feishu Sheets read supports only Sheet resources or Wiki nodes.",
                details={"resource_type": source.resource_type.value},
            )
        if source.resource_id is None:
            raise CapabilityError(
                CapabilityErrorCode.INVALID_LOCATOR,
                "The Feishu locator has no resource identifier.",
            )

        capabilities = (SHEETS_READ_CAPABILITY,)
        if source.resource_type is ResourceType.FEISHU_WIKI:
            capabilities = (SHEETS_READ_CAPABILITY, WIKI_NODE_READ_CAPABILITY)
        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=capabilities,
        )
        resolved_profile_ref = lease.profile_ref
        headers = {
            "Authorization": f"Bearer {lease.access_token}",
            "Accept": "application/json",
        }

        wiki_resolution: WikiNodeResolution | None = None
        spreadsheet_token = source.resource_id
        if source.resource_type is ResourceType.FEISHU_WIKI:
            wiki_resolution = await self._get_wiki_node(source.resource_id, headers)
            if wiki_resolution.object_type != "sheet":
                raise CapabilityError(
                    CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                    "The Feishu Wiki node does not point to a Sheet.",
                    details={"wiki_object_type": wiki_resolution.object_type},
                )
            spreadsheet_token = wiki_resolution.object_token
        if not RESOLVED_OBJECT_TOKEN.fullmatch(spreadsheet_token):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "The resolved Feishu Sheet has an invalid spreadsheet token.",
            )

        spreadsheet = await self._get_spreadsheet(spreadsheet_token, headers)
        plans, sheet_count, global_warnings = await self._get_worksheet_plans(
            spreadsheet_token,
            headers,
        )
        self._assign_ranges(plans, global_warnings)
        value_ranges, revisions = await self._get_value_ranges(
            spreadsheet_token,
            headers,
            [plan.requested_range for plan in plans if plan.requested_range],
        )
        if len(revisions) > 1:
            global_warnings.append("revision_changed_during_read")
        revision = sorted(revisions)[0] if revisions else None

        worksheets: list[WorksheetSnapshot] = []
        value_index = 0
        for plan in plans:
            values: list[list[Any]] = []
            returned_range: str | None = None
            sheet_revision: str | None = None
            if plan.requested_range is not None:
                value_range = value_ranges[value_index]
                value_index += 1
                if value_range is None:
                    plan.warnings.append("value_range_missing")
                else:
                    returned_range = optional_text(value_range.get("range"))
                    sheet_revision = optional_text(value_range.get("revision"))
                    values = _values(value_range)
                    if returned_range is None:
                        plan.warnings.append("returned_range_missing")
                    major_dimension = value_range.get("majorDimension")
                    if major_dimension not in {None, "ROWS"}:
                        plan.warnings.append("unexpected_major_dimension")
                    unsupported_complex_cells = sum(
                        1
                        for row in values
                        for cell in row
                        if isinstance(cell, (dict, list))
                        and not _known_complex_cell(cell)
                    )
                    if unsupported_complex_cells:
                        plan.warnings.append(
                            "unsupported_complex_cell_values:"
                            f"{unsupported_complex_cells}"
                        )
            returned_value_count = sum(len(row) for row in values)
            worksheets.append(
                WorksheetSnapshot(
                    sheet_id=plan.sheet_id,
                    title=plan.title,
                    index=plan.index,
                    hidden=plan.hidden,
                    resource_type=plan.resource_type,
                    grid_properties=plan.grid_properties,
                    merges=plan.merges,
                    requested_range=plan.requested_range,
                    returned_range=returned_range,
                    revision=sheet_revision or revision,
                    values=values,
                    requested_cell_count=plan.requested_cell_count,
                    returned_value_count=returned_value_count,
                    retrieval_complete=not plan.warnings,
                    warnings=tuple(plan.warnings),
                )
            )

        all_warnings = list(global_warnings)
        for worksheet in worksheets:
            all_warnings.extend(
                f"sheet:{worksheet.sheet_id}:{warning}"
                for warning in worksheet.warnings
            )
        retrieval_complete = not all_warnings
        title = required_text(spreadsheet.get("title"), "title", "spreadsheet")
        owner_id = optional_text(spreadsheet.get("owner_id"))
        url = optional_text(spreadsheet.get("url"))
        canonical = json.dumps(
            {
                "wiki_resolution": (
                    wiki_resolution.model_dump(mode="json")
                    if wiki_resolution is not None
                    else None
                ),
                "spreadsheet": {
                    "spreadsheet_token": spreadsheet_token,
                    "title": title,
                    "owner_id": owner_id,
                    "url": url,
                },
                "revision": revision,
                "sheet_count": sheet_count,
                "worksheets": [
                    worksheet.model_dump(mode="json") for worksheet in worksheets
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence = OperationEvidence(
            observed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            content_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
            provider_revision=revision,
            retrieval_complete=retrieval_complete,
            warnings=tuple(all_warnings),
        )
        return SheetsReadResult(
            status=(
                OperationStatus.OK
                if retrieval_complete
                else OperationStatus.RETRIEVAL_INCOMPLETE
            ),
            task_ref=task_ref,
            profile_ref=resolved_profile_ref,
            source=source,
            wiki_resolution=wiki_resolution,
            spreadsheet_token=spreadsheet_token,
            title=title,
            owner_id=owner_id,
            url=url,
            revision=revision,
            sheet_count=sheet_count,
            returned_sheet_count=len(worksheets),
            worksheets=worksheets,
            requested_cell_count=sum(
                worksheet.requested_cell_count for worksheet in worksheets
            ),
            returned_value_count=sum(
                worksheet.returned_value_count for worksheet in worksheets
            ),
            evidence=evidence,
        )

    async def aclose(self) -> None:
        await self._lease_client.aclose()
        if self._owns_http_client:
            await self._http.aclose()

    async def _get_wiki_node(
        self,
        node_token: str,
        headers: dict[str, str],
    ) -> WikiNodeResolution:
        response = await self._request(
            WIKI_NODE_ENDPOINT,
            headers=headers,
            params={"token": node_token},
            operation="Wiki node resolution",
        )
        data = data_object(response, "wiki_node")
        node = data.get("node")
        if not isinstance(node, dict):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid Wiki node contract.",
            )
        has_child = node.get("has_child")
        if has_child is not None and not isinstance(has_child, bool):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid Wiki has_child value.",
            )
        return WikiNodeResolution(
            node_token=required_text(node.get("node_token"), "node_token"),
            space_id=optional_text(node.get("space_id")),
            object_type=required_text(node.get("obj_type"), "obj_type").lower(),
            object_token=required_text(node.get("obj_token"), "obj_token"),
            title=optional_text(node.get("title")),
            node_type=optional_text(node.get("node_type")),
            has_child=has_child,
        )

    async def _get_spreadsheet(
        self,
        spreadsheet_token: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = await self._request(
            SPREADSHEET_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
            headers=headers,
            operation="Sheets metadata read",
        )
        data = data_object(response, "sheets_metadata")
        spreadsheet = data.get("spreadsheet")
        if not isinstance(spreadsheet, dict):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid spreadsheet metadata contract.",
            )
        returned_token = optional_text(spreadsheet.get("token"))
        if returned_token is not None and returned_token != spreadsheet_token:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned metadata for a different spreadsheet.",
            )
        return spreadsheet

    async def _get_worksheet_plans(
        self,
        spreadsheet_token: str,
        headers: dict[str, str],
    ) -> tuple[list[_WorksheetPlan], int, list[str]]:
        response = await self._request(
            SHEETS_QUERY_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
            headers=headers,
            operation="Sheets worksheet query",
        )
        data = data_object(response, "sheets_query")
        raw_sheets = data.get("sheets")
        if not isinstance(raw_sheets, list) or any(
            not isinstance(item, dict) for item in raw_sheets
        ):
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                "Feishu returned an invalid worksheet list.",
            )
        warnings: list[str] = []
        sheet_count = len(raw_sheets)
        if sheet_count > self._max_worksheets:
            warnings.append(
                f"worksheet_count_limit_exceeded:{sheet_count}>{self._max_worksheets}"
            )
            raw_sheets = raw_sheets[: self._max_worksheets]
        plans = [_worksheet_plan(item) for item in raw_sheets]
        return plans, sheet_count, warnings

    def _assign_ranges(
        self,
        plans: list[_WorksheetPlan],
        global_warnings: list[str],
    ) -> None:
        remaining_cells = self._max_total_cells
        total_limit_reported = False
        for plan in plans:
            if plan.resource_type != "sheet":
                plan.warnings.append(
                    f"unsupported_worksheet_resource_type:{plan.resource_type}"
                )
                continue
            rows = plan.grid_properties.row_count
            columns = plan.grid_properties.column_count
            if rows > self._max_rows_per_worksheet:
                plan.warnings.append(
                    f"row_limit_exceeded:{rows}>{self._max_rows_per_worksheet}"
                )
                rows = self._max_rows_per_worksheet
            if columns > self._max_columns_per_worksheet:
                plan.warnings.append(
                    "column_limit_exceeded:"
                    f"{columns}>{self._max_columns_per_worksheet}"
                )
                columns = self._max_columns_per_worksheet
            if rows == 0 or columns == 0:
                continue
            requested_cells = rows * columns
            if requested_cells > remaining_cells:
                if not total_limit_reported:
                    global_warnings.append(
                        f"total_cell_limit_exceeded:{self._max_total_cells}"
                    )
                    total_limit_reported = True
                plan.warnings.append("total_cell_budget_truncated")
                if remaining_cells == 0:
                    continue
                if columns > remaining_cells:
                    columns = remaining_cells
                    rows = 1
                else:
                    rows = remaining_cells // columns
                requested_cells = rows * columns
            if requested_cells == 0:
                continue
            plan.requested_range = (
                f"{plan.sheet_id}!A1:{_column_name(columns)}{rows}"
            )
            plan.requested_cell_count = requested_cells
            remaining_cells -= requested_cells

    async def _get_value_ranges(
        self,
        spreadsheet_token: str,
        headers: dict[str, str],
        ranges: list[str],
    ) -> tuple[list[dict[str, Any] | None], set[str]]:
        if not ranges:
            return [], set()
        results: list[dict[str, Any] | None] = []
        revisions: set[str] = set()
        for start in range(0, len(ranges), self._max_ranges_per_request):
            batch = ranges[start : start + self._max_ranges_per_request]
            response = await self._request(
                VALUES_BATCH_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
                headers=headers,
                params={
                    "ranges": ",".join(batch),
                    "valueRenderOption": "Formula",
                    "dateTimeRenderOption": "FormattedString",
                    "user_id_type": "open_id",
                },
                operation="Sheets values read",
                enforce_response_limit=True,
            )
            data = data_object(response, "sheets_values")
            batch_revision = optional_text(data.get("revision"))
            if batch_revision is not None:
                revisions.add(batch_revision)
            raw_ranges = data.get("valueRanges")
            if not isinstance(raw_ranges, list) or any(
                not isinstance(item, dict) for item in raw_ranges
            ):
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "Feishu returned an invalid Sheets value range list.",
                )
            if len(raw_ranges) > len(batch):
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "Feishu returned more value ranges than requested.",
                )
            results.extend(raw_ranges)
            results.extend([None] * (len(batch) - len(raw_ranges)))
            for value_range in raw_ranges:
                value_revision = optional_text(value_range.get("revision"))
                if value_revision is not None:
                    revisions.add(value_revision)
        return results, revisions

    async def _request(
        self,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        operation: str,
        enforce_response_limit: bool = False,
    ) -> httpx.Response:
        try:
            response = await self._http.get(
                f"{self._origin}{path}",
                headers=headers,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise http_error(response, operation)
        if enforce_response_limit and len(response.content) > self._max_response_bytes:
            raise CapabilityError(
                CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
                "Feishu Sheets values exceeded the Provider response safety limit.",
                details={"max_response_bytes": self._max_response_bytes},
            )
        return response


def _worksheet_plan(raw: dict[str, Any]) -> _WorksheetPlan:
    sheet_id = required_text(raw.get("sheet_id"), "sheet_id", "worksheet")
    if not _SHEET_ID.fullmatch(sheet_id):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "Feishu returned an invalid worksheet identifier.",
        )
    title = required_text(raw.get("title"), "title", "worksheet")
    index = _nonnegative_int(raw.get("index"), "worksheet index")
    hidden = raw.get("hidden", False)
    if not isinstance(hidden, bool):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "Feishu returned an invalid worksheet hidden value.",
        )
    resource_type = optional_text(raw.get("resource_type")) or "sheet"
    grid = raw.get("grid_properties")
    if not isinstance(grid, dict):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "Feishu returned no grid properties for a worksheet.",
        )
    grid_properties = GridProperties(
        frozen_row_count=_nonnegative_int(
            grid.get("frozen_row_count", 0), "frozen row count"
        ),
        frozen_column_count=_nonnegative_int(
            grid.get("frozen_column_count", 0), "frozen column count"
        ),
        row_count=_nonnegative_int(grid.get("row_count"), "row count"),
        column_count=_nonnegative_int(grid.get("column_count"), "column count"),
    )
    merges: list[MergeRange] = []
    warnings: list[str] = []
    raw_merges = raw.get("merges", [])
    if not isinstance(raw_merges, list):
        warnings.append("malformed_merge_ranges:1")
    else:
        malformed_merges = 0
        for raw_merge in raw_merges:
            try:
                merge = _merge_range(raw_merge)
            except (CapabilityError, TypeError, ValueError):
                malformed_merges += 1
                continue
            merges.append(merge)
        if malformed_merges:
            warnings.append(f"malformed_merge_ranges:{malformed_merges}")
    return _WorksheetPlan(
        sheet_id=sheet_id,
        title=title,
        index=index,
        hidden=hidden,
        resource_type=resource_type,
        grid_properties=grid_properties,
        merges=merges,
        warnings=warnings,
    )


def _merge_range(raw: object) -> MergeRange:
    if not isinstance(raw, dict):
        raise TypeError("merge range is not an object")
    merge = MergeRange(
        start_row_index=_nonnegative_int(
            raw.get("start_row_index"), "merge start row"
        ),
        end_row_index=_nonnegative_int(raw.get("end_row_index"), "merge end row"),
        start_column_index=_nonnegative_int(
            raw.get("start_column_index"), "merge start column"
        ),
        end_column_index=_nonnegative_int(
            raw.get("end_column_index"), "merge end column"
        ),
    )
    # Feishu returns inclusive merge endpoints. A horizontal merge therefore has
    # equal start/end row indexes, and a vertical merge has equal column indexes.
    if (
        merge.end_row_index < merge.start_row_index
        or merge.end_column_index < merge.start_column_index
    ):
        raise ValueError("merge range is reversed")
    return merge


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            f"Feishu returned an invalid {field}.",
        )
    return value


def _values(value_range: dict[str, Any]) -> list[list[Any]]:
    raw_values = value_range.get("values", [])
    if not isinstance(raw_values, list) or any(
        not isinstance(row, list) for row in raw_values
    ):
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "Feishu returned invalid worksheet values.",
        )
    return raw_values


def _known_complex_cell(value: dict[str, Any] | list[Any]) -> bool:
    if isinstance(value, dict):
        return _known_complex_segment(value)
    return all(
        isinstance(segment, dict) and _known_complex_segment(segment)
        for segment in value
    )


def _known_complex_segment(segment: dict[str, Any]) -> bool:
    type_marker = segment.get("type")
    return isinstance(type_marker, str) and type_marker in _KNOWN_COMPLEX_CELL_TYPES


def _column_name(column_count: int) -> str:
    if column_count < 1:
        raise ValueError("column_count must be positive")
    result = ""
    current = column_count
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
