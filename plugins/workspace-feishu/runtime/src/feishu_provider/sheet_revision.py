from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from capability_contracts.models import OperationEvidence, OperationStatus
from pydantic import BaseModel, ConfigDict, Field

from feishu_provider.locator import ResourceLocator
from feishu_provider.managed_sheets import (
    ConfirmationAction,
    ManagedSheetTargetView,
    RemoteMutationFailure,
)
from feishu_provider.operation_store import (
    ManagedSheetRegistration,
    OperationStore,
    OperationStoreError,
    ProtectedTarget,
    RevisionRecord,
    RevisionState,
    RevisionStep,
)
from feishu_provider.sheet_delivery import (
    DimensionSpan,
    FormulaCell,
    GridRange,
    SheetDeliverySpec,
)

PROVIDER_VERSION = "0.7.0"
REVISION_PREVIEW_TTL = timedelta(minutes=10)


class ManagedSheetRegistrationResolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = "feishu"
    provider_version: str = PROVIDER_VERSION
    operation_id: str = "feishu_managed_sheet_registration_resolve"
    status: OperationStatus = OperationStatus.OK
    task_ref: str
    profile_ref: str
    registration_ref: str
    managed_version: int = Field(ge=1)
    spec_hash: str
    spec_summary: dict[str, int | str]
    source: ResourceLocator
    target: ManagedSheetTargetView
    evidence: OperationEvidence


class RevisionDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_rows: int = Field(ge=1)
    base_columns: int = Field(ge=1)
    next_rows: int = Field(ge=1)
    next_columns: int = Field(ge=1)
    cells_added: int = Field(ge=0)
    cells_modified: int = Field(ge=0)
    cells_cleared: int = Field(ge=0)
    formulas_added: int = Field(ge=0)
    formulas_modified: int = Field(ge=0)
    formulas_removed: int = Field(ge=0)
    merges_added: int = Field(ge=0)
    merges_removed: int = Field(ge=0)
    styled_cells_changed: int = Field(ge=0)
    row_dimensions_changed: int = Field(ge=0)
    column_dimensions_changed: int = Field(ge=0)
    freeze_changed: bool
    retired_ranges: tuple[str, ...] = ()
    retired_cells: int = Field(ge=0)
    retired_nonempty_cells: int = Field(ge=0)
    neutral_rows: tuple[int, ...] = ()
    neutral_columns: tuple[int, ...] = ()


class RevisionConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_ref: str
    registration_ref: str
    profile_ref: str
    workbook_title: str
    workbook_url: str | None = None
    worksheet_title: str
    worksheet_id: str
    current_version: int = Field(ge=1)
    candidate_version: int = Field(ge=2)
    base_spec_hash: str
    next_spec_hash: str
    preview_sha256: str
    diff: RevisionDiffSummary

    def message(self) -> str:
        retired = "、".join(self.diff.retired_ranges) or "无"
        return (
            "即将修订既有受管飞书工作表。"
            f"账号 Profile：{self.profile_ref}；工作簿：{self.workbook_title}；"
            f"工作表：{self.worksheet_title}（{self.worksheet_id}）；"
            f"受管登记：{self.registration_ref}；版本：{self.current_version} → "
            f"{self.candidate_version}；矩形：{self.diff.base_rows}×"
            f"{self.diff.base_columns} → {self.diff.next_rows}×{self.diff.next_columns}；"
            f"单元格新增/修改/清空：{self.diff.cells_added}/"
            f"{self.diff.cells_modified}/{self.diff.cells_cleared}；"
            f"公式新增/修改/移除：{self.diff.formulas_added}/"
            f"{self.diff.formulas_modified}/{self.diff.formulas_removed}；"
            f"合并新增/移除：{self.diff.merges_added}/{self.diff.merges_removed}；"
            f"样式单元格变化：{self.diff.styled_cells_changed}；"
            f"行/列尺寸变化：{self.diff.row_dimensions_changed}/"
            f"{self.diff.column_dimensions_changed}；冻结变化："
            f"{'是' if self.diff.freeze_changed else '否'}；退役区域：{retired}"
            f"（{self.diff.retired_cells} 个单元格，其中非空 "
            f"{self.diff.retired_nonempty_cells} 个）；base_spec："
            f"{self.base_spec_hash}；next_spec：{self.next_spec_hash}；"
            f"preview_sha256：{self.preview_sha256}。修订采用检查点式前向恢复，"
            "不会回滚、切换目标、新建替代工作表或物理删除行列。"
        )


class ManagedSheetRevisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = "feishu"
    provider_version: str = PROVIDER_VERSION
    operation_id: str = "feishu_managed_sheet_revise"
    status: OperationStatus
    registration_ref: str
    operation_ref: str | None = None
    task_ref: str
    profile_ref: str
    managed_version: int = Field(ge=1)
    candidate_managed_version: int | None = Field(default=None, ge=2)
    target: ManagedSheetTargetView
    base_spec_hash: str
    next_spec_hash: str
    preview_sha256: str
    diff: RevisionDiffSummary
    last_completed_step: RevisionStep
    remote_revision: str | None = None
    diagnostic_code: str | None = None
    evidence: OperationEvidence


@dataclass(frozen=True, slots=True)
class ResolvedWorksheet:
    sheet_id: str
    title: str
    index: int
    hidden: bool
    resource_type: str


@dataclass(frozen=True, slots=True)
class ResolvedWorkbook:
    source: ResourceLocator
    profile_ref: str
    spreadsheet_token: str
    workbook_title: str
    workbook_url: str | None
    worksheets: tuple[ResolvedWorksheet, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionProof:
    target: ProtectedTarget
    api_hash: str
    export_hash: str
    remote_revision: str | None
    observed_at: datetime
    warnings: tuple[str, ...] = ()


RevisionConfirmer = Callable[
    [RevisionConfirmationRequest], Awaitable[ConfirmationAction]
]
RevisionStepCallback = Callable[[RevisionStep, str | None], None]


class ManagedSheetRevisionGateway(Protocol):
    async def resolve_workbook(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> ResolvedWorkbook: ...

    async def verify_revision_baseline(
        self,
        *,
        registration: ManagedSheetRegistration,
        task_ref: str,
        spec: SheetDeliverySpec,
    ) -> RevisionProof: ...

    async def execute_revision(
        self,
        *,
        record: RevisionRecord,
        registration: ManagedSheetRegistration,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        diff: RevisionDiffSummary,
        on_step: RevisionStepCallback,
    ) -> RevisionProof: ...

    async def reconcile_revision_final(
        self,
        *,
        record: RevisionRecord,
        registration: ManagedSheetRegistration,
        base_spec: SheetDeliverySpec,
        next_spec: SheetDeliverySpec,
        diff: RevisionDiffSummary,
    ) -> RevisionProof | None: ...

    async def aclose(self) -> None: ...


class ManagedSheetRevisionService:
    def __init__(
        self,
        *,
        gateway: ManagedSheetRevisionGateway,
        store: OperationStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._clock = clock

    @classmethod
    def default(cls) -> ManagedSheetRevisionService:
        from feishu_provider.feishu_sheet_gateway import FeishuManagedSheetsGateway

        return cls(
            gateway=FeishuManagedSheetsGateway.default(),
            store=OperationStore.default(),
        )

    async def resolve_registration(
        self,
        *,
        locator: str,
        task_ref: str,
        profile_ref: str | None,
    ) -> ManagedSheetRegistrationResolveResult:
        remote = await self._gateway.resolve_workbook(
            locator=locator,
            task_ref=task_ref,
            profile_ref=profile_ref,
        )
        matches = self._store.find_registrations(
            profile_ref=remote.profile_ref,
            spreadsheet_token=remote.spreadsheet_token,
            sheet_id=remote.source.worksheet_id,
        )
        if not matches:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "This Feishu worksheet is not registered as a managed worksheet in the selected Profile.",
            )
        if len(matches) != 1:
            raise CapabilityError(
                CapabilityErrorCode.AMBIGUOUS_WRITE,
                "The workbook locator matches more than one managed worksheet; use an exact Sheet link.",
                details={"managed_registration_count": len(matches)},
            )
        registration = matches[0]
        worksheet = _resolved_registered_worksheet(remote, registration)
        target = _refreshed_target(registration.target, remote, worksheet)
        content_hash = _registration_resolution_hash(registration, target)
        warnings = tuple(
            item
            for item, present in (
                (
                    "display_metadata_changed",
                    _display_metadata_changed(registration.target, target),
                ),
                (
                    "legacy_spec_summary_limited",
                    set(registration.spec_summary) == {"content_hash"},
                ),
            )
            if present
        )
        return ManagedSheetRegistrationResolveResult(
            task_ref=task_ref,
            profile_ref=registration.profile_ref,
            registration_ref=registration.registration_ref,
            managed_version=registration.current_version,
            spec_hash=registration.spec_hash,
            spec_summary=registration.spec_summary,
            source=remote.source,
            target=_target_view(target),
            evidence=OperationEvidence(
                observed_at=remote.observed_at.astimezone(UTC).isoformat(
                    timespec="seconds"
                ),
                content_hash=content_hash,
                provider_revision=registration.remote_revision,
                retrieval_complete=True,
                warnings=warnings,
            ),
        )

    async def revise(
        self,
        *,
        registration_ref: str,
        task_ref: str,
        next_spec: SheetDeliverySpec,
        base_spec: SheetDeliverySpec | None,
        confirmer: RevisionConfirmer,
    ) -> ManagedSheetRevisionResult:
        if base_spec is None:
            raise CapabilityError(
                CapabilityErrorCode.BASE_SPEC_REQUIRED,
                "A complete caller-held base_spec is required for managed worksheet revision.",
            )
        base_spec.validate_current_provider_support()
        next_spec.validate_current_provider_support()
        registration = self._store.load_registration(registration_ref)
        existing = self._store.find_revision(
            registration_ref=registration_ref,
            task_ref=task_ref,
            base_spec_hash=base_spec.content_hash,
            next_spec_hash=next_spec.content_hash,
        )
        diff = build_revision_diff(base_spec, next_spec)
        if existing is not None and existing.state is RevisionState.DELIVERED:
            return _revision_result(
                record=existing,
                registration=registration,
                diff=diff,
                retrieval_complete=True,
            )
        if existing is not None and existing.state in {
            RevisionState.DECLINED,
            RevisionState.CANCELLED,
        }:
            raise CapabilityError(
                CapabilityErrorCode.CONFIRMATION_REQUIRED,
                "The previous managed revision confirmation ended; use a new task_ref for another attempt.",
                details={"operation_ref": existing.operation_ref},
            )
        if not _hashes_equal(registration.spec_hash, base_spec.content_hash):
            raise CapabilityError(
                CapabilityErrorCode.WRITE_CONFLICT,
                "The supplied base_spec is not the current managed worksheet version.",
                details={
                    "registration_ref": registration_ref,
                    "managed_version": registration.current_version,
                    "current_spec_hash": registration.spec_hash,
                },
            )

        proof: RevisionProof | None = None
        if existing is None:
            active = self._store.load_active_revision(registration_ref)
            if active is not None:
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "Another managed worksheet revision is still active.",
                    details={"operation_ref": active.operation_ref},
                )
            proof = await self._verify_baseline(
                registration=registration,
                task_ref=task_ref,
                spec=base_spec,
            )
            registration = self._store.update_registration_target(
                registration_ref,
                proof.target,
                remote_revision=proof.remote_revision,
            )
            preview_hash = _revision_preview_hash(
                registration=registration,
                task_ref=task_ref,
                base_spec_hash=base_spec.content_hash,
                next_spec_hash=next_spec.content_hash,
                diff=diff,
            )
            if _hashes_equal(base_spec.content_hash, next_spec.content_hash):
                return _no_change_result(
                    registration=registration,
                    task_ref=task_ref,
                    spec=next_spec,
                    preview_hash=preview_hash,
                    diff=diff,
                    proof=proof,
                )
            existing = self._store.create_revision(
                registration_ref=registration_ref,
                task_ref=task_ref,
                base_spec_hash=base_spec.content_hash,
                next_spec_hash=next_spec.content_hash,
                next_spec_summary=next_spec.summary(),
                preview_hash=preview_hash,
                diff_summary=diff.model_dump(mode="json"),
                base_api_hash=proof.api_hash,
                base_export_hash=proof.export_hash,
                expires_at=self._now() + REVISION_PREVIEW_TTL,
            )

        record = existing
        if record.state is RevisionState.DELIVERED:
            return _revision_result(
                record=record,
                registration=self._store.load_registration(registration_ref),
                diff=diff,
                retrieval_complete=True,
            )
        if record.state in {RevisionState.DECLINED, RevisionState.CANCELLED}:
            raise CapabilityError(
                CapabilityErrorCode.CONFIRMATION_REQUIRED,
                "The previous managed revision confirmation ended; use a new task_ref for another attempt.",
                details={"operation_ref": record.operation_ref},
            )
        if record.state is RevisionState.PREVIEWED and record.expires_at <= self._now():
            self._store.record_unaccepted_revision(
                record.operation_ref,
                action=RevisionState.CANCELLED,
            )
            raise CapabilityError(
                CapabilityErrorCode.PREVIEW_EXPIRED,
                "The managed worksheet revision preview expired; use a new task_ref.",
            )
        if record.state is RevisionState.EXECUTING:
            record = self._store.require_revision_recovery(
                record.operation_ref,
                ambiguous=True,
                diagnostic_code="interrupted_revision_execution",
            )

        if record.state is RevisionState.PREVIEWED:
            action = await confirmer(
                _confirmation_request(record, registration, diff)
            )
            if action is not ConfirmationAction.ACCEPT:
                record = self._store.record_unaccepted_revision(
                    record.operation_ref,
                    action=(
                        RevisionState.DECLINED
                        if action is ConfirmationAction.DECLINE
                        else RevisionState.CANCELLED
                    ),
                )
                return _revision_result(
                    record=record,
                    registration=registration,
                    diff=diff,
                    retrieval_complete=True,
                )
            record = self._store.authorize_revision(
                operation_ref=record.operation_ref,
                task_ref=task_ref,
                base_spec_hash=base_spec.content_hash,
                next_spec_hash=next_spec.content_hash,
            )
        elif record.state in {
            RevisionState.RECOVERY_REQUIRED,
            RevisionState.VERIFICATION_INCOMPLETE,
        }:
            try:
                proof = await self._gateway.reconcile_revision_final(
                    record=record,
                    registration=registration,
                    base_spec=base_spec,
                    next_spec=next_spec,
                    diff=diff,
                )
            except RemoteMutationFailure as exc:
                record = self._store.resume_revision(
                    operation_ref=record.operation_ref,
                    task_ref=task_ref,
                    base_spec_hash=base_spec.content_hash,
                    next_spec_hash=next_spec.content_hash,
                )
                record = self._store.require_revision_recovery(
                    record.operation_ref,
                    ambiguous=exc.ambiguous,
                    diagnostic_code=exc.diagnostic_code,
                    verification_incomplete=exc.verification_incomplete,
                )
                return _revision_result(
                    record=record,
                    registration=registration,
                    diff=diff,
                    retrieval_complete=False,
                )
            record = self._store.resume_revision(
                operation_ref=record.operation_ref,
                task_ref=task_ref,
                base_spec_hash=base_spec.content_hash,
                next_spec_hash=next_spec.content_hash,
            )
            if proof is not None:
                if _revision_before(record.last_completed_step, RevisionStep.API_VERIFIED):
                    record = self._store.mark_revision_step(
                        record.operation_ref,
                        RevisionStep.API_VERIFIED,
                        remote_revision=proof.remote_revision,
                    )
                if _revision_before(record.last_completed_step, RevisionStep.EXPORT_VERIFIED):
                    record = self._store.mark_revision_step(
                        record.operation_ref,
                        RevisionStep.EXPORT_VERIFIED,
                        remote_revision=proof.remote_revision,
                    )
                return self._commit(
                    record=record,
                    registration=registration,
                    diff=diff,
                    proof=proof,
                )

        try:
            proof = await self._gateway.execute_revision(
                record=record,
                registration=registration,
                base_spec=base_spec,
                next_spec=next_spec,
                diff=diff,
                on_step=lambda step, revision: self._store.mark_revision_step(
                    record.operation_ref,
                    step,
                    remote_revision=revision,
                ),
            )
        except RemoteMutationFailure as exc:
            record = self._store.require_revision_recovery(
                record.operation_ref,
                ambiguous=exc.ambiguous,
                diagnostic_code=exc.diagnostic_code,
                verification_incomplete=exc.verification_incomplete,
            )
            return _revision_result(
                record=record,
                registration=registration,
                diff=diff,
                retrieval_complete=False,
            )
        except CapabilityError as exc:
            self._store.require_revision_recovery(
                record.operation_ref,
                ambiguous=False,
                diagnostic_code=f"capability_{exc.code.value}",
            )
            raise CapabilityError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                details={**exc.details, "operation_ref": record.operation_ref},
            ) from exc

        return self._commit(
            record=self._store.load_revision(record.operation_ref),
            registration=registration,
            diff=diff,
            proof=proof,
        )

    def _commit(
        self,
        *,
        record: RevisionRecord,
        registration: ManagedSheetRegistration,
        diff: RevisionDiffSummary,
        proof: RevisionProof,
    ) -> ManagedSheetRevisionResult:
        delivery_hash = _proof_hash(
            record.next_spec_hash,
            proof.api_hash,
            proof.export_hash,
        )
        record, registration = self._store.commit_revision(
            record.operation_ref,
            delivery_hash=delivery_hash,
            remote_revision=proof.remote_revision,
            target=proof.target,
        )
        return _revision_result(
            record=record,
            registration=registration,
            diff=diff,
            proof=proof,
        )

    async def _verify_baseline(
        self,
        *,
        registration: ManagedSheetRegistration,
        task_ref: str,
        spec: SheetDeliverySpec,
    ) -> RevisionProof:
        try:
            return await self._gateway.verify_revision_baseline(
                registration=registration,
                task_ref=task_ref,
                spec=spec,
            )
        except RemoteMutationFailure as exc:
            raise CapabilityError(
                CapabilityErrorCode.BASELINE_VERIFICATION_INCOMPLETE,
                "API and XLSX readback did not completely prove the managed worksheet baseline; no write was attempted.",
                retryable=True,
                details={"diagnostic_code": exc.diagnostic_code},
            ) from exc
        except CapabilityError as exc:
            if exc.code in {
                CapabilityErrorCode.PRECONDITION_FAILED,
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                CapabilityErrorCode.PERMISSION_DENIED,
                CapabilityErrorCode.AUTH_REQUIRED,
            }:
                raise
            raise CapabilityError(
                CapabilityErrorCode.BASELINE_VERIFICATION_INCOMPLETE,
                "API and XLSX readback did not completely prove the managed worksheet baseline; no write was attempted.",
                retryable=exc.retryable,
                details={"cause": exc.code.value},
            ) from exc

    async def aclose(self) -> None:
        await self._gateway.aclose()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OperationStoreError("Managed revision service clock must be timezone-aware")
        return value.astimezone(UTC)


