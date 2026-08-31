from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_protocol import (
    SHEETS_EXPORT_VERIFY_CAPABILITY,
    SHEETS_MANAGED_WRITE_CAPABILITY,
    SHEETS_READ_CAPABILITY,
    SHEETS_TYPED_VALUES_WRITE_CAPABILITY,
    WIKI_CHILD_LIST_CAPABILITY,
    WIKI_NODE_CREATE_CAPABILITY,
    WIKI_NODE_READ_CAPABILITY,
)

from feishu_provider.common import (
    OPEN_API_ORIGIN,
    RESOLVED_OBJECT_TOKEN,
    WIKI_NODE_ENDPOINT,
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
from feishu_provider.managed_sheets import (
    ExecutionProof,
    RecoveryProgress,
    RemoteMutationFailure,
    RemotePreview,
    StepCallback,
    TargetCallback,
    WorkbookCallback,
)
from feishu_provider.operation_store import (
    REVISION_STEP_ORDER,
    WRITE_STEP_ORDER,
    ManagedSheetRegistration,
    OperationRecord,
    ProtectedTarget,
    RevisionRecord,
    RevisionStep,
    WriteStep,
)
from feishu_provider.sheet_delivery import (
    BorderType,
    CellStyle,
    GridRange,
    HorizontalAlignment,
    PlacementMode,
    SheetDeliverySpec,
    VerticalAlignment,
    validate_sheet_title,
    validate_workbook_title,
)
from feishu_provider.sheet_revision import (
    ResolvedWorkbook,
    ResolvedWorksheet,
    RevisionDiffSummary,
    RevisionProof,
    RevisionStepCallback,
    revision_managed_ranges,
    revision_retired_ranges,
)
from feishu_provider.xlsx_verify import MAX_XLSX_BYTES, verify_sheet_export

SPREADSHEET_ENDPOINT = "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}"
SHEETS_QUERY_ENDPOINT = (
    "/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
)
VALUES_BATCH_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_get"
)
VALUES_ENDPOINT = "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
TYPED_VALUES_ENDPOINT = (
    "/open-apis/sheet_ai/v2/spreadsheets/{spreadsheet_token}/tools/invoke_write"
)
SHEETS_BATCH_UPDATE_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/sheets_batch_update"
)
STYLES_BATCH_UPDATE_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/styles_batch_update"
)
DIMENSION_RANGE_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/dimension_range"
)
MERGE_CELLS_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/merge_cells"
)
UNMERGE_CELLS_ENDPOINT = (
    "/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/unmerge_cells"
)
EXPORT_TASKS_ENDPOINT = "/open-apis/drive/v1/export_tasks"
EXPORT_TASK_ENDPOINT = "/open-apis/drive/v1/export_tasks/{ticket}"
EXPORT_DOWNLOAD_ENDPOINT = (
    "/open-apis/drive/v1/export_tasks/file/{file_token}/download"
)
WIKI_CHILDREN_ENDPOINT = "/open-apis/wiki/v2/spaces/{space_id}/nodes"

MAX_WORKSHEETS = 100
MAX_WIKI_CHILDREN = 1_000
MAX_WIKI_CHILD_PAGES = 100
MAX_BLANK_CHECK_CELLS = 200_000
MAX_GRID_ROWS = 5_000
MAX_GRID_COLUMNS = 500
MAX_JSON_RESPONSE_BYTES = 10 * 1024 * 1024
STYLE_BATCH_ITEMS = 10
_SHEET_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True, slots=True)
class _RemoteWorksheet:
    sheet_id: str
    title: str
    index: int
    hidden: bool
    resource_type: str
    row_count: int
    column_count: int
    frozen_row_count: int
    frozen_column_count: int
    merges: tuple[GridRange, ...]


@dataclass(frozen=True, slots=True)
class _ValueSnapshot:
    values: tuple[tuple[Any, ...], ...]
    revision: str | None


@dataclass(frozen=True, slots=True)
class _ApiProof:
    content_hash: str
    revision: str | None


@dataclass(frozen=True, slots=True)
class _WikiNode:
    space_id: str
    node_token: str
    object_token: str
    object_type: str
    parent_node_token: str | None
    node_type: str
    title: str
    has_child: bool


