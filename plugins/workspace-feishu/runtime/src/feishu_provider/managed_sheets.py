from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Awaitable, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from capability_contracts.errors import CapabilityError
from capability_contracts.models import OperationEvidence, OperationStatus
from feishu_provider.locator import ResourceLocator
from feishu_provider.operation_store import (
    ManagedSheetRegistration,
    OperationRecord,
    OperationState,
    OperationStore,
    OperationStoreError,
    ProtectedTarget,
    WriteStep,
)
from feishu_provider.sheet_delivery import PlacementMode, SheetDeliverySpec


PROVIDER_VERSION = "0.7.0"
PREVIEW_TTL = timedelta(minutes=10)


class ManagedSheetTargetView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workbook_title: str
    workbook_url: str | None = None
    wiki_url: str | None = None
    wiki_space_id: str | None = None
    wiki_node_token: str | None = None
    parent_wiki_node_token: str | None = None
    worksheet_id: str | None = None
    worksheet_title: str | None = None
    worksheet_index: int | None = Field(default=None, ge=0)


class ManagedSheetPreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = "feishu"
    provider_version: str = PROVIDER_VERSION
    operation_id: str = "feishu_managed_sheet_preview"
    status: OperationStatus = OperationStatus.PREVIEW_READY
    operation_ref: str
    task_ref: str
    profile_ref: str
    placement_mode: PlacementMode
    source: ResourceLocator
    target: ManagedSheetTargetView
    spec_summary: dict[str, int | str]
    preview_expires_at: str
    disclosures: tuple[str, ...]
    evidence: OperationEvidence


class ManagedSheetApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = "feishu"
    provider_version: str = PROVIDER_VERSION
    operation_id: str = "feishu_managed_sheet_apply"
    status: OperationStatus
    operation_ref: str
    registration_ref: str | None = None
    managed_version: int | None = Field(default=None, ge=1)
    task_ref: str
    profile_ref: str
    placement_mode: PlacementMode
    target: ManagedSheetTargetView
    spec_hash: str
    last_completed_step: WriteStep
    remote_revision: str | None = None
    diagnostic_code: str | None = None
    evidence: OperationEvidence


class ConfirmationAction(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"


class WriteConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_ref: str
    workbook_title: str
    workbook_url: str | None = None
    worksheet_title: str | None = None
    placement_mode: PlacementMode
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    cells: int = Field(ge=1)
    nonempty_cells: int = Field(ge=0)
    formula_cells: int = Field(ge=0)
    merge_ranges: int = Field(ge=0)
    disclosures: tuple[str, ...]

    def message(self) -> str:
        if self.placement_mode is PlacementMode.ADOPT_BLANK_SHEET:
            action = "接管指定的内容空白工作表"
        elif self.placement_mode is PlacementMode.CREATE_NEW_SHEET:
            action = "在指定工作簿中新建工作表"
        else:
            action = "在指定 Wiki 父节点下新建电子表格文件，并写入其唯一默认工作表"
        disclosure = "；".join(self.disclosures)
        worksheet = self.worksheet_title or "创建后读取并固定唯一默认工作表"
        target_location = (
            f"；目标定位：{self.workbook_url}" if self.workbook_url else ""
        )
        return (
            f"即将{action}。工作簿：{self.workbook_title}{target_location}；工作表："
            f"{worksheet}；交付矩形：{self.rows} 行 × "
            f"{self.columns} 列（{self.cells} 个单元格）；非空单元格 "
            f"{self.nonempty_cells} 个；公式 {self.formula_cells} 个；"
            f"合并区域 {self.merge_ranges} 个。{disclosure}"
        )


WriteConfirmer = Callable[
    [WriteConfirmationRequest],
    Awaitable[ConfirmationAction],
]


@dataclass(frozen=True, slots=True)
class RemotePreview:
    source: ResourceLocator
    profile_ref: str
    target: ProtectedTarget
    observed_at: datetime
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionProof:
    target: ProtectedTarget
    api_hash: str
    export_hash: str
    remote_revision: str | None
    observed_at: datetime
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryProgress:
    completed_step: WriteStep
    remote_revision: str | None
    observed_at: datetime
    warnings: tuple[str, ...] = ()
    target: ProtectedTarget | None = None


class RemoteMutationFailure(RuntimeError):
    """Safe failure raised after an execution grant has been consumed."""

    def __init__(
        self,
        diagnostic_code: str,
        *,
        ambiguous: bool,
        verification_incomplete: bool = False,
    ) -> None:
        self.diagnostic_code = diagnostic_code
        self.ambiguous = ambiguous
        self.verification_incomplete = verification_incomplete
        super().__init__(diagnostic_code)


TargetCallback = Callable[[ProtectedTarget, str | None], None]
WorkbookCallback = Callable[[ProtectedTarget, str | None], None]
StepCallback = Callable[[WriteStep, str | None], None]


class ManagedSheetsGateway(Protocol):
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
    ) -> RemotePreview: ...

    async def execute(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
        on_workbook: WorkbookCallback,
        on_target: TargetCallback,
        on_step: StepCallback,
    ) -> ExecutionProof: ...

    async def reconcile_final(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
    ) -> ExecutionProof | None: ...

    async def reconcile_progress(
        self,
        *,
        record: OperationRecord,
        spec: SheetDeliverySpec,
    ) -> RecoveryProgress | None: ...

    async def aclose(self) -> None: ...