def build_revision_diff(
    base_spec: SheetDeliverySpec,
    next_spec: SheetDeliverySpec,
) -> RevisionDiffSummary:
    cells_added = 0
    cells_modified = 0
    cells_cleared = 0
    formulas_added = 0
    formulas_modified = 0
    formulas_removed = 0
    styled_cells_changed = 0
    retired_nonempty_cells = 0
    max_rows = max(base_spec.row_count, next_spec.row_count)
    max_columns = max(base_spec.column_count, next_spec.column_count)
    for row in range(max_rows):
        for column in range(max_columns):
            base_exists = row < base_spec.row_count and column < base_spec.column_count
            next_exists = row < next_spec.row_count and column < next_spec.column_count
            base = base_spec.values[row][column] if base_exists else None
            next_ = next_spec.values[row][column] if next_exists else None
            base_blank = _blank_cell(base)
            next_blank = _blank_cell(next_)
            if base_blank and not next_blank:
                cells_added += 1
            elif not base_blank and next_blank:
                cells_cleared += 1
            elif not base_blank and not next_blank and not _cells_equal(base, next_):
                cells_modified += 1
            base_formula = isinstance(base, FormulaCell)
            next_formula = isinstance(next_, FormulaCell)
            if not base_formula and next_formula:
                formulas_added += 1
            elif base_formula and not next_formula:
                formulas_removed += 1
            elif base_formula and next_formula and not _cells_equal(base, next_):
                formulas_modified += 1
            if base_exists and not next_exists and not base_blank:
                retired_nonempty_cells += 1
            if next_exists:
                base_style = base_spec.style_at(row, column) if base_exists else None
                if base_style != next_spec.style_at(row, column):
                    styled_cells_changed += 1

    base_merges = {_range_key(item) for item in base_spec.merges}
    next_merges = {_range_key(item) for item in next_spec.merges}
    retired = revision_retired_ranges(base_spec, next_spec)
    neutral_rows = tuple(range(next_spec.row_count, base_spec.row_count))
    neutral_columns = tuple(range(next_spec.column_count, base_spec.column_count))
    base_row_sizes = _dimension_sizes(
        base_spec.row_count,
        base_spec.default_row_height_px,
        base_spec.row_heights,
    )
    next_row_sizes = _dimension_sizes(
        next_spec.row_count,
        next_spec.default_row_height_px,
        next_spec.row_heights,
    )
    base_column_sizes = _dimension_sizes(
        base_spec.column_count,
        base_spec.default_column_width_px,
        base_spec.column_widths,
    )
    next_column_sizes = _dimension_sizes(
        next_spec.column_count,
        next_spec.default_column_width_px,
        next_spec.column_widths,
    )
    row_dimensions_changed = _dimension_change_count(
        base_row_sizes,
        next_row_sizes,
        retired_default=24,
    )
    column_dimensions_changed = _dimension_change_count(
        base_column_sizes,
        next_column_sizes,
        retired_default=100,
    )
    return RevisionDiffSummary(
        base_rows=base_spec.row_count,
        base_columns=base_spec.column_count,
        next_rows=next_spec.row_count,
        next_columns=next_spec.column_count,
        cells_added=cells_added,
        cells_modified=cells_modified,
        cells_cleared=cells_cleared,
        formulas_added=formulas_added,
        formulas_modified=formulas_modified,
        formulas_removed=formulas_removed,
        merges_added=len(next_merges - base_merges),
        merges_removed=len(base_merges - next_merges),
        styled_cells_changed=styled_cells_changed,
        row_dimensions_changed=row_dimensions_changed,
        column_dimensions_changed=column_dimensions_changed,
        freeze_changed=(
            base_spec.frozen_row_count != next_spec.frozen_row_count
            or base_spec.frozen_column_count != next_spec.frozen_column_count
        ),
        retired_ranges=tuple(_unqualified_a1(item) for item in retired),
        retired_cells=sum(item.cell_count for item in retired),
        retired_nonempty_cells=retired_nonempty_cells,
        neutral_rows=neutral_rows,
        neutral_columns=neutral_columns,
    )