class FeishuManagedSheetsGateway:
    def __init__(
        self,
        *,
        lease_client: LeaseClient,
        http_client: httpx.AsyncClient | None = None,
        open_api_origin: str = OPEN_API_ORIGIN,
        timeout_seconds: float = 20.0,
        export_poll_attempts: int = 20,
        export_poll_interval_seconds: float = 0.5,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if export_poll_attempts < 1:
            raise ValueError("export_poll_attempts must be positive")
        if not 0 <= export_poll_interval_seconds <= 10:
            raise ValueError("export poll interval must be between 0 and 10 seconds")
        self._lease_client = lease_client
        self._origin = open_api_origin.rstrip("/")
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._export_poll_attempts = export_poll_attempts
        self._export_poll_interval_seconds = export_poll_interval_seconds
        self._sleeper = sleeper

    @classmethod
    def default(cls) -> FeishuManagedSheetsGateway:
        return cls(lease_client=LoopbackLeaseClient.default())

    async def preview(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
        placement_mode: PlacementMode,
        requested_sheet_title: str | None,
        spec: SheetDeliverySpec,
        requested_workbook_title: str | None = None,
    ) -> RemotePreview:
        source = _sheet_locator(locator)
        requested_sheet = (
            validate_sheet_title(requested_sheet_title)
            if placement_mode is PlacementMode.CREATE_NEW_SHEET
            else None
        )
        requested_workbook = (
            validate_workbook_title(requested_workbook_title)
            if placement_mode is PlacementMode.CREATE_NEW_WORKBOOK
            else None
        )
        if (
            placement_mode is PlacementMode.ADOPT_BLANK_SHEET
            and requested_sheet_title is not None
        ):
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
                "Blank-sheet adoption does not accept a replacement worksheet title.",
            )
        if (
            placement_mode is PlacementMode.CREATE_NEW_WORKBOOK
            and requested_sheet_title is not None
        ):
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
                "New-workbook delivery uses the automatically created default worksheet and does not accept requested_sheet_title.",
            )
        if (
            placement_mode is not PlacementMode.CREATE_NEW_WORKBOOK
            and requested_workbook_title is not None
        ):
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
                "requested_workbook_title is accepted only for create_new_workbook.",
            )
        if placement_mode is PlacementMode.CREATE_NEW_WORKBOOK:
            if source.resource_type is not ResourceType.FEISHU_WIKI:
                raise CapabilityError(
                    CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                    "New-workbook delivery requires an exact Feishu Wiki parent-node URL.",
                )
            if source.worksheet_id is not None:
                raise CapabilityError(
                    CapabilityErrorCode.INVALID_LOCATOR,
                    "A Wiki parent-node locator for new-workbook delivery cannot contain a sheet selector.",
                )
        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=_capabilities(source, spec, placement_mode),
        )
        headers = _headers(lease.access_token)
        if placement_mode is PlacementMode.CREATE_NEW_WORKBOOK:
            assert requested_workbook is not None
            parent = await self._wiki_parent(source, headers)
            children = await self._wiki_children(parent, headers)
            _ensure_unique_workbook_title(children, requested_workbook)
            target = ProtectedTarget(
                source_locator=source.original,
                workbook_title=requested_workbook,
                requested_workbook_title=requested_workbook,
                wiki_space_id=parent.space_id,
                parent_wiki_node_token=parent.node_token,
                parent_wiki_title=parent.title,
                initial_sheet_count=0,
                initial_child_count=len(children),
                initial_state_hash=_wiki_parent_state_hash(parent, children),
            )
            return RemotePreview(
                source=source,
                profile_ref=lease.profile_ref,
                target=target,
                observed_at=datetime.now(UTC),
                warnings=("new_workbook_default_worksheet_title_resolved_after_create",),
            )

        spreadsheet_token = await self._resolve_spreadsheet_token(source, headers)
        metadata = await self._spreadsheet_metadata(spreadsheet_token, headers)
        worksheets = await self._worksheets(spreadsheet_token, headers)
        workbook_title = required_text(
            metadata.get("title"), "title", "spreadsheet metadata"
        )
        workbook_url = optional_text(metadata.get("url")) or source.canonical_url
        observed_at = datetime.now(UTC)
        warnings: list[str] = []

        if placement_mode is PlacementMode.CREATE_NEW_SHEET:
            assert requested_sheet is not None
            _ensure_unique_title(worksheets, requested_sheet)
            if source.worksheet_id is not None:
                warnings.append("worksheet_selector_ignored_for_create")
            target = ProtectedTarget(
                source_locator=source.original,
                spreadsheet_token=spreadsheet_token,
                workbook_title=workbook_title,
                workbook_url=workbook_url,
                worksheet_selector=source.worksheet_id,
                requested_sheet_title=requested_sheet,
                initial_revision=None,
                initial_sheet_count=len(worksheets),
                initial_state_hash=_create_precondition_hash(
                    requested_sheet, worksheets
                ),
            )
        else:
            selected = _select_adoption_target(worksheets, source.worksheet_id)
            values = await self._values(
                spreadsheet_token,
                selected,
                headers,
                require_bounded_full_grid=True,
            )
            _ensure_content_blank(selected, values)
            target = ProtectedTarget(
                source_locator=source.original,
                spreadsheet_token=spreadsheet_token,
                workbook_title=workbook_title,
                workbook_url=workbook_url,
                worksheet_selector=source.worksheet_id,
                sheet_id=selected.sheet_id,
                sheet_title=selected.title,
                sheet_index=selected.index,
                initial_revision=values.revision,
                initial_sheet_count=len(worksheets),
                initial_state_hash=_adoption_state_hash(selected, values),
            )
            warnings.append("non_atomic_blank_sheet_adoption")

        return RemotePreview(
            source=source,
            profile_ref=lease.profile_ref,
            target=target,
            observed_at=observed_at,
            warnings=tuple(warnings),
        )

    async def resolve_workbook(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> ResolvedWorkbook:
        source = _sheet_locator(locator)
        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=profile_ref,
            capabilities=_registration_resolve_capabilities(source),
        )
        headers = _headers(lease.access_token)
        spreadsheet_token = await self._resolve_spreadsheet_token(source, headers)
        metadata = await self._spreadsheet_metadata(spreadsheet_token, headers)
        worksheets = await self._worksheets(spreadsheet_token, headers)
        return ResolvedWorkbook(
            source=source,
            profile_ref=lease.profile_ref,
            spreadsheet_token=spreadsheet_token,
            workbook_title=required_text(
                metadata.get("title"), "title", "spreadsheet metadata"
            ),
            workbook_url=optional_text(metadata.get("url")) or source.canonical_url,
            worksheets=tuple(
                ResolvedWorksheet(
                    sheet_id=item.sheet_id,
                    title=item.title,
                    index=item.index,
                    hidden=item.hidden,
                    resource_type=item.resource_type,
                )
                for item in worksheets
            ),
            observed_at=datetime.now(UTC),
        )

    async def verify_revision_baseline(
        self,
        *,
        registration: ManagedSheetRegistration,
        task_ref: str,
        spec: SheetDeliverySpec,
    ) -> RevisionProof:
        lease = await self._lease_client.issue(
            task_ref=task_ref,
            profile_ref=registration.profile_ref,
            capabilities=(
                SHEETS_EXPORT_VERIFY_CAPABILITY,
                SHEETS_READ_CAPABILITY,
            ),
        )
        _require_profile_binding(lease.profile_ref, registration.profile_ref)
        headers = _headers(lease.access_token)
        target = await self._refresh_managed_target(
            registration.target,
            headers,
            allow_display_metadata_change=True,
        )
        api_proof = await self._verify_api(target, spec, headers)
        export_hash = await self._verify_export(target, spec, headers)
        return RevisionProof(
            target=target,
            api_hash=api_proof.content_hash,
            export_hash=export_hash,
            remote_revision=api_proof.revision,
            observed_at=datetime.now(UTC),
            warnings=("baseline_double_verified",),
        )

    async def execute_revision(
        self,
        *,
        record: RevisionRecord,
        registration: ManagedSheetRegistration,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        diff: RevisionDiffSummary,
        on_step: RevisionStepCallback,
    ) -> RevisionProof:
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=registration.profile_ref,
            capabilities=_delivery_capabilities(next_spec),
        )
        _require_profile_binding(lease.profile_ref, registration.profile_ref)
        headers = _headers(lease.access_token)
        target = await self._refresh_managed_target(
            registration.target,
            headers,
            allow_display_metadata_change=False,
        )
        last_step = record.last_completed_step
        worksheet = await self._worksheet_by_id(target, headers)
        if _revision_before(last_step, RevisionStep.GRID_EXTENDED):
            revision = await self._extend_grid(target, worksheet, next_spec, headers)
            on_step(RevisionStep.GRID_EXTENDED, revision)
            last_step = RevisionStep.GRID_EXTENDED
        if _revision_before(last_step, RevisionStep.BASE_MERGES_REMOVED):
            revision = await self._remove_base_merges(
                target,
                base_spec,
                next_spec,
                headers,
            )
            on_step(RevisionStep.BASE_MERGES_REMOVED, revision)
            last_step = RevisionStep.BASE_MERGES_REMOVED
        if _revision_before(last_step, RevisionStep.NEXT_VALUES_WRITTEN):
            revision = await self._write_values(target, next_spec, headers)
            on_step(RevisionStep.NEXT_VALUES_WRITTEN, revision)
            last_step = RevisionStep.NEXT_VALUES_WRITTEN
        if _revision_before(last_step, RevisionStep.RETIRED_VALUES_CLEARED):
            revision = await self._clear_retired_values(
                target,
                revision_retired_ranges(base_spec, next_spec),
                headers,
            )
            on_step(RevisionStep.RETIRED_VALUES_CLEARED, revision)
            last_step = RevisionStep.RETIRED_VALUES_CLEARED
        if _revision_before(last_step, RevisionStep.UNION_STYLES_CLEARED):
            revision = await self._clear_managed_styles(
                target,
                revision_managed_ranges(base_spec, next_spec),
                headers,
            )
            on_step(RevisionStep.UNION_STYLES_CLEARED, revision)
            last_step = RevisionStep.UNION_STYLES_CLEARED
        if _revision_before(last_step, RevisionStep.NEXT_BASE_STYLE_WRITTEN):
            revision = await self._write_base_style(target, next_spec, headers)
            on_step(RevisionStep.NEXT_BASE_STYLE_WRITTEN, revision)
            last_step = RevisionStep.NEXT_BASE_STYLE_WRITTEN
        if _revision_before(last_step, RevisionStep.NEXT_STYLE_RANGES_WRITTEN):
            revision = await self._write_style_ranges(target, next_spec, headers)
            on_step(RevisionStep.NEXT_STYLE_RANGES_WRITTEN, revision)
            last_step = RevisionStep.NEXT_STYLE_RANGES_WRITTEN
        if _revision_before(last_step, RevisionStep.DIMENSIONS_WRITTEN):
            revision = await self._write_revision_dimensions(
                target,
                base_spec,
                next_spec,
                headers,
            )
            on_step(RevisionStep.DIMENSIONS_WRITTEN, revision)
            last_step = RevisionStep.DIMENSIONS_WRITTEN
        if _revision_before(last_step, RevisionStep.FREEZE_WRITTEN):
            revision = await self._write_freeze(target, next_spec, headers)
            on_step(RevisionStep.FREEZE_WRITTEN, revision)
            last_step = RevisionStep.FREEZE_WRITTEN
        if _revision_before(last_step, RevisionStep.NEXT_MERGES_WRITTEN):
            revision = await self._write_missing_merges(
                target,
                next_spec,
                headers,
            )
            on_step(RevisionStep.NEXT_MERGES_WRITTEN, revision)

        try:
            api_proof = await self._verify_api(target, next_spec, headers)
        except CapabilityError as exc:
            raise RemoteMutationFailure(
                "revision_api_verification_incomplete",
                ambiguous=False,
                verification_incomplete=True,
            ) from exc
        on_step(RevisionStep.API_VERIFIED, api_proof.revision)
        export_hash = await self._verify_revision_export(
            target,
            next_spec,
            diff,
            headers,
        )
        on_step(RevisionStep.EXPORT_VERIFIED, api_proof.revision)
        return RevisionProof(
            target=target,
            api_hash=api_proof.content_hash,
            export_hash=export_hash,
            remote_revision=api_proof.revision,
            observed_at=datetime.now(UTC),
        )

    async def reconcile_revision_final(
        self,
        *,
        record: RevisionRecord,
        registration: ManagedSheetRegistration,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        diff: RevisionDiffSummary,
    ) -> RevisionProof | None:
        repair_retired_styles = _requires_retired_style_repair(
            record.diagnostic_code
        )
        capabilities = [
            SHEETS_EXPORT_VERIFY_CAPABILITY,
            SHEETS_READ_CAPABILITY,
        ]
        if repair_retired_styles:
            capabilities.append(SHEETS_MANAGED_WRITE_CAPABILITY)
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=registration.profile_ref,
            capabilities=tuple(capabilities),
        )
        _require_profile_binding(lease.profile_ref, registration.profile_ref)
        headers = _headers(lease.access_token)
        try:
            target = await self._refresh_managed_target(
                registration.target,
                headers,
                allow_display_metadata_change=False,
            )
        except CapabilityError:
            return None
        if repair_retired_styles:
            await self._write_retired_neutral_styles(
                target,
                revision_retired_ranges(base_spec, next_spec),
                headers,
            )
        try:
            api_proof = await self._verify_api(target, next_spec, headers)
            export_hash = await self._verify_revision_export(
                target,
                next_spec,
                diff,
                headers,
            )
        except RemoteMutationFailure as exc:
            if exc.verification_incomplete and not exc.ambiguous:
                raise
            return None
        except CapabilityError:
            return None
        return RevisionProof(
            target=target,
            api_hash=api_proof.content_hash,
            export_hash=export_hash,
            remote_revision=api_proof.revision,
            observed_at=datetime.now(UTC),
            warnings=(
                "retired_styles_normalized_during_reconciliation"
                if repair_retired_styles
                else "ambiguous_revision_reconciled_by_double_readback"
            ,),
        )

    async def execute(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
        on_workbook: WorkbookCallback,
        on_target: TargetCallback,
        on_step: StepCallback,
    ) -> ExecutionProof:
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=record.profile_ref,
            capabilities=_execution_capabilities(record, spec),
        )
        headers = _headers(lease.access_token)
        target = record.target
        last_step = record.last_completed_step

        if (
            record.placement_mode is PlacementMode.CREATE_NEW_WORKBOOK
            and _before(last_step, WriteStep.WORKBOOK_CREATED)
        ):
            target = await self._recheck_and_create_workbook(target, headers)
            on_workbook(target, None)
            last_step = WriteStep.WORKBOOK_CREATED

        if _before(last_step, WriteStep.TARGET_REGISTERED):
            if record.placement_mode is PlacementMode.CREATE_NEW_SHEET:
                worksheets = await self._worksheets(
                    _required_spreadsheet_token(target), headers
                )
                requested_title = target.requested_sheet_title
                if requested_title is None:
                    raise RemoteMutationFailure(
                        "create_title_missing", ambiguous=True
                    )
                try:
                    _ensure_unique_title(worksheets, requested_title)
                except CapabilityError as exc:
                    raise RemoteMutationFailure(
                        "create_title_precondition_changed", ambiguous=True
                    ) from exc
                target, revision = await self._add_sheet(
                    target,
                    requested_title,
                    len(worksheets),
                    headers,
                )
            elif record.placement_mode is PlacementMode.CREATE_NEW_WORKBOOK:
                target, revision = await self._register_created_workbook_target(
                    target,
                    headers,
                )
            else:
                target, revision = await self._recheck_adoption(target, headers)
            on_target(target, revision)
            last_step = WriteStep.TARGET_REGISTERED

        if target.sheet_id is None:
            raise RemoteMutationFailure("stable_sheet_id_missing", ambiguous=True)

        worksheet = await self._worksheet_by_id(target, headers)
        if _before(last_step, WriteStep.GRID_EXTENDED):
            revision = await self._extend_grid(target, worksheet, spec, headers)
            on_step(WriteStep.GRID_EXTENDED, revision)
            last_step = WriteStep.GRID_EXTENDED
        if _before(last_step, WriteStep.VALUES_WRITTEN):
            revision = await self._write_values(target, spec, headers)
            on_step(WriteStep.VALUES_WRITTEN, revision)
            last_step = WriteStep.VALUES_WRITTEN
        if _before(last_step, WriteStep.STYLES_CLEARED):
            revision = await self._clear_styles(target, spec, headers)
            on_step(WriteStep.STYLES_CLEARED, revision)
            last_step = WriteStep.STYLES_CLEARED
        if _before(last_step, WriteStep.BASE_STYLE_WRITTEN):
            revision = await self._write_base_style(target, spec, headers)
            on_step(WriteStep.BASE_STYLE_WRITTEN, revision)
            last_step = WriteStep.BASE_STYLE_WRITTEN
        if _before(last_step, WriteStep.STYLE_RANGES_WRITTEN):
            revision = await self._write_style_ranges(target, spec, headers)
            on_step(WriteStep.STYLE_RANGES_WRITTEN, revision)
            last_step = WriteStep.STYLE_RANGES_WRITTEN
        if _before(last_step, WriteStep.DIMENSIONS_WRITTEN):
            revision = await self._write_dimensions(target, spec, headers)
            on_step(WriteStep.DIMENSIONS_WRITTEN, revision)
            last_step = WriteStep.DIMENSIONS_WRITTEN
        if _before(last_step, WriteStep.FREEZE_WRITTEN):
            revision = await self._write_freeze(target, spec, headers)
            on_step(WriteStep.FREEZE_WRITTEN, revision)
            last_step = WriteStep.FREEZE_WRITTEN
        if _before(last_step, WriteStep.MERGES_WRITTEN):
            revision = await self._write_merges(target, spec, headers)
            on_step(WriteStep.MERGES_WRITTEN, revision)

        try:
            api_proof = await self._verify_api(target, spec, headers)
        except CapabilityError as exc:
            raise RemoteMutationFailure(
                "api_verification_incomplete",
                ambiguous=False,
                verification_incomplete=True,
            ) from exc
        on_step(WriteStep.API_VERIFIED, api_proof.revision)
        try:
            export_hash = await self._verify_export(target, spec, headers)
        except RemoteMutationFailure:
            raise
        except CapabilityError as exc:
            raise RemoteMutationFailure(
                _export_diagnostic("unclassified", exc),
                ambiguous=False,
                verification_incomplete=True,
            ) from exc
        on_step(WriteStep.EXPORT_VERIFIED, api_proof.revision)
        return ExecutionProof(
            target=target,
            api_hash=api_proof.content_hash,
            export_hash=export_hash,
            remote_revision=api_proof.revision,
            observed_at=datetime.now(UTC),
        )

    async def reconcile_final(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
    ) -> ExecutionProof | None:
        target = record.target
        if target.sheet_id is None:
            return None
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=record.profile_ref,
            capabilities=(
                SHEETS_EXPORT_VERIFY_CAPABILITY,
                SHEETS_READ_CAPABILITY,
            ),
        )
        headers = _headers(lease.access_token)
        try:
            api_proof = await self._verify_api(target, spec, headers)
            export_hash = await self._verify_export(target, spec, headers)
        except RemoteMutationFailure as exc:
            if exc.verification_incomplete and not exc.ambiguous:
                raise
            return None
        except CapabilityError:
            return None
        return ExecutionProof(
            target=target,
            api_hash=api_proof.content_hash,
            export_hash=export_hash,
            remote_revision=api_proof.revision,
            observed_at=datetime.now(UTC),
            warnings=("ambiguous_write_reconciled_by_double_readback",),
        )

    async def reconcile_progress(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
    ) -> RecoveryProgress | None:
        if record.placement_mode is PlacementMode.CREATE_NEW_WORKBOOK:
            if record.last_completed_step is WriteStep.WORKBOOK_CREATED:
                return RecoveryProgress(
                    completed_step=WriteStep.WORKBOOK_CREATED,
                    remote_revision=record.remote_revision,
                    observed_at=datetime.now(UTC),
                    warnings=("created_workbook_checkpoint_preserved",),
                    target=record.target,
                )
            if (
                record.last_completed_step is WriteStep.NONE
                and record.ambiguous
                and record.diagnostic_code is not None
                and (
                    record.diagnostic_code.startswith("workbook_create_")
                    or record.diagnostic_code == "interrupted_execution"
                )
            ):
                return await self._reconcile_workbook_creation(record)

        if (
            record.placement_mode is not PlacementMode.CREATE_NEW_SHEET
            or record.last_completed_step is not WriteStep.GRID_EXTENDED
            or record.diagnostic_code != "values_write_contract_unknown"
            or record.target.sheet_id is None
        ):
            return None
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=record.profile_ref,
            capabilities=(SHEETS_READ_CAPABILITY,),
        )
        headers = _headers(lease.access_token)
        try:
            worksheet = await self._worksheet_by_id(record.target, headers)
            values = await self._values(
                _required_spreadsheet_token(record.target),
                worksheet,
                headers,
                require_bounded_full_grid=True,
            )
        except CapabilityError:
            return None
        if (
            worksheet.merges
            or worksheet.frozen_row_count != 0
            or worksheet.frozen_column_count != 0
        ):
            return None
        if _values_prove_written_spec(worksheet, values, spec):
            return RecoveryProgress(
                completed_step=WriteStep.VALUES_WRITTEN,
                remote_revision=values.revision,
                observed_at=datetime.now(UTC),
                warnings=("ambiguous_values_write_reconciled_by_api_readback",),
            )
        if _values_prove_blank(values):
            return RecoveryProgress(
                completed_step=WriteStep.GRID_EXTENDED,
                remote_revision=values.revision,
                observed_at=datetime.now(UTC),
                warnings=(
                    "ambiguous_values_write_proved_not_applied_by_api_readback",
                ),
            )
        return None

    async def aclose(self) -> None:
        await self._lease_client.aclose()
        if self._owns_http_client:
            await self._http.aclose()

    async def _recheck_and_create_workbook(
        self,
        target: ProtectedTarget,
        headers: dict[str, str],
    ) -> ProtectedTarget:
        source = _sheet_locator(target.source_locator)
        if source.resource_type is not ResourceType.FEISHU_WIKI:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The pending workbook target is no longer a Wiki parent node.",
            )
        parent = await self._wiki_parent(source, headers)
        _ensure_parent_binding(target, parent)
        children = await self._wiki_children(parent, headers)
        if _wiki_parent_state_hash(parent, children) != target.initial_state_hash:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The Wiki parent or its child-node set changed after preview; no workbook was created.",
            )
        requested_title = target.requested_workbook_title
        if requested_title is None:
            raise RemoteMutationFailure(
                "workbook_create_title_missing",
                ambiguous=False,
            )
        _ensure_unique_workbook_title(children, requested_title)
        return await self._create_workbook(
            source,
            target,
            requested_title,
            headers,
        )

    async def _create_workbook(
        self,
        source: ResourceLocator,
        target: ProtectedTarget,
        title: str,
        headers: dict[str, str],
    ) -> ProtectedTarget:
        if target.wiki_space_id is None or target.parent_wiki_node_token is None:
            raise RemoteMutationFailure(
                "workbook_create_parent_missing",
                ambiguous=False,
            )
        data = await self._mutate_json(
            WIKI_CHILDREN_ENDPOINT.format(space_id=target.wiki_space_id),
            method="POST",
            headers=headers,
            body={
                "obj_type": "sheet",
                "parent_node_token": target.parent_wiki_node_token,
                "node_type": "origin",
                "title": title,
            },
            operation="workbook_create",
        )
        raw_node = data.get("node")
        if not isinstance(raw_node, dict):
            raise RemoteMutationFailure(
                "workbook_create_contract_unknown",
                ambiguous=True,
            )
        try:
            node = _wiki_node(raw_node)
        except CapabilityError as exc:
            raise RemoteMutationFailure(
                "workbook_create_contract_unknown",
                ambiguous=True,
            ) from exc
        if (
            node.space_id != target.wiki_space_id
            or node.parent_node_token != target.parent_wiki_node_token
            or node.object_type != "sheet"
            or node.node_type != "origin"
            or node.title != title
        ):
            raise RemoteMutationFailure(
                "workbook_create_contract_unknown",
                ambiguous=True,
            )
        return _target_from_created_node(target, source, node)

    async def _register_created_workbook_target(
        self,
        target: ProtectedTarget,
        headers: dict[str, str],
    ) -> tuple[ProtectedTarget, str | None]:
        spreadsheet_token = _required_spreadsheet_token(target)
        metadata = await self._spreadsheet_metadata(spreadsheet_token, headers)
        workbook_title = required_text(
            metadata.get("title"), "title", "spreadsheet metadata"
        )
        if workbook_title != target.requested_workbook_title:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The newly created workbook title does not match the authorized title.",
            )
        worksheets = await self._worksheets(spreadsheet_token, headers)
        candidates = [
            item
            for item in worksheets
            if not item.hidden and item.resource_type == "sheet"
        ]
        if len(worksheets) != 1 or len(candidates) != 1:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The new workbook no longer contains exactly one normal visible default worksheet.",
                details={"worksheet_count": len(worksheets)},
            )
        worksheet = candidates[0]
        values = await self._values(
            spreadsheet_token,
            worksheet,
            headers,
            require_bounded_full_grid=True,
        )
        _ensure_content_blank(worksheet, values)
        if worksheet.frozen_row_count or worksheet.frozen_column_count:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The new workbook default worksheet changed before registration.",
            )
        return (
            target.model_copy(
                update={
                    "workbook_title": workbook_title,
                    "workbook_url": (
                        optional_text(metadata.get("url"))
                        or target.created_wiki_url
                    ),
                    "sheet_id": worksheet.sheet_id,
                    "sheet_title": worksheet.title,
                    "sheet_index": worksheet.index,
                    "initial_revision": values.revision,
                    "initial_sheet_count": 1,
                }
            ),
            values.revision,
        )

    async def _reconcile_workbook_creation(
        self,
        record: OperationRecord,
    ) -> RecoveryProgress | None:
        source = _sheet_locator(record.target.source_locator)
        if source.resource_type is not ResourceType.FEISHU_WIKI:
            return None
        lease = await self._lease_client.issue(
            task_ref=record.task_ref,
            profile_ref=record.profile_ref,
            capabilities=(
                WIKI_NODE_READ_CAPABILITY,
                WIKI_CHILD_LIST_CAPABILITY,
            ),
        )
        headers = _headers(lease.access_token)
        try:
            parent = await self._wiki_parent(source, headers)
            _ensure_parent_binding(record.target, parent)
            children = await self._wiki_children(parent, headers)
        except CapabilityError:
            return None
        requested_title = record.target.requested_workbook_title
        if requested_title is None:
            return None
        matches = [
            child
            for child in children
            if child.title == requested_title
        ]
        if not matches:
            if (
                _wiki_parent_state_hash(parent, children)
                != record.target.initial_state_hash
            ):
                return None
            return RecoveryProgress(
                completed_step=WriteStep.NONE,
                remote_revision=None,
                observed_at=datetime.now(UTC),
                warnings=("ambiguous_workbook_create_proved_not_applied",),
            )
        if len(matches) != 1:
            return None
        candidate = matches[0]
        if candidate.object_type != "sheet" or candidate.node_type != "origin":
            return None
        baseline_children = tuple(
            child for child in children if child.node_token != candidate.node_token
        )
        if (
            _wiki_parent_state_hash(parent, baseline_children)
            != record.target.initial_state_hash
        ):
            return None
        target = _target_from_created_node(record.target, source, candidate)
        return RecoveryProgress(
            completed_step=WriteStep.WORKBOOK_CREATED,
            remote_revision=None,
            observed_at=datetime.now(UTC),
            warnings=("ambiguous_workbook_create_reconciled_by_child_diff",),
            target=target,
        )

    async def _wiki_parent(
        self,
        source: ResourceLocator,
        headers: dict[str, str],
    ) -> _WikiNode:
        assert source.resource_id is not None
        data = await self._get_json(
            WIKI_NODE_ENDPOINT,
            headers=headers,
            params={"token": source.resource_id},
            operation="wiki_parent_node",
        )
        raw_node = data.get("node")
        if not isinstance(raw_node, dict):
            raise _contract_error("Feishu returned an invalid Wiki parent node.")
        node = _wiki_node(raw_node)
        if node.node_token != source.resource_id:
            raise _contract_error("Feishu returned another Wiki parent node.")
        if node.node_type != "origin":
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "New workbooks can be created only beneath an origin Wiki node.",
            )
        return node

    async def _wiki_children(
        self,
        parent: _WikiNode,
        headers: dict[str, str],
    ) -> tuple[_WikiNode, ...]:
        children: list[_WikiNode] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        for _ in range(MAX_WIKI_CHILD_PAGES):
            params = {
                "page_size": "50",
                "parent_node_token": parent.node_token,
            }
            if page_token is not None:
                params["page_token"] = page_token
            data = await self._get_json(
                WIKI_CHILDREN_ENDPOINT.format(space_id=parent.space_id),
                headers=headers,
                params=params,
                operation="wiki_child_nodes",
                enforce_response_limit=True,
            )
            raw_items = data.get("items", [])
            if (
                not isinstance(raw_items, list)
                or len(raw_items) > 50
                or any(not isinstance(item, dict) for item in raw_items)
            ):
                raise _contract_error("Feishu returned an invalid Wiki child list.")
            page = tuple(_wiki_node(item) for item in raw_items)
            if any(
                item.space_id != parent.space_id
                or item.parent_node_token != parent.node_token
                for item in page
            ):
                raise _contract_error("Feishu returned Wiki nodes outside the requested parent.")
            children.extend(page)
            if len(children) > MAX_WIKI_CHILDREN:
                raise CapabilityError(
                    CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
                    "The Wiki parent has more child nodes than the managed-write safety limit.",
                    details={"max_child_nodes": MAX_WIKI_CHILDREN},
                )
            has_more = data.get("has_more", False)
            if not isinstance(has_more, bool):
                raise _contract_error("Feishu returned an invalid Wiki pagination flag.")
            if not has_more:
                break
            next_page_token = required_text(
                data.get("page_token"), "page_token", "Wiki child list"
            )
            if (
                len(next_page_token) > 512
                or any(ord(character) < 32 for character in next_page_token)
                or next_page_token in seen_page_tokens
            ):
                raise _contract_error("Feishu returned an invalid Wiki page token.")
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token
        else:
            raise CapabilityError(
                CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
                "The Wiki child list did not complete within the pagination safety limit.",
            )
        if len({item.node_token for item in children}) != len(children):
            raise _contract_error("Feishu returned duplicate Wiki child node identifiers.")
        return tuple(children)

    async def _resolve_spreadsheet_token(
        self,
        source: ResourceLocator,
        headers: dict[str, str],
    ) -> str:
        assert source.resource_id is not None
        if source.resource_type is ResourceType.FEISHU_SHEET:
            return source.resource_id
        data = await self._get_json(
            WIKI_NODE_ENDPOINT,
            headers=headers,
            params={"token": source.resource_id},
            operation="wiki_node",
        )
        node = data.get("node")
        if not isinstance(node, dict):
            raise _contract_error("Feishu returned an invalid Wiki node contract.")
        object_type = required_text(node.get("obj_type"), "obj_type").lower()
        if object_type != "sheet":
            raise CapabilityError(
                CapabilityErrorCode.UNSUPPORTED_RESOURCE,
                "The Feishu Wiki node does not point to a Sheet.",
                details={"wiki_object_type": object_type},
            )
        token = required_text(node.get("obj_token"), "obj_token")
        if not RESOLVED_OBJECT_TOKEN.fullmatch(token):
            raise _contract_error("The resolved Feishu Sheet token is invalid.")
        return token

    async def _spreadsheet_metadata(
        self,
        spreadsheet_token: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        data = await self._get_json(
            SPREADSHEET_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
            headers=headers,
            operation="sheets_metadata",
        )
        spreadsheet = data.get("spreadsheet")
        if not isinstance(spreadsheet, dict):
            raise _contract_error("Feishu returned invalid spreadsheet metadata.")
        returned_token = optional_text(spreadsheet.get("token"))
        if returned_token is not None and returned_token != spreadsheet_token:
            raise _contract_error("Feishu returned metadata for another spreadsheet.")
        return spreadsheet

    async def _worksheets(
        self,
        spreadsheet_token: str,
        headers: dict[str, str],
    ) -> tuple[_RemoteWorksheet, ...]:
        data = await self._get_json(
            SHEETS_QUERY_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
            headers=headers,
            operation="sheets_query",
        )
        raw = data.get("sheets")
        if (
            not isinstance(raw, list)
            or len(raw) > MAX_WORKSHEETS
            or any(not isinstance(item, dict) for item in raw)
        ):
            raise _contract_error("Feishu returned an invalid worksheet list.")
        worksheets = tuple(_worksheet(item) for item in raw)
        if len({item.sheet_id for item in worksheets}) != len(worksheets):
            raise _contract_error("Feishu returned duplicate worksheet identifiers.")
        return worksheets

    async def _values(
        self,
        spreadsheet_token: str,
        worksheet: _RemoteWorksheet,
        headers: dict[str, str],
        *,
        require_bounded_full_grid: bool,
    ) -> _ValueSnapshot:
        if require_bounded_full_grid:
            _ensure_bounded_grid(worksheet)
        range_ = GridRange(
            row_start=0,
            row_end=worksheet.row_count,
            column_start=0,
            column_end=worksheet.column_count,
        ).a1(worksheet.sheet_id)
        data = await self._get_json(
            VALUES_BATCH_ENDPOINT.format(spreadsheet_token=spreadsheet_token),
            headers=headers,
            params={
                "ranges": range_,
                "valueRenderOption": "Formula",
                "dateTimeRenderOption": "FormattedString",
                "user_id_type": "open_id",
            },
            operation="sheets_values",
            enforce_response_limit=True,
        )
        raw_ranges = data.get("valueRanges")
        if (
            not isinstance(raw_ranges, list)
            or len(raw_ranges) != 1
            or not isinstance(raw_ranges[0], dict)
        ):
            raise _contract_error("Feishu did not return the complete worksheet range.")
        value_range = raw_ranges[0]
        if optional_text(value_range.get("range")) is None:
            raise _contract_error("Feishu returned no worksheet range evidence.")
        raw_values = value_range.get("values", [])
        if not isinstance(raw_values, list) or any(
            not isinstance(row, list) for row in raw_values
        ):
            raise _contract_error("Feishu returned invalid worksheet values.")
        if len(raw_values) > worksheet.row_count or any(
            len(row) > worksheet.column_count for row in raw_values
        ):
            raise _contract_error("Feishu returned values outside the requested range.")
        return _ValueSnapshot(
            values=tuple(tuple(row) for row in raw_values),
            revision=(
                optional_text(value_range.get("revision"))
                or optional_text(data.get("revision"))
            ),
        )

    async def _recheck_adoption(
        self,
        target: ProtectedTarget,
        headers: dict[str, str],
    ) -> tuple[ProtectedTarget, str | None]:
        worksheet = await self._worksheet_by_id(target, headers)
        values = await self._values(
            _required_spreadsheet_token(target),
            worksheet,
            headers,
            require_bounded_full_grid=True,
        )
        _ensure_content_blank(worksheet, values)
        if _adoption_state_hash(worksheet, values) != target.initial_state_hash:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The blank worksheet changed after preview; no write was attempted.",
            )
        return target, values.revision

    async def _worksheet_by_id(
        self,
        target: ProtectedTarget,
        headers: dict[str, str],
    ) -> _RemoteWorksheet:
        if target.sheet_id is None:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet has no stable identifier.",
            )
        matches = [
            item
            for item in await self._worksheets(
                _required_spreadsheet_token(target), headers
            )
            if item.sheet_id == target.sheet_id
        ]
        if (
            len(matches) != 1
            or matches[0].title != target.sheet_title
            or matches[0].hidden
            or matches[0].resource_type != "sheet"
        ):
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet identity, title, visibility, or type changed.",
            )
        return matches[0]

    async def _refresh_managed_target(
        self,
        target: ProtectedTarget,
        headers: dict[str, str],
        *,
        allow_display_metadata_change: bool,
    ) -> ProtectedTarget:
        if target.sheet_id is None:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet has no stable identifier.",
            )
        metadata = await self._spreadsheet_metadata(
            _required_spreadsheet_token(target),
            headers,
        )
        worksheets = await self._worksheets(
            _required_spreadsheet_token(target), headers
        )
        matches = [item for item in worksheets if item.sheet_id == target.sheet_id]
        if len(matches) != 1:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet no longer exists in its registered workbook.",
            )
        worksheet = matches[0]
        if worksheet.hidden or worksheet.resource_type != "sheet":
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet is hidden or no longer a normal worksheet.",
            )
        workbook_title = required_text(
            metadata.get("title"), "title", "spreadsheet metadata"
        )
        workbook_url = optional_text(metadata.get("url")) or target.workbook_url
        changed = (
            target.workbook_title != workbook_title
            or target.workbook_url != workbook_url
            or target.sheet_title != worksheet.title
            or target.sheet_index != worksheet.index
        )
        if changed and not allow_display_metadata_change:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet display metadata changed after revision preview.",
            )
        return target.model_copy(
            update={
                "workbook_title": workbook_title,
                "workbook_url": workbook_url,
                "sheet_title": worksheet.title,
                "sheet_index": worksheet.index,
            }
        )

    async def _add_sheet(
        self,
        target: ProtectedTarget,
        title: str,
        index: int,
        headers: dict[str, str],
    ) -> tuple[ProtectedTarget, str | None]:
        data = await self._mutate_json(
            SHEETS_BATCH_UPDATE_ENDPOINT.format(
                spreadsheet_token=_required_spreadsheet_token(target)
            ),
            method="POST",
            headers=headers,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": title, "index": index}}}
                ]
            },
            operation="sheet_add",
        )
        replies = data.get("replies")
        if not isinstance(replies, list) or len(replies) != 1:
            raise RemoteMutationFailure("sheet_add_contract_unknown", ambiguous=True)
        reply = replies[0]
        properties = (
            reply.get("addSheet", {}).get("properties")
            if isinstance(reply, dict)
            else None
        )
        if not isinstance(properties, dict):
            raise RemoteMutationFailure("sheet_add_contract_unknown", ambiguous=True)
        sheet_id = optional_text(
            properties.get("sheetId") or properties.get("sheet_id")
        )
        returned_title = optional_text(properties.get("title"))
        returned_index = properties.get("index")
        if (
            sheet_id is None
            or not _SHEET_ID.fullmatch(sheet_id)
            or returned_title != title
            or isinstance(returned_index, bool)
            or not isinstance(returned_index, int)
            or returned_index < 0
        ):
            raise RemoteMutationFailure("sheet_add_contract_unknown", ambiguous=True)
        return (
            target.model_copy(
                update={
                    "sheet_id": sheet_id,
                    "sheet_title": returned_title,
                    "sheet_index": returned_index,
                }
            ),
            optional_text(data.get("revision")),
        )

    async def _extend_grid(
        self,
        target: ProtectedTarget,
        worksheet: _RemoteWorksheet,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        dimensions = []
        if worksheet.row_count < spec.row_count:
            dimensions.append(("ROWS", spec.row_count - worksheet.row_count))
        if worksheet.column_count < spec.column_count:
            dimensions.append(("COLUMNS", spec.column_count - worksheet.column_count))
        revision: str | None = None
        for completed, (major_dimension, length) in enumerate(dimensions):
            try:
                data = await self._mutate_json(
                    DIMENSION_RANGE_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="POST",
                    headers=headers,
                    body={
                        "dimension": {
                            "sheetId": target.sheet_id,
                            "majorDimension": major_dimension,
                            "length": length,
                        }
                    },
                    operation="grid_extend",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "grid_extend_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _write_values(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        if spec.has_boolean_values:
            return await self._write_typed_values(target, spec, headers)
        data = await self._mutate_json(
            VALUES_ENDPOINT.format(
                spreadsheet_token=_required_spreadsheet_token(target)
            ),
            method="PUT",
            headers=headers,
            body={
                "valueRange": {
                    "range": spec.delivery_range.a1(_required_sheet_id(target)),
                    "values": spec.remote_values(),
                }
            },
            operation="values_write",
        )
        return optional_text(data.get("revision"))

    async def _write_typed_values(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        sheet_id = _required_sheet_id(target)
        qualified_range = spec.delivery_range.a1(sheet_id)
        _, _, cell_range = qualified_range.partition("!")
        tool_input = {
            "excel_id": _required_spreadsheet_token(target),
            "sheet_id": sheet_id,
            "range": cell_range,
            "cells": spec.typed_remote_cells(),
        }
        data = await self._mutate_json(
            TYPED_VALUES_ENDPOINT.format(
                spreadsheet_token=_required_spreadsheet_token(target)
            ),
            method="POST",
            headers=headers,
            body={
                "tool_name": "set_cell_range",
                "input": json.dumps(
                    tool_input,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
            operation="values_write",
        )
        return optional_text(data.get("revision"))

    async def _clear_retired_values(
        self,
        target: ProtectedTarget,
        ranges: tuple[GridRange, ...],
        headers: dict[str, str],
    ) -> str | None:
        revision: str | None = None
        for completed, range_ in enumerate(ranges):
            values = [
                [""] * (range_.column_end - range_.column_start)
                for _ in range(range_.row_end - range_.row_start)
            ]
            try:
                data = await self._mutate_json(
                    VALUES_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="PUT",
                    headers=headers,
                    body={
                        "valueRange": {
                            "range": range_.a1(_required_sheet_id(target)),
                            "values": values,
                        }
                    },
                    operation="retired_values_clear",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "retired_values_clear_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _clear_managed_styles(
        self,
        target: ProtectedTarget,
        ranges: tuple[GridRange, ...],
        headers: dict[str, str],
    ) -> str | None:
        if not ranges:
            return None
        items = tuple(
            {
                "ranges": [range_.a1(_required_sheet_id(target))],
                "style": {"clean": True},
            }
            for range_ in ranges
        )
        revision = await self._batched_style_requests(target, items, headers)
        neutral_revision = await self._write_retired_neutral_styles(
            target,
            ranges[1:],
            headers,
        )
        return neutral_revision or revision

    async def _write_retired_neutral_styles(
        self,
        target: ProtectedTarget,
        ranges: tuple[GridRange, ...],
        headers: dict[str, str],
    ) -> str | None:
        if not ranges:
            return None
        sheet_id = _required_sheet_id(target)
        items = tuple(
            {
                "ranges": [range_.a1(sheet_id)],
                "style": _retired_neutral_style_payload(),
            }
            for range_ in ranges
        )
        return await self._batched_style_requests(target, items, headers)

    async def _clear_styles(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        return await self._style_request(
            target,
            [{
                "ranges": [spec.delivery_range.a1(_required_sheet_id(target))],
                "style": {"clean": True},
            }],
            headers,
            "styles_clear",
        )

    async def _write_base_style(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        return await self._style_request(
            target,
            [{
                "ranges": [spec.delivery_range.a1(_required_sheet_id(target))],
                "style": _style_payload(spec.base_style),
            }],
            headers,
            "base_style_write",
        )

    async def _write_style_ranges(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        resolved = spec.resolved_style_ranges()
        if not resolved:
            return None
        sheet_id = _required_sheet_id(target)
        clean_items = [
            {"ranges": [range_.a1(sheet_id)], "style": {"clean": True}}
            for range_, _ in resolved
        ]
        style_items = [
            {"ranges": [range_.a1(sheet_id)], "style": _style_payload(style)}
            for range_, style in resolved
        ]
        return await self._batched_style_requests(
            target,
            (*clean_items, *style_items),
            headers,
        )

    async def _batched_style_requests(
        self,
        target: ProtectedTarget,
        items: tuple[dict[str, Any], ...],
        headers: dict[str, str],
    ) -> str | None:
        revision: str | None = None
        for completed, start in enumerate(range(0, len(items), STYLE_BATCH_ITEMS)):
            try:
                revision = (
                    await self._style_request(
                        target,
                        list(items[start : start + STYLE_BATCH_ITEMS]),
                        headers,
                        "style_ranges_write",
                    )
                    or revision
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "style_ranges_partial_unknown", ambiguous=True
                    ) from exc
                raise
        return revision

    async def _style_request(
        self,
        target: ProtectedTarget,
        items: list[dict[str, Any]],
        headers: dict[str, str],
        operation: str,
    ) -> str | None:
        try:
            data = await self._mutate_json(
                STYLES_BATCH_UPDATE_ENDPOINT.format(
                    spreadsheet_token=_required_spreadsheet_token(target)
                ),
                method="PUT",
                headers=headers,
                body={"data": items},
                operation=operation,
            )
        except RemoteMutationFailure as exc:
            if len(items) > 1 and not exc.ambiguous:
                raise RemoteMutationFailure(
                    f"{operation}_batch_unknown", ambiguous=True
                ) from exc
            raise
        return optional_text(data.get("revision"))

    async def _write_dimensions(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        dimensions: list[tuple[str, int, int, int]] = [
            ("ROWS", 0, spec.row_count, spec.default_row_height_px),
            ("COLUMNS", 0, spec.column_count, spec.default_column_width_px),
        ]
        dimensions.extend(
            ("ROWS", span.start_index, span.end_index, span.pixel_size)
            for span in spec.row_heights
        )
        dimensions.extend(
            ("COLUMNS", span.start_index, span.end_index, span.pixel_size)
            for span in spec.column_widths
        )
        revision: str | None = None
        for completed, (major, start, end, pixels) in enumerate(dimensions):
            try:
                data = await self._mutate_json(
                    DIMENSION_RANGE_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="PUT",
                    headers=headers,
                    body={
                        "dimension": {
                            "sheetId": _required_sheet_id(target),
                            "majorDimension": major,
                            "startIndex": start + 1,
                            "endIndex": end,
                        },
                        "dimensionProperties": {
                            "visible": True,
                            "fixedSize": pixels,
                        },
                    },
                    operation="dimensions_write",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "dimensions_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _write_revision_dimensions(
        self,
        target: ProtectedTarget,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        revision = await self._write_dimensions(target, next_spec, headers)
        resets: list[tuple[str, int, int, int]] = []
        if base_spec.row_count > next_spec.row_count:
            resets.append(
                ("ROWS", next_spec.row_count, base_spec.row_count, 24)
            )
        if base_spec.column_count > next_spec.column_count:
            resets.append(
                (
                    "COLUMNS",
                    next_spec.column_count,
                    base_spec.column_count,
                    100,
                )
            )
        for major, start, end, pixels in resets:
            try:
                data = await self._mutate_json(
                    DIMENSION_RANGE_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="PUT",
                    headers=headers,
                    body={
                        "dimension": {
                            "sheetId": _required_sheet_id(target),
                            "majorDimension": major,
                            "startIndex": start + 1,
                            "endIndex": end,
                        },
                        "dimensionProperties": {
                            "visible": True,
                            "fixedSize": pixels,
                        },
                    },
                    operation="retired_dimensions_reset",
                )
            except RemoteMutationFailure as exc:
                raise RemoteMutationFailure(
                    "revision_dimensions_partial_unknown",
                    ambiguous=True,
                ) from exc
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _write_freeze(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        data = await self._mutate_json(
            SHEETS_BATCH_UPDATE_ENDPOINT.format(
                spreadsheet_token=_required_spreadsheet_token(target)
            ),
            method="POST",
            headers=headers,
            body={
                "requests": [
                    {
                        "updateSheet": {
                            "properties": {
                                "sheetId": _required_sheet_id(target),
                                "frozenRowCount": spec.frozen_row_count,
                                "frozenColCount": spec.frozen_column_count,
                            }
                        }
                    }
                ]
            },
            operation="freeze_write",
        )
        return optional_text(data.get("revision"))

    async def _write_merges(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        revision: str | None = None
        for completed, merge in enumerate(spec.merges):
            try:
                data = await self._mutate_json(
                    MERGE_CELLS_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="POST",
                    headers=headers,
                    body={
                        "range": merge.a1(_required_sheet_id(target)),
                        "mergeType": "MERGE_ALL",
                    },
                    operation="merges_write",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "merges_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _remove_base_merges(
        self,
        target: ProtectedTarget,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        worksheet = await self._worksheet_by_id(target, headers)
        allowed = {
            *(_range_key(item) for item in base_spec.merges),
            *(_range_key(item) for item in next_spec.merges),
        }
        unexpected = [
            item for item in worksheet.merges if _range_key(item) not in allowed
        ]
        if unexpected:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet gained an unrecognized merge during revision.",
            )
        revision: str | None = None
        for completed, merge in enumerate(worksheet.merges):
            try:
                data = await self._mutate_json(
                    UNMERGE_CELLS_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="POST",
                    headers=headers,
                    body={"range": merge.a1(_required_sheet_id(target))},
                    operation="base_merges_remove",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "base_merges_remove_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _write_missing_merges(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> str | None:
        worksheet = await self._worksheet_by_id(target, headers)
        expected = {_range_key(item): item for item in spec.merges}
        actual = {_range_key(item): item for item in worksheet.merges}
        if any(key not in expected for key in actual):
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "The managed worksheet contains an unexpected merge before final merge reconstruction.",
            )
        missing = [item for key, item in expected.items() if key not in actual]
        revision: str | None = None
        for completed, merge in enumerate(missing):
            try:
                data = await self._mutate_json(
                    MERGE_CELLS_ENDPOINT.format(
                        spreadsheet_token=_required_spreadsheet_token(target)
                    ),
                    method="POST",
                    headers=headers,
                    body={
                        "range": merge.a1(_required_sheet_id(target)),
                        "mergeType": "MERGE_ALL",
                    },
                    operation="next_merges_write",
                )
            except RemoteMutationFailure as exc:
                if completed:
                    raise RemoteMutationFailure(
                        "next_merges_partial_unknown", ambiguous=True
                    ) from exc
                raise
            revision = optional_text(data.get("revision")) or revision
        return revision

    async def _verify_api(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
    ) -> _ApiProof:
        worksheet = await self._worksheet_by_id(target, headers)
        values = await self._values(
            _required_spreadsheet_token(target),
            worksheet,
            headers,
            require_bounded_full_grid=True,
        )
        mismatches: list[str] = []
        if worksheet.frozen_row_count != spec.frozen_row_count:
            mismatches.append("frozen_rows")
        if worksheet.frozen_column_count != spec.frozen_column_count:
            mismatches.append("frozen_columns")
        if tuple(sorted(worksheet.merges, key=_range_key)) != tuple(
            sorted(spec.merges, key=_range_key)
        ):
            mismatches.append("merge_ranges")
        expected = spec.remote_values()
        for row_index in range(worksheet.row_count):
            for column_index in range(worksheet.column_count):
                actual = _cell(values.values, row_index, column_index)
                inside = (
                    row_index < spec.row_count
                    and column_index < spec.column_count
                )
                if inside:
                    if not _values_equal(
                        actual, expected[row_index][column_index]
                    ):
                        mismatches.append(f"cell_value:{row_index}:{column_index}")
                elif not _blank(actual):
                    mismatches.append("content_outside_delivery_rectangle")
        if mismatches:
            raise CapabilityError(
                CapabilityErrorCode.VERIFICATION_INCOMPLETE,
                "Feishu API readback does not prove the authorized worksheet state.",
                details={"mismatches": list(dict.fromkeys(mismatches))[:50]},
            )
        canonical = json.dumps(
            {
                "sheet_id": worksheet.sheet_id,
                "sheet_title": worksheet.title,
                "spec_hash": spec.content_hash,
                "revision": values.revision,
                "grid_rows": worksheet.row_count,
                "grid_columns": worksheet.column_count,
                "merges": [item.model_dump(mode="json") for item in worksheet.merges],
                "frozen_rows": worksheet.frozen_row_count,
                "frozen_columns": worksheet.frozen_column_count,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _ApiProof(
            content_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
            revision=values.revision,
        )

    async def _verify_export(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        headers: dict[str, str],
        *,
        retired_ranges: tuple[GridRange, ...] = (),
        neutral_row_indexes: tuple[int, ...] = (),
        neutral_column_indexes: tuple[int, ...] = (),
    ) -> str:
        try:
            try:
                data = await self._write_like_json(
                    EXPORT_TASKS_ENDPOINT,
                    method="POST",
                    headers=headers,
                    body={
                        "file_extension": "xlsx",
                        "token": _required_spreadsheet_token(target),
                        "type": "sheet",
                    },
                    operation="export_create",
                )
                ticket = required_text(data.get("ticket"), "ticket", "export task")
                if not RESOLVED_OBJECT_TOKEN.fullmatch(ticket):
                    raise _contract_error("Feishu returned an invalid export ticket.")
            except CapabilityError as exc:
                raise RemoteMutationFailure(
                    _export_diagnostic("create", exc),
                    ambiguous=False,
                    verification_incomplete=True,
                ) from exc

            file_token: str | None = None
            declared_size: int | None = None
            for attempt in range(self._export_poll_attempts):
                try:
                    data = await self._get_json(
                        EXPORT_TASK_ENDPOINT.format(ticket=ticket),
                        headers=headers,
                        params={"token": _required_spreadsheet_token(target)},
                        operation="export_poll",
                    )
                    result = data.get("result")
                    if not isinstance(result, dict):
                        raise _contract_error("Feishu returned an invalid export result.")
                    status = result.get("job_status")
                except CapabilityError as exc:
                    raise RemoteMutationFailure(
                        _export_diagnostic("poll", exc),
                        ambiguous=False,
                        verification_incomplete=True,
                    ) from exc

                if status == 0:
                    try:
                        file_token = required_text(
                            result.get("file_token"), "file_token", "export result"
                        )
                        if not RESOLVED_OBJECT_TOKEN.fullmatch(file_token):
                            raise _contract_error(
                                "Feishu returned an invalid export file token."
                            )
                        size = result.get("file_size")
                        if (
                            isinstance(size, bool)
                            or not isinstance(size, int)
                            or size < 1
                        ):
                            raise _contract_error(
                                "Feishu returned an invalid export file size."
                            )
                        declared_size = size
                    except CapabilityError as exc:
                        raise RemoteMutationFailure(
                            _export_diagnostic("poll", exc),
                            ambiguous=False,
                            verification_incomplete=True,
                        ) from exc
                    break
                if status not in {1, 2}:
                    raise RemoteMutationFailure(
                        _export_job_diagnostic(status),
                        ambiguous=False,
                        verification_incomplete=True,
                    )
                if attempt + 1 < self._export_poll_attempts:
                    await self._sleeper(self._export_poll_interval_seconds)

            if file_token is None or declared_size is None:
                raise RemoteMutationFailure(
                    "xlsx_export_poll_timeout",
                    ambiguous=False,
                    verification_incomplete=True,
                )
            if declared_size > MAX_XLSX_BYTES:
                raise RemoteMutationFailure(
                    "xlsx_export_size_limit",
                    ambiguous=False,
                    verification_incomplete=True,
                )
            try:
                payload = await self._download(
                    EXPORT_DOWNLOAD_ENDPOINT.format(file_token=file_token),
                    headers=headers,
                    operation="export_download",
                )
            except CapabilityError as exc:
                raise RemoteMutationFailure(
                    _export_diagnostic("download", exc),
                    ambiguous=False,
                    verification_incomplete=True,
                ) from exc
            if len(payload) != declared_size:
                raise RemoteMutationFailure(
                    "xlsx_export_download_size_mismatch",
                    ambiguous=False,
                    verification_incomplete=True,
                )
            try:
                verification = verify_sheet_export(
                    payload,
                    target_title=target.sheet_title or "",
                    spec=spec,
                    retired_ranges=retired_ranges,
                    neutral_row_indexes=neutral_row_indexes,
                    neutral_column_indexes=neutral_column_indexes,
                )
            except CapabilityError as exc:
                raise RemoteMutationFailure(
                    _export_diagnostic("verify", exc),
                    ambiguous=False,
                    verification_incomplete=True,
                ) from exc
            return verification.content_hash
        except RemoteMutationFailure:
            raise
        except httpx.HTTPError as exc:
            raise RemoteMutationFailure(
                "xlsx_export_transport_unavailable",
                ambiguous=False,
                verification_incomplete=True,
            ) from exc

    async def _verify_revision_export(
        self,
        target: ProtectedTarget,
        spec: SheetDeliverySpec,
        diff: RevisionDiffSummary,
        headers: dict[str, str],
    ) -> str:
        retired: list[GridRange] = []
        shared_rows = min(diff.base_rows, diff.next_rows)
        if diff.base_rows > diff.next_rows:
            retired.append(
                GridRange(
                    row_start=diff.next_rows,
                    row_end=diff.base_rows,
                    column_start=0,
                    column_end=diff.base_columns,
                )
            )
        if diff.base_columns > diff.next_columns and shared_rows > 0:
            retired.append(
                GridRange(
                    row_start=0,
                    row_end=shared_rows,
                    column_start=diff.next_columns,
                    column_end=diff.base_columns,
                )
            )
        return await self._verify_export(
            target,
            spec,
            headers,
            retired_ranges=tuple(retired),
            neutral_row_indexes=diff.neutral_rows,
            neutral_column_indexes=diff.neutral_columns,
        )

    async def _get_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        operation: str,
        params: dict[str, str] | None = None,
        enforce_response_limit: bool = False,
    ) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{self._origin}{path}", headers=headers, params=params
            )
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise http_error(response, operation)
        if enforce_response_limit and len(response.content) > MAX_JSON_RESPONSE_BYTES:
            raise CapabilityError(
                CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
                "Feishu returned more worksheet data than the Provider safety limit.",
                details={"max_response_bytes": MAX_JSON_RESPONSE_BYTES},
            )
        return data_object(response, operation)

    async def _mutate_json(
        self,
        path: str,
        *,
        method: str,
        headers: dict[str, str],
        body: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                f"{self._origin}{path}",
                headers=headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise RemoteMutationFailure(
                f"{operation}_transport_unknown", ambiguous=True
            ) from exc
        if not response.is_success:
            error = http_error(response, operation)
            if response.status_code >= 500 or response.status_code == 408:
                raise RemoteMutationFailure(
                    f"{operation}_server_unknown", ambiguous=True
                ) from error
            raise RemoteMutationFailure(
                _mutation_diagnostic(operation, error), ambiguous=False
            ) from error
        try:
            return _mutation_data_object(response, operation)
        except CapabilityError as exc:
            diagnostic = _mutation_diagnostic(operation, exc)
            if diagnostic != f"{operation}_{exc.code.value}":
                raise RemoteMutationFailure(
                    diagnostic,
                    ambiguous=False,
                ) from exc
            raise RemoteMutationFailure(
                f"{operation}_contract_unknown", ambiguous=True
            ) from exc

    async def _write_like_json(
        self,
        path: str,
        *,
        method: str,
        headers: dict[str, str],
        body: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, f"{self._origin}{path}", headers=headers, json=body
            )
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise http_error(response, operation)
        return data_object(response, operation)

    async def _download(
        self,
        path: str,
        *,
        headers: dict[str, str],
        operation: str,
    ) -> bytes:
        try:
            response = await self._http.get(f"{self._origin}{path}", headers=headers)
        except httpx.HTTPError as exc:
            raise CapabilityError(
                CapabilityErrorCode.PROVIDER_UNAVAILABLE,
                f"Feishu could not be reached for {operation}.",
                retryable=True,
            ) from exc
        if not response.is_success:
            raise http_error(response, operation)
        if len(response.content) > MAX_XLSX_BYTES:
            raise CapabilityError(
                CapabilityErrorCode.VERIFICATION_INCOMPLETE,
                "Feishu XLSX download exceeds the verification size limit.",
            )
        return response.content


def _sheet_locator(value: str) -> ResourceLocator:
    source = classify_locator(value)
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
            "Managed worksheet delivery supports only Sheet resources or Wiki nodes.",
            details={"resource_type": source.resource_type.value},
        )
    if source.resource_id is None:
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "The Feishu locator has no resource identifier.",
        )
    return source


def _delivery_capabilities(spec: SheetDeliverySpec) -> tuple[str, ...]:
    capabilities = [
        SHEETS_EXPORT_VERIFY_CAPABILITY,
        SHEETS_MANAGED_WRITE_CAPABILITY,
        SHEETS_READ_CAPABILITY,
    ]
    if spec.has_boolean_values:
        capabilities.append(SHEETS_TYPED_VALUES_WRITE_CAPABILITY)
    return tuple(capabilities)


def _execution_capabilities(
    record: OperationRecord,
    spec: SheetDeliverySpec,
) -> tuple[str, ...]:
    capabilities = list(_delivery_capabilities(spec))
    if (
        record.placement_mode is PlacementMode.CREATE_NEW_WORKBOOK
        and _before(record.last_completed_step, WriteStep.WORKBOOK_CREATED)
    ):
        capabilities.extend(
            (
                WIKI_NODE_READ_CAPABILITY,
                WIKI_CHILD_LIST_CAPABILITY,
                WIKI_NODE_CREATE_CAPABILITY,
            )
        )
    return tuple(dict.fromkeys(capabilities))


def _registration_resolve_capabilities(
    source: ResourceLocator,
) -> tuple[str, ...]:
    capabilities = [SHEETS_READ_CAPABILITY]
    if source.resource_type is ResourceType.FEISHU_WIKI:
        capabilities.append(WIKI_NODE_READ_CAPABILITY)
    return tuple(capabilities)


def _capabilities(
    source: ResourceLocator,
    spec: SheetDeliverySpec,
    placement_mode: PlacementMode,
) -> tuple[str, ...]:
    capabilities = list(_delivery_capabilities(spec))
    if source.resource_type is ResourceType.FEISHU_WIKI:
        capabilities.append(WIKI_NODE_READ_CAPABILITY)
    if placement_mode is PlacementMode.CREATE_NEW_WORKBOOK:
        capabilities.extend(
            (WIKI_CHILD_LIST_CAPABILITY, WIKI_NODE_CREATE_CAPABILITY)
        )
    return tuple(capabilities)


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _require_profile_binding(actual: str, expected: str) -> None:
    if actual != expected:
        raise CapabilityError(
            CapabilityErrorCode.PROVIDER_CONTRACT_ERROR,
            "The authorization control plane returned another Feishu Profile.",
        )


def _worksheet(raw: dict[str, Any]) -> _RemoteWorksheet:
    sheet_id = required_text(raw.get("sheet_id"), "sheet_id", "worksheet")
    if not _SHEET_ID.fullmatch(sheet_id):
        raise _contract_error("Feishu returned an invalid worksheet identifier.")
    title = required_text(raw.get("title"), "title", "worksheet")
    index = _nonnegative_int(raw.get("index"), "worksheet index")
    hidden = raw.get("hidden", False)
    if not isinstance(hidden, bool):
        raise _contract_error("Feishu returned an invalid worksheet hidden value.")
    resource_type = optional_text(raw.get("resource_type")) or "sheet"
    grid = raw.get("grid_properties")
    if not isinstance(grid, dict):
        raise _contract_error("Feishu returned no worksheet grid properties.")
    merges: list[GridRange] = []
    raw_merges = raw.get("merges", [])
    if not isinstance(raw_merges, list):
        raise _contract_error("Feishu returned invalid merge ranges.")
    for merge in raw_merges:
        if not isinstance(merge, dict):
            raise _contract_error("Feishu returned an invalid merge range.")
        start_row = _nonnegative_int(
            merge.get("start_row_index"), "merge start row"
        )
        end_row = _nonnegative_int(merge.get("end_row_index"), "merge end row")
        start_column = _nonnegative_int(
            merge.get("start_column_index"), "merge start column"
        )
        end_column = _nonnegative_int(
            merge.get("end_column_index"), "merge end column"
        )
        try:
            merges.append(
                GridRange(
                    row_start=start_row,
                    row_end=end_row + 1,
                    column_start=start_column,
                    column_end=end_column + 1,
                )
            )
        except ValueError as exc:
            raise _contract_error("Feishu returned a reversed merge range.") from exc
    return _RemoteWorksheet(
        sheet_id=sheet_id,
        title=title,
        index=index,
        hidden=hidden,
        resource_type=resource_type,
        row_count=_nonnegative_int(grid.get("row_count"), "row count"),
        column_count=_nonnegative_int(grid.get("column_count"), "column count"),
        frozen_row_count=_nonnegative_int(
            grid.get("frozen_row_count", 0), "frozen row count"
        ),
        frozen_column_count=_nonnegative_int(
            grid.get("frozen_column_count", 0), "frozen column count"
        ),
        merges=tuple(merges),
    )


def _wiki_node(raw: dict[str, Any]) -> _WikiNode:
    space_id = required_text(raw.get("space_id"), "space_id", "Wiki node")
    node_token = required_text(raw.get("node_token"), "node_token", "Wiki node")
    object_token = required_text(raw.get("obj_token"), "obj_token", "Wiki node")
    if (
        not RESOLVED_OBJECT_TOKEN.fullmatch(space_id)
        or not RESOLVED_OBJECT_TOKEN.fullmatch(node_token)
        or not RESOLVED_OBJECT_TOKEN.fullmatch(object_token)
    ):
        raise _contract_error("Feishu returned an invalid Wiki node identity.")
    object_type = required_text(raw.get("obj_type"), "obj_type", "Wiki node").lower()
    node_type = required_text(raw.get("node_type"), "node_type", "Wiki node").lower()
    if node_type not in {"origin", "shortcut"}:
        raise _contract_error("Feishu returned an invalid Wiki node type.")
    has_child = raw.get("has_child", False)
    if not isinstance(has_child, bool):
        raise _contract_error("Feishu returned an invalid Wiki child flag.")
    parent_node_token = optional_text(raw.get("parent_node_token"))
    if (
        parent_node_token is not None
        and not RESOLVED_OBJECT_TOKEN.fullmatch(parent_node_token)
    ):
        raise _contract_error("Feishu returned an invalid Wiki parent identity.")
    return _WikiNode(
        space_id=space_id,
        node_token=node_token,
        object_token=object_token,
        object_type=object_type,
        parent_node_token=parent_node_token,
        node_type=node_type,
        title=optional_text(raw.get("title")) or "",
        has_child=has_child,
    )


def _select_adoption_target(
    worksheets: tuple[_RemoteWorksheet, ...],
    selector: str | None,
) -> _RemoteWorksheet:
    if selector is not None:
        matches = [item for item in worksheets if item.sheet_id == selector]
        if len(matches) != 1:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The worksheet selected by the Feishu URL was not found.",
            )
        selected = matches[0]
    else:
        candidates = [
            item
            for item in worksheets
            if not item.hidden and item.resource_type == "sheet"
        ]
        if len(candidates) != 1:
            raise CapabilityError(
                CapabilityErrorCode.PRECONDITION_FAILED,
                "Blank-sheet adoption needs an exact URL sheet selector unless the workbook has exactly one normal visible worksheet.",
                details={"normal_visible_worksheet_count": len(candidates)},
            )
        selected = candidates[0]
    if selected.hidden or selected.resource_type != "sheet":
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "Only a normal visible worksheet can be adopted.",
        )
    return selected


def _ensure_unique_title(
    worksheets: tuple[_RemoteWorksheet, ...],
    requested_title: str,
) -> None:
    if any(item.title.casefold() == requested_title.casefold() for item in worksheets):
        raise CapabilityError(
            CapabilityErrorCode.WRITE_CONFLICT,
            "The requested worksheet title already exists in the target workbook.",
            details={"conflict": "worksheet_title"},
        )


def _ensure_unique_workbook_title(
    children: tuple[_WikiNode, ...],
    requested_title: str,
) -> None:
    if any(item.title.casefold() == requested_title.casefold() for item in children):
        raise CapabilityError(
            CapabilityErrorCode.WRITE_CONFLICT,
            "The requested workbook title already exists under the Wiki parent node.",
            details={"conflict": "wiki_child_title"},
        )


def _ensure_parent_binding(target: ProtectedTarget, parent: _WikiNode) -> None:
    if (
        target.wiki_space_id != parent.space_id
        or target.parent_wiki_node_token != parent.node_token
        or target.parent_wiki_title != parent.title
    ):
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The Wiki parent identity or title changed after preview.",
        )


def _target_from_created_node(
    target: ProtectedTarget,
    source: ResourceLocator,
    node: _WikiNode,
) -> ProtectedTarget:
    return target.model_copy(
        update={
            "spreadsheet_token": node.object_token,
            "workbook_title": node.title,
            "workbook_url": _wiki_url(source, node.node_token),
            "created_wiki_node_token": node.node_token,
            "created_wiki_url": _wiki_url(source, node.node_token),
        }
    )


def _wiki_url(source: ResourceLocator, node_token: str) -> str:
    parsed = urlsplit(source.canonical_url or source.original)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CapabilityError(
            CapabilityErrorCode.INVALID_LOCATOR,
            "The Feishu Wiki parent URL cannot anchor the created workbook URL.",
        )
    return f"https://{parsed.netloc}/wiki/{node_token}"


def _wiki_parent_state_hash(
    parent: _WikiNode,
    children: tuple[_WikiNode, ...],
) -> str:
    return _hash_json(
        {
            "parent": {
                "space_id": parent.space_id,
                "node_token": parent.node_token,
                "object_token": parent.object_token,
                "object_type": parent.object_type,
                "node_type": parent.node_type,
                "title": parent.title,
            },
            "children": sorted(
                (
                    {
                        "space_id": child.space_id,
                        "node_token": child.node_token,
                        "object_token": child.object_token,
                        "object_type": child.object_type,
                        "parent_node_token": child.parent_node_token,
                        "node_type": child.node_type,
                        "title": child.title,
                    }
                    for child in children
                ),
                key=lambda item: item["node_token"],
            ),
        }
    )


def _ensure_bounded_grid(worksheet: _RemoteWorksheet) -> None:
    if (
        worksheet.row_count < 1
        or worksheet.column_count < 1
        or worksheet.row_count > MAX_GRID_ROWS
        or worksheet.column_count > MAX_GRID_COLUMNS
        or worksheet.row_count * worksheet.column_count > MAX_BLANK_CHECK_CELLS
    ):
        raise CapabilityError(
            CapabilityErrorCode.RETRIEVAL_INCOMPLETE,
            "The complete worksheet grid exceeds the managed-write readback limit.",
            details={"max_full_grid_cells": MAX_BLANK_CHECK_CELLS},
        )


def _ensure_content_blank(
    worksheet: _RemoteWorksheet,
    values: _ValueSnapshot,
) -> None:
    if worksheet.merges:
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The selected worksheet is not content-blank because it contains merges.",
        )
    if any(not _blank(cell) for row in values.values for cell in row):
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The selected worksheet contains values, formulas, links, mentions, or rich text.",
        )


def _create_precondition_hash(
    requested_title: str,
    worksheets: tuple[_RemoteWorksheet, ...],
) -> str:
    return _hash_json(
        {
            "requested_title_casefold": requested_title.casefold(),
            "existing_titles_casefold": sorted(item.title.casefold() for item in worksheets),
        }
    )


def _adoption_state_hash(
    worksheet: _RemoteWorksheet,
    values: _ValueSnapshot,
) -> str:
    return _hash_json(
        {
            "sheet_id": worksheet.sheet_id,
            "title": worksheet.title,
            "index": worksheet.index,
            "hidden": worksheet.hidden,
            "resource_type": worksheet.resource_type,
            "rows": worksheet.row_count,
            "columns": worksheet.column_count,
            "frozen_rows": worksheet.frozen_row_count,
            "frozen_columns": worksheet.frozen_column_count,
            "merges": [item.model_dump(mode="json") for item in worksheet.merges],
            "values": values.values,
            "revision": values.revision,
        }
    )


def _style_payload(style: CellStyle) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "font": {
            "bold": style.bold,
            "italic": style.italic,
            "fontSize": f"{style.font_size_pt}pt/1.5",
            "clean": False,
        },
        "textDecoration": int(style.underline) + 2 * int(style.strikethrough),
        "formatter": "",
        "hAlign": {
            HorizontalAlignment.LEFT: 0,
            HorizontalAlignment.CENTER: 1,
            HorizontalAlignment.RIGHT: 2,
        }[style.horizontal_alignment],
        "vAlign": {
            VerticalAlignment.TOP: 0,
            VerticalAlignment.MIDDLE: 1,
            VerticalAlignment.BOTTOM: 2,
        }[style.vertical_alignment],
        "foreColor": style.text_color,
        "clean": False,
    }
    if style.fill_color is not None:
        payload["backColor"] = style.fill_color
    if style.border_type is BorderType.FULL:
        payload["borderType"] = "FULL_BORDER"
        payload["borderColor"] = style.border_color
    elif style.border_type is not BorderType.NONE:
        raise CapabilityError(
            CapabilityErrorCode.UNSUPPORTED_DELIVERY_SPEC,
            "The current Provider only maps verified full or absent borders.",
        )
    return payload


def _retired_neutral_style_payload() -> dict[str, Any]:
    return {
        "font": {"clean": True},
        "textDecoration": 0,
        "formatter": "",
        "hAlign": 0,
        "vAlign": 0,
        "borderType": "NO_BORDER",
        "clean": False,
    }


def _before(current: WriteStep, candidate: WriteStep) -> bool:
    return WRITE_STEP_ORDER.index(current) < WRITE_STEP_ORDER.index(candidate)


def _revision_before(current: RevisionStep, candidate: RevisionStep) -> bool:
    return REVISION_STEP_ORDER.index(current) < REVISION_STEP_ORDER.index(candidate)


_EXPORT_JOB_DIAGNOSTICS = {
    3: "xlsx_export_job_internal_error",
    107: "xlsx_export_job_document_too_large",
    108: "xlsx_export_job_timeout",
    109: "xlsx_export_job_content_permission_denied",
    110: "xlsx_export_job_permission_denied",
    111: "xlsx_export_job_document_deleted",
    122: "xlsx_export_job_disabled",
    123: "xlsx_export_job_document_not_found",
    6000: "xlsx_export_job_too_many_images",
}


def _export_job_diagnostic(status: object) -> str:
    if isinstance(status, bool) or not isinstance(status, int):
        return "xlsx_export_poll_invalid_job_status"
    return _EXPORT_JOB_DIAGNOSTICS.get(
        status,
        f"xlsx_export_job_status_{status}",
    )


def _requires_retired_style_repair(diagnostic_code: str | None) -> bool:
    if diagnostic_code is None:
        return False
    return re.fullmatch(
        r"xlsx_verify_retired_style:\d+:\d+:"
        r"(?:bold|italic|underline|strikethrough|fill_color|border|"
        r"horizontal_alignment|vertical_alignment|wrap_text|number_format)",
        diagnostic_code,
    ) is not None


def _export_diagnostic(stage: str, error: CapabilityError) -> str:
    if stage == "verify":
        mismatches = error.details.get("mismatches")
        if isinstance(mismatches, list) and mismatches:
            safe_mismatches = [
                mismatch
                for mismatch in mismatches
                if isinstance(mismatch, str)
                and len(mismatch) <= 96
                and re.fullmatch(r"[A-Za-z0-9_.:-]+", mismatch)
            ]
            dimension_diagnostic = _compact_dimension_diagnostic(safe_mismatches)
            if dimension_diagnostic is not None:
                return dimension_diagnostic
            if safe_mismatches:
                return f"xlsx_verify_{safe_mismatches[0]}"

    diagnostic = f"xlsx_export_{stage}_{error.code.value}"
    platform_code = error.details.get("platform_code")
    if (
        isinstance(platform_code, (int, str))
        and not isinstance(platform_code, bool)
    ):
        normalized = str(platform_code)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", normalized):
            diagnostic += f":{normalized}"
    return diagnostic


def _mutation_diagnostic(operation: str, error: CapabilityError) -> str:
    diagnostic = f"{operation}_{error.code.value}"
    platform_code = error.details.get("platform_code")
    if (
        isinstance(platform_code, (int, str))
        and not isinstance(platform_code, bool)
    ):
        normalized = str(platform_code)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", normalized):
            diagnostic += f":{normalized}"
    return diagnostic


def _compact_dimension_diagnostic(mismatches: list[str]) -> str | None:
    prefix = "xlsx_verify_dimensions:"
    tokens: list[str] = []
    for mismatch in mismatches:
        column = re.fullmatch(
            r"column_width:(\d+):raw_milli_([0-9]+|none):"
            r"actual_([0-9]+|none):expected_(\d+)",
            mismatch,
        )
        row = re.fullmatch(
            r"row_height:(\d+):actual_([0-9]+|none):expected_(\d+)",
            mismatch,
        )
        if column is not None:
            token = "c" + "-".join(column.groups())
        elif row is not None:
            token = "r" + "-".join(row.groups())
        else:
            continue
        candidate = prefix + "_".join((*tokens, token))
        if len(candidate) > 128:
            break
        tokens.append(token)
    return prefix + "_".join(tokens) if tokens else None


def _required_sheet_id(target: ProtectedTarget) -> str:
    if target.sheet_id is None or not _SHEET_ID.fullmatch(target.sheet_id):
        raise RemoteMutationFailure("stable_sheet_id_missing", ambiguous=True)
    return target.sheet_id


def _required_spreadsheet_token(target: ProtectedTarget) -> str:
    if (
        target.spreadsheet_token is None
        or not RESOLVED_OBJECT_TOKEN.fullmatch(target.spreadsheet_token)
    ):
        raise RemoteMutationFailure(
            "stable_workbook_id_missing",
            ambiguous=True,
        )
    return target.spreadsheet_token


def _cell(values: tuple[tuple[Any, ...], ...], row: int, column: int) -> Any:
    if row >= len(values) or column >= len(values[row]):
        return None
    return values[row][column]


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _values_equal(actual: Any, expected: Any) -> bool:
    if _blank(actual) and _blank(expected):
        return True
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return actual == expected


def _values_prove_written_spec(
    worksheet: _RemoteWorksheet,
    values: _ValueSnapshot,
    spec: SheetDeliverySpec,
) -> bool:
    if (
        worksheet.row_count < spec.row_count
        or worksheet.column_count < spec.column_count
    ):
        return False
    expected = spec.remote_values()
    for row_index in range(worksheet.row_count):
        for column_index in range(worksheet.column_count):
            actual = _cell(values.values, row_index, column_index)
            if row_index < spec.row_count and column_index < spec.column_count:
                if not _values_equal(actual, expected[row_index][column_index]):
                    return False
            elif not _blank(actual):
                return False
    return True


def _values_prove_blank(values: _ValueSnapshot) -> bool:
    return all(_blank(cell) for row in values.values for cell in row)


def _range_key(value: GridRange) -> tuple[int, int, int, int]:
    return (
        value.row_start,
        value.column_start,
        value.row_end,
        value.column_end,
    )


def _hash_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _contract_error(f"Feishu returned an invalid {field}.")
    return value


def _mutation_data_object(
    response: httpx.Response,
    operation: str,
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise _contract_error(
            f"Feishu returned invalid JSON for {operation}."
        ) from exc
    if not isinstance(payload, dict):
        raise _contract_error(
            f"Feishu returned an invalid response for {operation}."
        )
    platform_code = payload.get("code")
    if platform_code not in {0, "0"}:
        raise http_error(response, operation)
    data = payload.get("data")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise _contract_error(
            f"Feishu returned an invalid data object for {operation}."
        )
    return data


def _contract_error(message: str) -> CapabilityError:
    return CapabilityError(CapabilityErrorCode.PROVIDER_CONTRACT_ERROR, message)