class ManagedSheetWriteService:
    def __init__(
        self,
        *,
        gateway: ManagedSheetsGateway,
        store: OperationStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._clock = clock

    @classmethod
    def default(cls) -> ManagedSheetWriteService:
        from feishu_provider.feishu_sheet_gateway import FeishuManagedSheetsGateway

        return cls(
            gateway=FeishuManagedSheetsGateway.default(),
            store=OperationStore.default(),
        )

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
    ) -> ManagedSheetPreviewResult:
        spec.validate_current_provider_support()
        remote = await self._gateway.preview(
            locator=locator,
            task_ref=task_ref,
            profile_ref=profile_ref,
            placement_mode=placement_mode,
            requested_sheet_title=requested_sheet_title,
            spec=spec,
            requested_workbook_title=requested_workbook_title,
        )
        now = self._now()
        expires_at = now + PREVIEW_TTL
        preview_hash = _preview_hash(
            task_ref=task_ref,
            profile_ref=remote.profile_ref,
            placement_mode=placement_mode,
            spec_hash=spec.content_hash,
            target=remote.target,
        )
        record = self._store.create_preview(
            task_ref=task_ref,
            profile_ref=remote.profile_ref,
            placement_mode=placement_mode,
            spec_hash=spec.content_hash,
            preview_hash=preview_hash,
            target=remote.target,
            expires_at=expires_at,
        )
        disclosures = _disclosures(placement_mode, remote.warnings)
        return ManagedSheetPreviewResult(
            operation_ref=record.operation_ref,
            task_ref=task_ref,
            profile_ref=remote.profile_ref,
            placement_mode=placement_mode,
            source=remote.source,
            target=_target_view(remote.target),
            spec_summary=spec.summary(),
            preview_expires_at=expires_at.isoformat(timespec="seconds"),
            disclosures=disclosures,
            evidence=OperationEvidence(
                observed_at=remote.observed_at.astimezone(UTC).isoformat(
                    timespec="seconds"
                ),
                content_hash=preview_hash,
                provider_revision=remote.target.initial_revision,
                retrieval_complete=True,
                warnings=remote.warnings,
            ),
        )

    async def apply(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        spec: SheetDeliverySpec,
        confirmer: WriteConfirmer,
    ) -> ManagedSheetApplyResult:
        recovery_warnings: tuple[str, ...] = ()
        spec.validate_current_provider_support()
        record = self._store.load_matching(
            operation_ref=operation_ref,
            task_ref=task_ref,
            spec_hash=spec.content_hash,
        )
        if record.state is OperationState.DELIVERED:
            registration = self._store.load_registration_for_operation(operation_ref)
            if record.delivery_hash is None:
                try:
                    proof = await self._gateway.reconcile_final(
                        record=record,
                        spec=spec,
                    )
                except RemoteMutationFailure as exc:
                    return _result_from_record(
                        record,
                        registration=registration,
                        retrieval_complete=False,
                        additional_warnings=(
                            "delivery_evidence_refresh_incomplete",
                            exc.diagnostic_code,
                        ),
                    )
                if proof is None:
                    return _result_from_record(
                        record,
                        registration=registration,
                        retrieval_complete=False,
                        additional_warnings=(
                            "delivery_evidence_refresh_incomplete",
                        ),
                    )
                delivery_hash = _proof_hash(
                    record.spec_hash,
                    proof.api_hash,
                    proof.export_hash,
                )
                record = self._store.record_delivery_evidence(
                    operation_ref,
                    delivery_hash=delivery_hash,
                    remote_revision=proof.remote_revision,
                )
                return _result_from_record(
                    record,
                    registration=registration,
                    retrieval_complete=True,
                )
            return _result_from_record(
                record,
                registration=registration,
                retrieval_complete=True,
            )
        if record.state is OperationState.EXECUTING:
            record = self._store.require_recovery(
                operation_ref,
                ambiguous=True,
                diagnostic_code="interrupted_execution",
            )

        if record.state is OperationState.PREVIEWED:
            action = await confirmer(_confirmation_request(record, spec))
            if action is not ConfirmationAction.ACCEPT:
                state = (
                    OperationState.DECLINED
                    if action is ConfirmationAction.DECLINE
                    else OperationState.CANCELLED
                )
                record = self._store.record_unaccepted_confirmation(
                    operation_ref,
                    action=state,
                )
                return _result_from_record(record, retrieval_complete=True)
            record = self._store.authorize_execution(
                operation_ref=operation_ref,
                task_ref=task_ref,
                spec_hash=spec.content_hash,
            )
        elif record.state in {
            OperationState.RECOVERY_REQUIRED,
            OperationState.VERIFICATION_INCOMPLETE,
        }:
            if record.ambiguous and not _is_preflight_capability_recovery(record):
                try:
                    proof = await self._gateway.reconcile_final(
                        record=record,
                        spec=spec,
                    )
                except RemoteMutationFailure as exc:
                    self._store.resume_execution(
                        operation_ref=operation_ref,
                        task_ref=task_ref,
                        spec_hash=spec.content_hash,
                    )
                    record = self._store.require_recovery(
                        operation_ref,
                        ambiguous=exc.ambiguous,
                        diagnostic_code=exc.diagnostic_code,
                        verification_incomplete=exc.verification_incomplete,
                    )
                    return _result_from_record(record, retrieval_complete=False)
                if proof is None:
                    progress = await self._gateway.reconcile_progress(
                        record=record,
                        spec=spec,
                    )
                    if progress is None:
                        return _result_from_record(record, retrieval_complete=False)
                    record = self._store.resume_execution(
                        operation_ref=operation_ref,
                        task_ref=task_ref,
                        spec_hash=spec.content_hash,
                    )
                    if (
                        progress.completed_step is WriteStep.WORKBOOK_CREATED
                        and progress.target is not None
                    ):
                        record = self._store.register_workbook(
                            operation_ref,
                            progress.target,
                            remote_revision=progress.remote_revision,
                        )
                    elif (
                        progress.completed_step is WriteStep.TARGET_REGISTERED
                        and progress.target is not None
                    ):
                        record = self._store.register_target(
                            operation_ref,
                            progress.target,
                            remote_revision=progress.remote_revision,
                        )
                    else:
                        if progress.target is not None:
                            record = self._store.update_target(
                                operation_ref,
                                progress.target,
                                remote_revision=progress.remote_revision,
                            )
                        record = self._store.mark_step(
                            operation_ref,
                            progress.completed_step,
                            remote_revision=progress.remote_revision,
                        )
                    recovery_warnings = progress.warnings
                else:
                    record = self._store.resume_execution(
                        operation_ref=operation_ref,
                        task_ref=task_ref,
                        spec_hash=spec.content_hash,
                    )
                    if record.target.sheet_id is None:
                        record = self._store.register_target(
                            operation_ref,
                            proof.target,
                            remote_revision=proof.remote_revision,
                        )
                    self._store.mark_step(
                        operation_ref,
                        WriteStep.API_VERIFIED,
                        remote_revision=proof.remote_revision,
                    )
                    self._store.mark_step(
                        operation_ref,
                        WriteStep.EXPORT_VERIFIED,
                        remote_revision=proof.remote_revision,
                    )
                    record = self._store.mark_delivered(
                        operation_ref,
                        spec_hash=spec.content_hash,
                        spec_summary=spec.summary(),
                        delivery_hash=_proof_hash(
                            spec.content_hash,
                            proof.api_hash,
                            proof.export_hash,
                        ),
                        remote_revision=proof.remote_revision,
                    )
                    return _result_from_record(
                        record,
                        registration=self._store.load_registration_for_operation(
                            operation_ref
                        ),
                        proof=proof,
                    )
            else:
                record = self._store.resume_execution(
                    operation_ref=operation_ref,
                    task_ref=task_ref,
                    spec_hash=spec.content_hash,
                )

        try:
            proof = await self._gateway.execute(
                record=record,
                spec=spec,
                on_workbook=lambda target, revision: self._store.register_workbook(
                    operation_ref,
                    target,
                    remote_revision=revision,
                ),
                on_target=lambda target, revision: self._store.register_target(
                    operation_ref,
                    target,
                    remote_revision=revision,
                ),
                on_step=lambda step, revision: self._store.mark_step(
                    operation_ref,
                    step,
                    remote_revision=revision,
                ),
            )
        except RemoteMutationFailure as exc:
            record = self._store.require_recovery(
                operation_ref,
                ambiguous=exc.ambiguous,
                diagnostic_code=exc.diagnostic_code,
                verification_incomplete=exc.verification_incomplete,
            )
            return _result_from_record(record, retrieval_complete=False)
        except CapabilityError as exc:
            self._store.require_recovery(
                operation_ref,
                ambiguous=False,
                diagnostic_code=f"capability_{exc.code.value}",
            )
            raise CapabilityError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                details={**exc.details, "operation_ref": operation_ref},
            ) from exc
        except OperationStoreError:
            raise

        if recovery_warnings:
            proof = ExecutionProof(
                target=proof.target,
                api_hash=proof.api_hash,
                export_hash=proof.export_hash,
                remote_revision=proof.remote_revision,
                observed_at=proof.observed_at,
                warnings=tuple(dict.fromkeys((*recovery_warnings, *proof.warnings))),
            )

        record = self._store.mark_delivered(
            operation_ref,
            spec_hash=spec.content_hash,
            spec_summary=spec.summary(),
            delivery_hash=_proof_hash(
                spec.content_hash,
                proof.api_hash,
                proof.export_hash,
            ),
            remote_revision=proof.remote_revision,
        )
        return _result_from_record(
            record,
            registration=self._store.load_registration_for_operation(operation_ref),
            proof=proof,
        )

    async def aclose(self) -> None:
        await self._gateway.aclose()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OperationStoreError("Managed sheet service clock must be timezone-aware")
        return value.astimezone(UTC)