def revision_retired_ranges(
    base_spec: SheetDeliverySpec,
    next_spec: SheetDeliverySpec,
) -> tuple[GridRange, ...]:
    ranges: list[GridRange] = []
    shared_rows = min(base_spec.row_count, next_spec.row_count)
    if base_spec.row_count > next_spec.row_count:
        ranges.append(
            GridRange(
                row_start=next_spec.row_count,
                row_end=base_spec.row_count,
                column_start=0,
                column_end=base_spec.column_count,
            )
        )
    if base_spec.column_count > next_spec.column_count and shared_rows > 0:
        ranges.append(
            GridRange(
                row_start=0,
                row_end=shared_rows,
                column_start=next_spec.column_count,
                column_end=base_spec.column_count,
            )
        )
    return tuple(ranges)


def revision_managed_ranges(
    base_spec: SheetDeliverySpec,
    next_spec: SheetDeliverySpec,
) -> tuple[GridRange, ...]:
    return (next_spec.delivery_range, *revision_retired_ranges(base_spec, next_spec))


def _confirmation_request(
    record: RevisionRecord,
    registration: ManagedSheetRegistration,
    diff: RevisionDiffSummary,
) -> RevisionConfirmationRequest:
    target = registration.target
    if target.sheet_id is None or target.sheet_title is None:
        raise OperationStoreError("Managed revision target has no stable worksheet identity")
    return RevisionConfirmationRequest(
        operation_ref=record.operation_ref,
        registration_ref=registration.registration_ref,
        profile_ref=registration.profile_ref,
        workbook_title=target.workbook_title,
        workbook_url=target.workbook_url,
        worksheet_title=target.sheet_title,
        worksheet_id=target.sheet_id,
        current_version=registration.current_version,
        candidate_version=record.candidate_version,
        base_spec_hash=record.base_spec_hash,
        next_spec_hash=record.next_spec_hash,
        preview_sha256=record.preview_hash,
        diff=diff,
    )


