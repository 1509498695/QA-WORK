from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import parse_qsl, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.models import (
    OperationEvidence,
    OperationStatus,
)
from feishu_protocol import (
    SHEETS_MEDIA_READ_CAPABILITY,
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
from feishu_provider.locator import (
    ResourceLocator,
    ResourceType,
    TargetKind,
    classify_locator,
)


SPREADSHEET_ENDPOINT = "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}"
SHEETS_QUERY_ENDPOINT = (
    "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
)
VALUES_BATCH_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get"
)
MEDIA_DOWNLOAD_ENDPOINT = "/open-apis/drive/v1/medias/{file_token}/download"
MEDIA_TEMP_URLS_ENDPOINT = (
    "/open-apis/drive/v1/medias/batch_get_tmp_download_url"
)

DEFAULT_MAX_WORKSHEETS = 100
DEFAULT_MAX_ROWS_PER_WORKSHEET = 5_000
DEFAULT_MAX_COLUMNS_PER_WORKSHEET = 500
DEFAULT_MAX_TOTAL_CELLS = 200_000
DEFAULT_MAX_RANGES_PER_REQUEST = 20
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ASSET_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ASSET_COUNT = 64
DEFAULT_MEDIA_DOWNLOAD_INTERVAL_SECONDS = 0.21
MAX_MEDIA_TOKENS_PER_TEMP_URL_REQUEST = 5
_SHEET_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_KNOWN_COMPLEX_CELL_TYPES = {"text", "mention", "url", "formula"}
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
_SHEET_IMAGE_LINK_HOST = "internal-api-drive-stream.feishu.cn"
_SHEET_IMAGE_LINK_PATH = re.compile(
    r"^/space/api/box/stream/download/v2/cover/[A-Za-z0-9_-]{6,256}/$"
)
_SHEET_IMAGE_LINK_QUERY_KEYS = {
    "height",
    "mount_node_token",
    "mount_point",
    "policy",
    "width",
}
_TEMP_DOWNLOAD_HOSTS = {
    "internal-api-drive-stream.feishu.cn",
    "internal-api-drive-stream-hl.feishu.cn",
}
_TEMP_DOWNLOAD_PATH = "/space/api/box/stream/download/authcode/"


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


class SheetImageAssetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sheet_id: str
    worksheet_title: str
    cell: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    asset_kind: Literal["image"] = "image"
    source_type: Literal["embed-image"] = "embed-image"
    file_token: str
    width_px: float | None = Field(default=None, gt=0)
    height_px: float | None = Field(default=None, gt=0)
    media_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    content_base64: str | None = Field(default=None, repr=False)
    retrieval_method: Literal["temporary_url", "media_api", "cell_link"] | None = (
        None
    )
    retrieval_complete: bool
    warning: str | None = None


class SheetsReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = "feishu"
    provider_version: str = "0.7.0"
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
    assets: list[SheetImageAssetSnapshot] = Field(default_factory=list)
    asset_count: int = Field(default=0, ge=0)
    asset_total_bytes: int = Field(default=0, ge=0)
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


@dataclass(frozen=True, slots=True)
class _SheetImageDescriptor:
    sheet_id: str
    worksheet_title: str
    cell: str
    row_index: int
    column_index: int
    file_token: str
    width_px: float | None
    height_px: float | None
    download_link: str | None