def _is_preflight_capability_recovery(record: OperationRecord) -> bool:
    """Recognize legacy records poisoned by a pre-mutation capability failure."""

    return bool(
        record.ambiguous
        and record.diagnostic_code
        and record.diagnostic_code.startswith("capability_")
    )


def _confirmation_request(
    record: OperationRecord,
    spec: SheetDeliverySpec,
) -> WriteConfirmationRequest:
    summary = spec.summary()
    return WriteConfirmationRequest(
        operation_ref=record.operation_ref,
        workbook_title=record.target.workbook_title,
        workbook_url=(
            record.target.created_wiki_url or record.target.source_locator
        ),
        worksheet_title=(
            record.target.sheet_title
            or record.target.requested_sheet_title
        ),
        placement_mode=record.placement_mode,
        rows=spec.row_count,
        columns=spec.column_count,
        cells=spec.row_count * spec.column_count,
        nonempty_cells=int(summary["nonempty_cells"]),
        formula_cells=int(summary["formula_cells"]),
        merge_ranges=len(spec.merges),
        disclosures=_disclosures(record.placement_mode, ()),
    )


def _disclosures(
    placement_mode: PlacementMode,
    remote_warnings: tuple[str, ...],
) -> tuple[str, ...]:
    if placement_mode is PlacementMode.ADOPT_BLANK_SHEET:
        defaults = (
            "接管空白工作表不是远端原子事务，失败后只做前向恢复，不自动清空或回滚",
            "仅规范化声明的交付矩形，矩形外空白单元格的已有格式保持不变",
        )
    elif placement_mode is PlacementMode.CREATE_NEW_SHEET:
        defaults = (
            "将在现有工作簿中创建唯一命名的新工作表，失败后不会自动删除",
            "交付矩形外保持新工作表默认状态",
        )
    else:
        defaults = (
            "将在指定 Wiki 父节点下创建唯一命名的新电子表格文件，失败后不会自动删除",
            "只接管新文件自动创建的唯一默认工作表，并保持其原始标题",
            "结果不明时只按同一操作对账或安全前向恢复，不新建替代文件",
        )
    return tuple(dict.fromkeys((*defaults, *remote_warnings)))