def _revision_result(
    *,
    record: RevisionRecord,
    registration: ManagedSheetRegistration,
    diff: RevisionDiffSummary,
    proof: RevisionProof | None = None,
    retrieval_complete: bool | None = None,
) -> ManagedSheetRevisionResult:
    status = {
        RevisionState.DELIVERED: OperationStatus.DELIVERED,
        RevisionState.DECLINED: OperationStatus.DECLINED,
        RevisionState.CANCELLED: OperationStatus.CANCELLED,
        RevisionState.RECOVERY_REQUIRED: OperationStatus.RECOVERY_REQUIRED,
        RevisionState.VERIFICATION_INCOMPLETE: OperationStatus.VERIFICATION_INCOMPLETE,
    }.get(record.state)
    if status is None:
        raise OperationStoreError(
            f"Revision state {record.state.value} cannot be returned as a final result"
        )
    complete = retrieval_complete if retrieval_complete is not None else proof is not None
    warnings = list(proof.warnings if proof is not None else ())
    if record.diagnostic_code:
        warnings.append(record.diagnostic_code)
    content_hash = (
        _proof_hash(record.next_spec_hash, proof.api_hash, proof.export_hash)
        if proof is not None
        else record.delivery_hash or record.preview_hash
    )
    return ManagedSheetRevisionResult(
        status=status,
        registration_ref=record.registration_ref,
        operation_ref=record.operation_ref,
        task_ref=record.task_ref,
        profile_ref=registration.profile_ref,
        managed_version=registration.current_version,
        candidate_managed_version=record.candidate_version,
        target=_target_view(proof.target if proof is not None else registration.target),
        base_spec_hash=record.base_spec_hash,
        next_spec_hash=record.next_spec_hash,
        preview_sha256=record.preview_hash,
        diff=diff,
        last_completed_step=record.last_completed_step,
        remote_revision=(proof.remote_revision if proof is not None else record.remote_revision),
        diagnostic_code=record.diagnostic_code,
        evidence=OperationEvidence(
            observed_at=(
                proof.observed_at if proof is not None else record.updated_at
            ).astimezone(UTC).isoformat(timespec="seconds"),
            content_hash=content_hash,
            provider_revision=(
                proof.remote_revision if proof is not None else record.remote_revision
            ),
            retrieval_complete=complete,
            warnings=tuple(dict.fromkeys(warnings)),
        ),
    )