@dataclass(frozen=True, slots=True)
class _DownloadedMedia:
    media_type: str | None = None
    byte_count: int | None = None
    content_hash: str | None = None
    content_base64: str | None = None
    retrieval_method: Literal["temporary_url", "media_api", "cell_link"] | None = (
        None
    )
    retrieval_complete: bool = False
    warning: str | None = None


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
        max_asset_bytes: int = DEFAULT_MAX_ASSET_BYTES,
        max_total_asset_bytes: int = DEFAULT_MAX_TOTAL_ASSET_BYTES,
        max_asset_count: int = DEFAULT_MAX_ASSET_COUNT,
        media_download_interval_seconds: float = (
            DEFAULT_MEDIA_DOWNLOAD_INTERVAL_SECONDS
        ),
    ) -> None:
        limits = (
            max_worksheets,
            max_rows_per_worksheet,
            max_columns_per_worksheet,
            max_total_cells,
            max_ranges_per_request,
            max_response_bytes,
            max_asset_bytes,
            max_total_asset_bytes,
            max_asset_count,
        )
        if any(limit < 1 for limit in limits):
            raise ValueError("Sheets read limits must be positive")
        if not 0 <= media_download_interval_seconds <= 10:
            raise ValueError("Media download interval must be between 0 and 10 seconds")
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
        self._max_asset_bytes = max_asset_bytes
        self._max_total_asset_bytes = max_total_asset_bytes
        self._max_asset_count = max_asset_count
        self._media_download_interval_seconds = media_download_interval_seconds

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
        image_descriptors: list[_SheetImageDescriptor] = []
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
                    (
                        sheet_image_descriptors,
                        malformed_embed_images,
                        unsupported_complex_cells,
                    ) = _inspect_sheet_cells(
                        values,
                        sheet_id=plan.sheet_id,
                        worksheet_title=plan.title,
                    )
                    image_descriptors.extend(sheet_image_descriptors)
                    if malformed_embed_images:
                        plan.warnings.append(
                            f"malformed_embed_image_cells:{malformed_embed_images}"
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

        assets, asset_total_bytes = await self._get_sheet_image_assets(
            image_descriptors,
            task_ref=task_ref,
            profile_ref=resolved_profile_ref,
        )
        asset_warning_counts = Counter(
            (asset.sheet_id, asset.warning)
            for asset in assets
            if asset.warning is not None
        )
        if asset_warning_counts:
            worksheets = [
                _worksheet_with_asset_warnings(worksheet, asset_warning_counts)
                for worksheet in worksheets
            ]

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
                "assets": [
                    asset.model_dump(mode="json", exclude={"content_base64"})
                    for asset in assets
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
            assets=assets,
            asset_count=len(assets),
            asset_total_bytes=asset_total_bytes,
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

    async def _get_sheet_image_assets(
        self,
        descriptors: list[_SheetImageDescriptor],
        *,
        task_ref: str,
        profile_ref: str,
    ) -> tuple[list[SheetImageAssetSnapshot], int]:
        if not descriptors:
            return [], 0

        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=(SHEETS_MEDIA_READ_CAPABILITY,),
        )
        headers = {
            "Authorization": f"Bearer {lease.access_token}",
            "Accept": "*/*",
        }
        unique_tokens = list(dict.fromkeys(item.file_token for item in descriptors))
        download_links = {
            descriptor.file_token: descriptor.download_link
            for descriptor in descriptors
            if descriptor.download_link is not None
        }
        downloadable_tokens = unique_tokens[: self._max_asset_count]
        temporary_urls = await self._get_media_temporary_urls(
            downloadable_tokens,
            headers=headers,
        )
        downloads: dict[str, _DownloadedMedia] = {}
        remaining_total = self._max_total_asset_bytes
        downloaded_total = 0
        downloaded_once = False
        for index, file_token in enumerate(unique_tokens):
            if index >= self._max_asset_count:
                downloads[file_token] = _DownloadedMedia(
                    warning="asset_count_limit_exceeded"
                )
                continue
            if remaining_total <= 0:
                downloads[file_token] = _DownloadedMedia(
                    warning="asset_total_size_limit_exceeded"
                )
                continue
            if downloaded_once and self._media_download_interval_seconds:
                await asyncio.sleep(self._media_download_interval_seconds)
            allowed_bytes = min(self._max_asset_bytes, remaining_total)
            limit_warning = (
                "asset_total_size_limit_exceeded"
                if remaining_total < self._max_asset_bytes
                else "asset_size_limit_exceeded"
            )
            media = await self._download_sheet_image(
                file_token,
                temporary_url=temporary_urls.get(file_token),
                download_link=download_links.get(file_token),
                headers=headers,
                allowed_bytes=allowed_bytes,
                limit_warning=limit_warning,
            )
            downloaded_once = True
            downloads[file_token] = media
            if media.retrieval_complete and media.byte_count is not None:
                remaining_total -= media.byte_count
                downloaded_total += media.byte_count

        return [
            _sheet_image_asset(descriptor, downloads[descriptor.file_token])
            for descriptor in descriptors
        ], downloaded_total

    async def _get_media_temporary_urls(
        self,
        file_tokens: list[str],
        *,
        headers: dict[str, str],
    ) -> dict[str, str]:
        temporary_urls: dict[str, str] = {}
        requested_once = False
        for start in range(
            0,
            len(file_tokens),
            MAX_MEDIA_TOKENS_PER_TEMP_URL_REQUEST,
        ):
            if requested_once and self._media_download_interval_seconds:
                await asyncio.sleep(self._media_download_interval_seconds)
            batch = file_tokens[
                start : start + MAX_MEDIA_TOKENS_PER_TEMP_URL_REQUEST
            ]
            response = await self._request(
                MEDIA_TEMP_URLS_ENDPOINT,
                headers=headers,
                params=[("file_tokens", file_token) for file_token in batch],
                operation="Sheets image temporary URL lookup",
            )
            requested_once = True
            data = data_object(response, "sheets_image_temporary_urls")
            raw_urls = data.get("tmp_download_urls")
            if not isinstance(raw_urls, list) or any(
                not isinstance(item, dict) for item in raw_urls
            ):
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "Feishu returned an invalid image temporary URL list.",
                )
            if len(raw_urls) > len(batch):
                raise CapabilityError(
                    CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                    "Feishu returned more image temporary URLs than requested.",
                )
            for item in raw_urls:
                file_token = item.get("file_token")
                temporary_url = _safe_media_temporary_url(
                    item.get("tmp_download_url")
                )
                if (
                    not isinstance(file_token, str)
                    or file_token not in batch
                    or file_token in temporary_urls
                    or temporary_url is None
                ):
                    raise CapabilityError(
                        CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
                        "Feishu returned an untrusted image temporary URL contract.",
                    )
                temporary_urls[file_token] = temporary_url
        return temporary_urls

    async def _download_sheet_image(
        self,
        file_token: str,
        *,
        temporary_url: str | None,
        download_link: str | None,
        headers: dict[str, str],
        allowed_bytes: int,
        limit_warning: str,
    ) -> _DownloadedMedia:
        if temporary_url is not None:
            try:
                return await self._download_sheet_image_url(
                    temporary_url,
                    headers={"Accept": "*/*"},
                    allowed_bytes=allowed_bytes,
                    limit_warning=limit_warning,
                    retrieval_method="temporary_url",
                    operation="Sheets temporary image download",
                )
            except CapabilityError as error:
                if error.code not in {
                    CapabilityErrorCode.AUTH_REQUIRED,
                    CapabilityErrorCode.PERMISSION_DENIED,
                    CapabilityErrorCode.RESOURCE_NOT_FOUND,
                }:
                    raise
        media_url = (
            f"{self._origin}"
            f"{MEDIA_DOWNLOAD_ENDPOINT.format(file_token=file_token)}"
        )
        try:
            return await self._download_sheet_image_url(
                media_url,
                headers=headers,
                allowed_bytes=allowed_bytes,
                limit_warning=limit_warning,
                retrieval_method="media_api",
                operation="Sheets image download",
            )
        except CapabilityError as error:
            if (
                download_link is None
                or error.code
                not in {
                    CapabilityErrorCode.PERMISSION_DENIED,
                    CapabilityErrorCode.RESOURCE_NOT_FOUND,
                }
            ):
                raise
        return await self._download_sheet_image_url(
            download_link,
            headers=headers,
            allowed_bytes=allowed_bytes,
            limit_warning=limit_warning,
            retrieval_method="cell_link",
            operation="Sheets cell image download",
        )

    async def _download_sheet_image_url(
        self,
        url: str,
        *,
        headers: dict[str, str],
        allowed_bytes: int,
        limit_warning: str,
        retrieval_method: Literal["temporary_url", "media_api", "cell_link"],
        operation: str,
    ) -> _DownloadedMedia:
        request_headers = {
            **headers,
            "Range": f"bytes=0-{allowed_bytes}",
        }
        try:
            async with self._http.stream(
                "GET",
                url,
                headers=request_headers,
            ) as response:
                if response.status_code not in {200, 206}:
                    await response.aread()
                    raise http_error(response, operation)
                declared_size = _declared_asset_size(response)
                media_type = _media_type(response.headers.get("content-type"))
                if declared_size is not None and declared_size > allowed_bytes:
                    return _DownloadedMedia(
                        media_type=media_type,
                        byte_count=declared_size,
                        retrieval_method=retrieval_method,
                        warning=limit_warning,
                    )
                chunks: list[bytes] = []
                downloaded_size = 0
                async for chunk in response.aiter_bytes():
                    downloaded_size += len(chunk)
                    if downloaded_size > allowed_bytes:
                        return _DownloadedMedia(
                            media_type=media_type,
                            byte_count=declared_size,
                            retrieval_method=retrieval_method,
                            warning=limit_warning,
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
                if not _response_contains_complete_asset(response, len(content)):
                    return _DownloadedMedia(
                        media_type=media_type,
                        byte_count=declared_size,
                        retrieval_method=retrieval_method,
                        warning="asset_partial_response",
                    )
        except CapabilityError:
            raise
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc

        return _DownloadedMedia(
            media_type=media_type,
            byte_count=len(content),
            content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
            content_base64=base64.b64encode(content).decode("ascii"),
            retrieval_method=retrieval_method,
            retrieval_complete=True,
        )

    async def _request(
        self,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | list[tuple[str, str]] | None = None,
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


def _inspect_sheet_cells(
    values: list[list[Any]],
    *,
    sheet_id: str,
    worksheet_title: str,
) -> tuple[list[_SheetImageDescriptor], int, int]:
    descriptors: list[_SheetImageDescriptor] = []
    malformed_embed_images = 0
    unsupported_complex_cells = 0
    for row_index, row in enumerate(values):
        for column_index, cell in enumerate(row):
            if isinstance(cell, dict) and cell.get("type") == "embed-image":
                try:
                    descriptors.append(
                        _embed_image_descriptor(
                            cell,
                            sheet_id=sheet_id,
                            worksheet_title=worksheet_title,
                            row_index=row_index,
                            column_index=column_index,
                        )
                    )
                except (TypeError, ValueError):
                    malformed_embed_images += 1
                continue
            if isinstance(cell, (dict, list)) and not _known_complex_cell(cell):
                unsupported_complex_cells += 1
    return descriptors, malformed_embed_images, unsupported_complex_cells


def _embed_image_descriptor(
    cell: dict[str, Any],
    *,
    sheet_id: str,
    worksheet_title: str,
    row_index: int,
    column_index: int,
) -> _SheetImageDescriptor:
    file_token = cell.get("fileToken")
    if not isinstance(file_token, str) or not RESOLVED_OBJECT_TOKEN.fullmatch(
        file_token
    ):
        raise ValueError("invalid embedded image token")
    return _SheetImageDescriptor(
        sheet_id=sheet_id,
        worksheet_title=worksheet_title,
        cell=f"{_column_name(column_index + 1)}{row_index + 1}",
        row_index=row_index,
        column_index=column_index,
        file_token=file_token,
        width_px=_positive_dimension(cell.get("width")),
        height_px=_positive_dimension(cell.get("height")),
        download_link=_safe_sheet_image_link(cell.get("link")),
    )


def _positive_dimension(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("image dimension is not numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("image dimension is not positive and finite")
    return normalized


def _safe_media_temporary_url(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
        )
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _TEMP_DOWNLOAD_HOSTS
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != _TEMP_DOWNLOAD_PATH
        or parsed.fragment
        or len(query_pairs) != 1
        or query_pairs[0][0] != "code"
    ):
        return None
    code = query_pairs[0][1]
    if (
        not 16 <= len(code) <= 3072
        or any(ord(character) < 33 or ord(character) > 126 for character in code)
    ):
        return None
    return value


def _safe_sheet_image_link(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(_SHEET_IMAGE_LINK_QUERY_KEYS),
        )
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != _SHEET_IMAGE_LINK_HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not _SHEET_IMAGE_LINK_PATH.fullmatch(parsed.path)
        or parsed.fragment
        or len(query_pairs) != len(_SHEET_IMAGE_LINK_QUERY_KEYS)
        or {key for key, _ in query_pairs} != _SHEET_IMAGE_LINK_QUERY_KEYS
    ):
        return None
    query = dict(query_pairs)
    if (
        query.get("mount_point") != "sheet_image"
        or query.get("policy") != "equal"
        or not _valid_image_link_dimension(query.get("width"))
        or not _valid_image_link_dimension(query.get("height"))
    ):
        return None
    mount_node_token = query.get("mount_node_token")
    if (
        not isinstance(mount_node_token, str)
        or not RESOLVED_OBJECT_TOKEN.fullmatch(mount_node_token)
    ):
        return None
    return value


def _valid_image_link_dimension(value: str | None) -> bool:
    return value is not None and value.isdigit() and 1 <= int(value) <= 8192


def _sheet_image_asset(
    descriptor: _SheetImageDescriptor,
    media: _DownloadedMedia,
) -> SheetImageAssetSnapshot:
    return SheetImageAssetSnapshot(
        sheet_id=descriptor.sheet_id,
        worksheet_title=descriptor.worksheet_title,
        cell=descriptor.cell,
        row_index=descriptor.row_index,
        column_index=descriptor.column_index,
        file_token=descriptor.file_token,
        width_px=descriptor.width_px,
        height_px=descriptor.height_px,
        media_type=media.media_type,
        byte_count=media.byte_count,
        content_hash=media.content_hash,
        content_base64=media.content_base64,
        retrieval_method=media.retrieval_method,
        retrieval_complete=media.retrieval_complete,
        warning=media.warning,
    )


def _worksheet_with_asset_warnings(
    worksheet: WorksheetSnapshot,
    warning_counts: Counter[tuple[str, str]],
) -> WorksheetSnapshot:
    additions = [
        f"{warning}:{count}"
        for (sheet_id, warning), count in sorted(warning_counts.items())
        if sheet_id == worksheet.sheet_id
    ]
    if not additions:
        return worksheet
    return worksheet.model_copy(
        update={
            "retrieval_complete": False,
            "warnings": (*worksheet.warnings, *additions),
        }
    )


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


def _declared_asset_size(response: httpx.Response) -> int | None:
    content_range = response.headers.get("content-range")
    if content_range:
        match = _CONTENT_RANGE.fullmatch(content_range.strip())
        if match is not None and match.group(3) != "*":
            return int(match.group(3))
    if response.status_code == 200:
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit():
            return int(content_length)
    return None


def _response_contains_complete_asset(
    response: httpx.Response,
    downloaded_size: int,
) -> bool:
    if response.status_code == 200:
        declared_size = _declared_asset_size(response)
        return declared_size is None or declared_size == downloaded_size
    content_range = response.headers.get("content-range")
    if not content_range:
        return False
    match = _CONTENT_RANGE.fullmatch(content_range.strip())
    if match is None or match.group(3) == "*":
        return False
    start, end, total = map(int, match.groups())
    return start == 0 and end + 1 == total == downloaded_size


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    return _safe_metadata_text(value.split(";", 1)[0], max_length=255)


def _safe_metadata_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
    ):
        return None
    return normalized


def _column_name(column_count: int) -> str:
    if column_count < 1:
        raise ValueError("column_count must be positive")
    result = ""
    current = column_count
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