def _preview_hash(
    *,
    task_ref: str,
    profile_ref: str,
    placement_mode: PlacementMode,
    spec_hash: str,
    target: ProtectedTarget,
) -> str:
    payload = json.dumps(
        {
            "task_ref": task_ref,
            "profile_ref": profile_ref,
            "placement_mode": placement_mode.value,
            "spec_hash": spec_hash,
            "target": target.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _target_view(target: ProtectedTarget) -> ManagedSheetTargetView:
    title = target.sheet_title or target.requested_sheet_title
    if title is None and target.requested_workbook_title is None:
        raise OperationStoreError("Managed sheet target has no worksheet title")
    return ManagedSheetTargetView(
        workbook_title=target.workbook_title,
        workbook_url=target.workbook_url,
        wiki_url=target.created_wiki_url,
        wiki_space_id=target.wiki_space_id,
        wiki_node_token=target.created_wiki_node_token,
        parent_wiki_node_token=target.parent_wiki_node_token,
        worksheet_id=target.sheet_id,
        worksheet_title=title,
        worksheet_index=target.sheet_index,
    )


def _result_from_record(
    record: OperationRecord,
    *,
    registration: ManagedSheetRegistration | None = None,
    proof: ExecutionProof | None = None,
    retrieval_complete: bool | None = None,
    additional_warnings: tuple[str, ...] = (),
) -> ManagedSheetApplyResult:
    status = {
        OperationState.DELIVERED: OperationStatus.DELIVERED,
        OperationState.DECLINED: OperationStatus.DECLINED,
        OperationState.CANCELLED: OperationStatus.CANCELLED,
        OperationState.RECOVERY_REQUIRED: OperationStatus.RECOVERY_REQUIRED,
        OperationState.VERIFICATION_INCOMPLETE: OperationStatus.VERIFICATION_INCOMPLETE,
    }.get(record.state)
    if status is None:
        raise OperationStoreError(
            f"Operation state {record.state.value} cannot be returned as a final result"
        )
    complete = retrieval_complete if retrieval_complete is not None else proof is not None
    observed_at = proof.observed_at if proof is not None else record.updated_at
    content_hash = (
        _proof_hash(record.spec_hash, proof.api_hash, proof.export_hash)
        if proof is not None
        else record.delivery_hash or record.preview_hash
    )
    warnings = (
        *(proof.warnings if proof is not None else ()),
        *additional_warnings,
    )
    if record.diagnostic_code:
        warnings = (*warnings, record.diagnostic_code)
    return ManagedSheetApplyResult(
        status=status,
        operation_ref=record.operation_ref,
        registration_ref=(
            registration.registration_ref if registration is not None else None
        ),
        managed_version=(
            registration.current_version if registration is not None else None
        ),
        task_ref=record.task_ref,
        profile_ref=record.profile_ref,
        placement_mode=record.placement_mode,
        target=_target_view(proof.target if proof is not None else record.target),
        spec_hash=record.spec_hash,
        last_completed_step=record.last_completed_step,
        remote_revision=(proof.remote_revision if proof is not None else record.remote_revision),
        diagnostic_code=record.diagnostic_code,
        evidence=OperationEvidence(
            observed_at=observed_at.astimezone(UTC).isoformat(timespec="seconds"),
            content_hash=content_hash,
            provider_revision=(
                proof.remote_revision if proof is not None else record.remote_revision
            ),
            retrieval_complete=complete,
            warnings=warnings,
        ),
    )


def _proof_hash(spec_hash: str, api_hash: str, export_hash: str) -> str:
    canonical = "\x1f".join((spec_hash, api_hash, export_hash)).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