def _no_change_result(
    *,
    registration: ManagedSheetRegistration,
    task_ref: str,
    spec: SheetDeliverySpec,
    preview_hash: str,
    diff: RevisionDiffSummary,
    proof: RevisionProof,
) -> ManagedSheetRevisionResult:
    return ManagedSheetRevisionResult(
        status=OperationStatus.NO_CHANGE,
        registration_ref=registration.registration_ref,
        task_ref=task_ref,
        profile_ref=registration.profile_ref,
        managed_version=registration.current_version,
        target=_target_view(proof.target),
        base_spec_hash=spec.content_hash,
        next_spec_hash=spec.content_hash,
        preview_sha256=preview_hash,
        diff=diff,
        last_completed_step=RevisionStep.NONE,
        remote_revision=proof.remote_revision,
        evidence=OperationEvidence(
            observed_at=proof.observed_at.astimezone(UTC).isoformat(
                timespec="seconds"
            ),
            content_hash=_proof_hash(
                spec.content_hash, proof.api_hash, proof.export_hash
            ),
            provider_revision=proof.remote_revision,
            retrieval_complete=True,
            warnings=tuple(dict.fromkeys(("baseline_double_verified", *proof.warnings))),
        ),
    )


def _resolved_registered_worksheet(
    remote: ResolvedWorkbook,
    registration: ManagedSheetRegistration,
) -> ResolvedWorksheet:
    sheet_id = registration.target.sheet_id
    matches = [item for item in remote.worksheets if item.sheet_id == sheet_id]
    if len(matches) != 1:
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The registered Feishu worksheet no longer exists in the workbook.",
        )
    worksheet = matches[0]
    if worksheet.hidden or worksheet.resource_type != "sheet":
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The registered Feishu worksheet is hidden or no longer a normal worksheet.",
        )
    return worksheet


def _refreshed_target(
    target: ProtectedTarget,
    remote: ResolvedWorkbook,
    worksheet: ResolvedWorksheet,
) -> ProtectedTarget:
    if target.spreadsheet_token != remote.spreadsheet_token or target.sheet_id != worksheet.sheet_id:
        raise CapabilityError(
            CapabilityErrorCode.PRECONDITION_FAILED,
            "The managed Feishu worksheet stable identity changed.",
        )
    return target.model_copy(
        update={
            "workbook_title": remote.workbook_title,
            "workbook_url": remote.workbook_url,
            "sheet_title": worksheet.title,
            "sheet_index": worksheet.index,
        }
    )


def _target_view(target: ProtectedTarget) -> ManagedSheetTargetView:
    if target.sheet_id is None or target.sheet_title is None:
        raise OperationStoreError("Managed revision target has no stable worksheet identity")
    return ManagedSheetTargetView(
        workbook_title=target.workbook_title,
        workbook_url=target.workbook_url,
        worksheet_id=target.sheet_id,
        worksheet_title=target.sheet_title,
        worksheet_index=target.sheet_index,
    )


def _display_metadata_changed(base: ProtectedTarget, current: ProtectedTarget) -> bool:
    return (
        base.workbook_title != current.workbook_title
        or base.workbook_url != current.workbook_url
        or base.sheet_title != current.sheet_title
        or base.sheet_index != current.sheet_index
    )


def _registration_resolution_hash(
    registration: ManagedSheetRegistration,
    target: ProtectedTarget,
) -> str:
    payload = {
        "registration_ref": registration.registration_ref,
        "managed_version": registration.current_version,
        "spec_hash": registration.spec_hash,
        "target": {
            "spreadsheet_token_hash": _text_hash(target.spreadsheet_token),
            "sheet_id_hash": _text_hash(target.sheet_id or ""),
            "workbook_title": target.workbook_title,
            "worksheet_title": target.sheet_title,
            "worksheet_index": target.sheet_index,
        },
    }
    return _hash_json(payload)


def _revision_preview_hash(
    *,
    registration: ManagedSheetRegistration,
    task_ref: str,
    base_spec_hash: str,
    next_spec_hash: str,
    diff: RevisionDiffSummary,
) -> str:
    target = registration.target
    return _hash_json(
        {
            "registration_ref": registration.registration_ref,
            "task_ref": task_ref,
            "profile_ref": registration.profile_ref,
            "current_version": registration.current_version,
            "candidate_version": registration.current_version + 1,
            "spreadsheet_token_hash": _text_hash(target.spreadsheet_token),
            "sheet_id_hash": _text_hash(target.sheet_id or ""),
            "base_spec_hash": base_spec_hash,
            "next_spec_hash": next_spec_hash,
            "diff": diff.model_dump(mode="json"),
        }
    )


def _proof_hash(spec_hash: str, api_hash: str, export_hash: str) -> str:
    return _text_hash(f"{spec_hash}\x1f{api_hash}\x1f{export_hash}")


def _hash_json(value: Any) -> str:
    return _text_hash(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _text_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hashes_equal(left: str, right: str) -> bool:
    return left == right


def _blank_cell(value: object) -> bool:
    return value is None or value == ""


def _cells_equal(left: object, right: object) -> bool:
    if isinstance(left, FormulaCell) and isinstance(right, FormulaCell):
        return left.formula == right.formula
    return type(left) is type(right) and left == right


def _range_key(value: GridRange) -> tuple[int, int, int, int]:
    return (
        value.row_start,
        value.row_end,
        value.column_start,
        value.column_end,
    )


def _dimension_sizes(
    extent: int,
    default: int,
    spans: tuple[DimensionSpan, ...],
) -> tuple[int, ...]:
    values = [default] * extent
    for span in spans:
        for index in range(span.start_index, span.end_index):
            values[index] = span.pixel_size
    return tuple(values)


def _dimension_change_count(
    base: tuple[int, ...],
    next_: tuple[int, ...],
    *,
    retired_default: int,
) -> int:
    maximum = max(len(base), len(next_))
    changed = 0
    for index in range(maximum):
        before = base[index] if index < len(base) else None
        after = next_[index] if index < len(next_) else retired_default
        if before != after:
            changed += 1
    return changed


def _unqualified_a1(value: GridRange) -> str:
    qualified = value.a1("sheet")
    return qualified.partition("!")[2]


def _revision_before(current: RevisionStep, candidate: RevisionStep) -> bool:
    order = tuple(RevisionStep)
    return order.index(current) < order.index(candidate)
