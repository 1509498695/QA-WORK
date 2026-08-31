from __future__ import annotations

import base64
import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_provider.locator import classify_locator
from feishu_provider.managed_sheets import (
    ConfirmationAction,
    ExecutionProof,
    ManagedSheetWriteService,
    RecoveryProgress,
    RemoteMutationFailure,
    RemotePreview,
)
from feishu_provider.operation_store import (
    OperationStore,
    ProtectedTarget,
    WriteStep,
)
from feishu_provider.sheet_delivery import (
    SHEET_DELIVERY_SCHEMA_VERSION,
    PlacementMode,
    SheetDeliverySpec,
)


PROFILE_REF = "profile_0123456789abcdef0123"
LOCATOR = "https://example.feishu.cn/sheets/shtcn1234567890"
WIKI_LOCATOR = "https://example.feishu.cn/wiki/MlK5wn103ikcd1kA1JScXTFCnOb"


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _spec() -> SheetDeliverySpec:
    return SheetDeliverySpec.model_validate(
        {
            "schema_version": SHEET_DELIVERY_SCHEMA_VERSION,
            "row_count": 2,
            "column_count": 2,
            "values": [["标题", "值"], [1, 2]],
            "base_style": {
                "font_size_pt": 10,
                "text_color": "#000000",
                "border_type": "full",
                "border_color": "#D0D5DD",
            },
        }
    )


def _preview_target() -> ProtectedTarget:
    return ProtectedTarget(
        source_locator=LOCATOR,
        spreadsheet_token="shtcn1234567890",
        workbook_title="测试工作簿",
        workbook_url=LOCATOR,
        requested_sheet_title="自动化用例",
        initial_revision="7",
        initial_sheet_count=1,
        initial_state_hash="sha256:initial",
    )


class FakeGateway:
    def __init__(
        self,
        *,
        fail_ambiguously: bool = False,
        fail_reconciliation_verification: bool = False,
    ) -> None:
        self.fail_ambiguously = fail_ambiguously
        self.fail_reconciliation_verification = fail_reconciliation_verification
        self.preview_calls = 0
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.closed = False
        self._failed = False
        self.last_target: ProtectedTarget | None = None

    async def preview(self, **_: object) -> RemotePreview:
        self.preview_calls += 1
        return RemotePreview(
            source=classify_locator(LOCATOR),
            profile_ref=PROFILE_REF,
            target=_preview_target(),
            observed_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        )

    async def execute(  # type: ignore[no-untyped-def]
        self, *, record, spec, on_workbook, on_target, on_step
    ):
        del spec, on_workbook
        self.execute_calls += 1
        target = record.target
        if target.sheet_id is None:
            target = target.model_copy(
                update={
                    "sheet_id": "sheet-created",
                    "sheet_title": "自动化用例",
                    "sheet_index": 1,
                }
            )
            on_target(target, "8")
        self.last_target = target
        for step in (
            WriteStep.GRID_EXTENDED,
            WriteStep.VALUES_WRITTEN,
        ):
            on_step(step, "9")
        if self.fail_ambiguously and not self._failed:
            self._failed = True
            raise RemoteMutationFailure("styles_transport_unknown", ambiguous=True)
        for step in (
            WriteStep.STYLES_CLEARED,
            WriteStep.BASE_STYLE_WRITTEN,
            WriteStep.STYLE_RANGES_WRITTEN,
            WriteStep.DIMENSIONS_WRITTEN,
            WriteStep.FREEZE_WRITTEN,
            WriteStep.MERGES_WRITTEN,
            WriteStep.API_VERIFIED,
            WriteStep.EXPORT_VERIFIED,
        ):
            on_step(step, "10")
        return self._proof(target)

    async def reconcile_final(self, **_: object) -> ExecutionProof | None:
        self.reconcile_calls += 1
        assert self.last_target is not None
        if self.fail_reconciliation_verification:
            raise RemoteMutationFailure(
                "xlsx_verify_column_width:0:actual_138:expected_150",
                ambiguous=False,
                verification_incomplete=True,
            )
        return self._proof(self.last_target)

    async def reconcile_progress(self, **_: object) -> RecoveryProgress | None:
        return None

    async def aclose(self) -> None:
        self.closed = True

    @staticmethod
    def _proof(target: ProtectedTarget) -> ExecutionProof:
        return ExecutionProof(
            target=target,
            api_hash="sha256:api",
            export_hash="sha256:export",
            remote_revision="10",
            observed_at=datetime(2026, 8, 25, 0, 1, tzinfo=UTC),
        )


class ProgressRecoveryGateway(FakeGateway):
    def __init__(
        self,
        recovery_step: WriteStep = WriteStep.VALUES_WRITTEN,
    ) -> None:
        super().__init__()
        self.progress_calls = 0
        self.recovery_step = recovery_step

    async def execute(  # type: ignore[no-untyped-def]
        self, *, record, spec, on_workbook, on_target, on_step
    ):
        del spec, on_workbook
        self.execute_calls += 1
        target = record.target
        if target.sheet_id is None:
            target = target.model_copy(
                update={
                    "sheet_id": "sheet-created",
                    "sheet_title": "自动化用例",
                    "sheet_index": 1,
                }
            )
            on_target(target, "8")
        self.last_target = target
        if not self._failed:
            self._failed = True
            on_step(WriteStep.GRID_EXTENDED, None)
            raise RemoteMutationFailure(
                "values_write_contract_unknown",
                ambiguous=True,
            )
        assert record.last_completed_step is self.recovery_step
        if self.recovery_step is WriteStep.GRID_EXTENDED:
            on_step(WriteStep.VALUES_WRITTEN, "9")
        for step in (
            WriteStep.STYLES_CLEARED,
            WriteStep.BASE_STYLE_WRITTEN,
            WriteStep.STYLE_RANGES_WRITTEN,
            WriteStep.DIMENSIONS_WRITTEN,
            WriteStep.FREEZE_WRITTEN,
            WriteStep.MERGES_WRITTEN,
            WriteStep.API_VERIFIED,
            WriteStep.EXPORT_VERIFIED,
        ):
            on_step(step, "10")
        return self._proof(target)

    async def reconcile_final(self, **_: object) -> ExecutionProof | None:
        self.reconcile_calls += 1
        return None

    async def reconcile_progress(self, **_: object) -> RecoveryProgress | None:
        self.progress_calls += 1
        warning = (
            "ambiguous_values_write_reconciled_by_api_readback"
            if self.recovery_step is WriteStep.VALUES_WRITTEN
            else "ambiguous_values_write_proved_not_applied_by_api_readback"
        )
        return RecoveryProgress(
            completed_step=self.recovery_step,
            remote_revision="9",
            observed_at=datetime(2026, 8, 25, 0, 1, tzinfo=UTC),
            warnings=(warning,),
        )


class CapabilityRecoveryGateway(FakeGateway):
    async def execute(self, **kwargs: object) -> ExecutionProof:
        if not self._failed:
            self._failed = True
            self.execute_calls += 1
            raise CapabilityError(
                CapabilityErrorCode.AUTH_REQUIRED,
                "Reauthorize the selected profile.",
            )
        return await super().execute(**kwargs)


class NewWorkbookGateway(FakeGateway):
    async def preview(self, **_: object) -> RemotePreview:
        self.preview_calls += 1
        return RemotePreview(
            source=classify_locator(WIKI_LOCATOR),
            profile_ref=PROFILE_REF,
            target=ProtectedTarget(
                source_locator=WIKI_LOCATOR,
                workbook_title="正式用例-20260828",
                requested_workbook_title="正式用例-20260828",
                wiki_space_id="7527507018619224092",
                parent_wiki_node_token="MlK5wn103ikcd1kA1JScXTFCnOb",
                parent_wiki_title="测试用例",
                initial_sheet_count=0,
                initial_child_count=0,
                initial_state_hash="sha256:initial",
            ),
            observed_at=datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
        )

    async def execute(  # type: ignore[no-untyped-def]
        self, *, record, spec, on_workbook, on_target, on_step
    ):
        del spec
        self.execute_calls += 1
        target = record.target
        if record.last_completed_step is WriteStep.NONE:
            target = target.model_copy(
                update={
                    "spreadsheet_token": "shtcnCreatedWorkbook123",
                    "workbook_url": "https://example.feishu.cn/wiki/wikcnCreatedWorkbook123",
                    "created_wiki_node_token": "wikcnCreatedWorkbook123",
                    "created_wiki_url": "https://example.feishu.cn/wiki/wikcnCreatedWorkbook123",
                }
            )
            on_workbook(target, None)
        target = target.model_copy(
            update={
                "workbook_url": "https://example.feishu.cn/sheets/shtcnCreatedWorkbook123",
                "sheet_id": "created-default-sheet",
                "sheet_title": "Sheet1",
                "sheet_index": 0,
            }
        )
        on_target(target, "3")
        self.last_target = target
        for step in (
            WriteStep.GRID_EXTENDED,
            WriteStep.VALUES_WRITTEN,
            WriteStep.STYLES_CLEARED,
            WriteStep.BASE_STYLE_WRITTEN,
            WriteStep.STYLE_RANGES_WRITTEN,
            WriteStep.DIMENSIONS_WRITTEN,
            WriteStep.FREEZE_WRITTEN,
            WriteStep.MERGES_WRITTEN,
            WriteStep.API_VERIFIED,
            WriteStep.EXPORT_VERIFIED,
        ):
            on_step(step, "4")
        return self._proof(target)


def _service(tmp_path: Path, gateway: FakeGateway) -> ManagedSheetWriteService:
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    return ManagedSheetWriteService(
        gateway=gateway,
        store=OperationStore(
            tmp_path / "operations.sqlite3",
            FakeProtector(),
            clock=lambda: now,
        ),
        clock=lambda: now,
    )


async def _preview(service: ManagedSheetWriteService):  # type: ignore[no-untyped-def]
    return await service.preview(
        locator=LOCATOR,
        task_ref="task-write",
        profile_ref=PROFILE_REF,
        placement_mode=PlacementMode.CREATE_NEW_SHEET,
        requested_sheet_title="自动化用例",
        spec=_spec(),
    )


async def _case_preview_and_accepted_apply_deliver_one_managed_sheet(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    service = _service(tmp_path, gateway)
    preview = await _preview(service)
    confirmations = []

    async def accept(request):  # type: ignore[no-untyped-def]
        confirmations.append(request)
        return ConfirmationAction.ACCEPT

    result = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )
    repeated = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert preview.status.value == "preview_ready"
    assert preview.target.worksheet_title == "自动化用例"
    assert result.status.value == "delivered"
    assert result.registration_ref is not None
    assert result.registration_ref.startswith("managed_")
    assert result.managed_version == 1
    assert result.target.worksheet_id == "sheet-created"
    assert result.last_completed_step is WriteStep.EXPORT_VERIFIED
    assert result.evidence.retrieval_complete is True
    assert repeated.evidence.content_hash == result.evidence.content_hash
    assert repeated.evidence.retrieval_complete is True
    assert len(confirmations) == 1
    assert confirmations[0].cells == 4
    assert gateway.execute_calls == 1
    assert gateway.reconcile_calls == 0


async def _case_decline_has_zero_remote_writes(tmp_path: Path) -> None:
    gateway = FakeGateway()
    service = _service(tmp_path, gateway)
    preview = await _preview(service)

    async def decline(_):  # type: ignore[no-untyped-def]
        return ConfirmationAction.DECLINE

    result = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=decline,
    )

    assert result.status.value == "declined"
    assert result.last_completed_step is WriteStep.NONE
    assert gateway.execute_calls == 0


async def _case_ambiguous_mutation_recovers_by_readback_without_new_confirmation(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(fail_ambiguously=True)
    service = _service(tmp_path, gateway)
    preview = await _preview(service)
    confirmations = 0

    async def accept(_):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        return ConfirmationAction.ACCEPT

    first = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )
    second = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert first.status.value == "recovery_required"
    assert first.diagnostic_code == "styles_transport_unknown"
    assert second.status.value == "delivered"
    assert confirmations == 1
    assert gateway.execute_calls == 1
    assert gateway.reconcile_calls == 1


async def _case_reconciliation_preserves_safe_xlsx_diagnostic(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway(
        fail_ambiguously=True,
        fail_reconciliation_verification=True,
    )
    service = _service(tmp_path, gateway)
    preview = await _preview(service)

    async def accept(_):  # type: ignore[no-untyped-def]
        return ConfirmationAction.ACCEPT

    first = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )
    second = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert first.status.value == "recovery_required"
    assert second.status.value == "verification_incomplete"
    assert second.diagnostic_code == (
        "xlsx_verify_column_width:0:actual_138:expected_150"
    )
    assert gateway.execute_calls == 1
    assert gateway.reconcile_calls == 1


async def _case_values_contract_unknown_resumes_after_exact_progress_readback(
    tmp_path: Path,
    recovery_step: WriteStep = WriteStep.VALUES_WRITTEN,
) -> None:
    gateway = ProgressRecoveryGateway(recovery_step)
    service = _service(tmp_path, gateway)
    preview = await _preview(service)
    confirmations = 0

    async def accept(_):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        return ConfirmationAction.ACCEPT

    first = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )
    second = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert first.status.value == "recovery_required"
    assert first.last_completed_step is WriteStep.GRID_EXTENDED
    assert first.diagnostic_code == "values_write_contract_unknown"
    assert second.status.value == "delivered"
    assert second.last_completed_step is WriteStep.EXPORT_VERIFIED
    assert second.evidence.retrieval_complete is True
    expected_warning = (
        "ambiguous_values_write_reconciled_by_api_readback"
        if recovery_step is WriteStep.VALUES_WRITTEN
        else "ambiguous_values_write_proved_not_applied_by_api_readback"
    )
    assert second.evidence.warnings == (expected_warning,)
    assert confirmations == 1
    assert gateway.execute_calls == 2
    assert gateway.reconcile_calls == 1
    assert gateway.progress_calls == 1


async def _case_legacy_delivered_record_backfills_delivery_evidence(
    tmp_path: Path,
) -> None:
    gateway = FakeGateway()
    service = _service(tmp_path, gateway)
    preview = await _preview(service)

    async def accept(_):  # type: ignore[no-untyped-def]
        return ConfirmationAction.ACCEPT

    first = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )
    with sqlite3.connect(tmp_path / "operations.sqlite3") as connection:
        connection.execute(
            "UPDATE operations SET delivery_hash = NULL WHERE operation_ref = ?",
            (preview.operation_ref,),
        )
    second = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert first.status.value == "delivered"
    assert second.status.value == "delivered"
    assert second.evidence.retrieval_complete is True
    assert second.evidence.content_hash == first.evidence.content_hash
    assert gateway.execute_calls == 1
    assert gateway.reconcile_calls == 1


async def _case_capability_failure_is_non_ambiguous_and_resumes(
    tmp_path: Path,
    *,
    simulate_legacy_ambiguous_record: bool = False,
) -> None:
    gateway = CapabilityRecoveryGateway()
    service = _service(tmp_path, gateway)
    preview = await _preview(service)
    confirmations = 0

    async def accept(_):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        return ConfirmationAction.ACCEPT

    with pytest.raises(CapabilityError) as error:
        await service.apply(
            operation_ref=preview.operation_ref,
            task_ref="task-write",
            spec=_spec(),
            confirmer=accept,
        )

    assert error.value.code is CapabilityErrorCode.AUTH_REQUIRED
    with sqlite3.connect(tmp_path / "operations.sqlite3") as connection:
        stored = connection.execute(
            "SELECT ambiguous, diagnostic_code FROM operations "
            "WHERE operation_ref = ?",
            (preview.operation_ref,),
        ).fetchone()
        assert stored == (0, "capability_auth_required")
        if simulate_legacy_ambiguous_record:
            connection.execute(
                "UPDATE operations SET ambiguous = 1 WHERE operation_ref = ?",
                (preview.operation_ref,),
            )

    result = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-write",
        spec=_spec(),
        confirmer=accept,
    )

    assert result.status.value == "delivered"
    assert result.evidence.retrieval_complete is True
    assert confirmations == 1
    assert gateway.execute_calls == 2
    assert gateway.reconcile_calls == 0


async def _case_new_workbook_delivery_uses_one_confirmation_and_two_identity_checkpoints(
    tmp_path: Path,
) -> None:
    gateway = NewWorkbookGateway()
    service = _service(tmp_path, gateway)
    preview = await service.preview(
        locator=WIKI_LOCATOR,
        task_ref="task-new-workbook",
        profile_ref=PROFILE_REF,
        placement_mode=PlacementMode.CREATE_NEW_WORKBOOK,
        requested_sheet_title=None,
        requested_workbook_title="正式用例-20260828",
        spec=_spec(),
    )
    confirmations = []

    async def accept(request):  # type: ignore[no-untyped-def]
        confirmations.append(request)
        return ConfirmationAction.ACCEPT

    result = await service.apply(
        operation_ref=preview.operation_ref,
        task_ref="task-new-workbook",
        spec=_spec(),
        confirmer=accept,
    )

    assert preview.target.workbook_title == "正式用例-20260828"
    assert preview.target.worksheet_title is None
    assert result.status.value == "delivered"
    assert result.target.wiki_node_token == "wikcnCreatedWorkbook123"
    assert result.target.worksheet_id == "created-default-sheet"
    assert result.target.worksheet_title == "Sheet1"
    assert result.last_completed_step is WriteStep.EXPORT_VERIFIED
    assert result.evidence.retrieval_complete is True
    assert len(confirmations) == 1
    assert confirmations[0].worksheet_title is None
    assert "Wiki 父节点下新建电子表格文件" in confirmations[0].message()


def test_preview_and_accepted_apply_deliver_one_managed_sheet(
    tmp_path: Path,
) -> None:
    asyncio.run(_case_preview_and_accepted_apply_deliver_one_managed_sheet(tmp_path))


def test_new_workbook_delivery_uses_one_confirmation_and_two_identity_checkpoints(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _case_new_workbook_delivery_uses_one_confirmation_and_two_identity_checkpoints(
            tmp_path
        )
    )


def test_decline_has_zero_remote_writes(tmp_path: Path) -> None:
    asyncio.run(_case_decline_has_zero_remote_writes(tmp_path))


def test_ambiguous_mutation_recovers_by_readback_without_new_confirmation(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _case_ambiguous_mutation_recovers_by_readback_without_new_confirmation(
            tmp_path
        )
    )


def test_reconciliation_preserves_safe_xlsx_diagnostic(tmp_path: Path) -> None:
    asyncio.run(_case_reconciliation_preserves_safe_xlsx_diagnostic(tmp_path))


def test_values_contract_unknown_resumes_after_exact_progress_readback(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _case_values_contract_unknown_resumes_after_exact_progress_readback(tmp_path)
    )


def test_values_contract_unknown_resumes_after_blank_readback(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _case_values_contract_unknown_resumes_after_exact_progress_readback(
            tmp_path,
            WriteStep.GRID_EXTENDED,
        )
    )


def test_legacy_delivered_record_backfills_delivery_evidence(tmp_path: Path) -> None:
    asyncio.run(_case_legacy_delivered_record_backfills_delivery_evidence(tmp_path))


def test_capability_failure_is_non_ambiguous_and_resumes(tmp_path: Path) -> None:
    asyncio.run(_case_capability_failure_is_non_ambiguous_and_resumes(tmp_path))


def test_legacy_ambiguous_capability_failure_resumes_without_reconciliation(
    tmp_path: Path,
) -> None:
    asyncio.run(
        _case_capability_failure_is_non_ambiguous_and_resumes(
            tmp_path,
            simulate_legacy_ambiguous_record=True,
        )
    )
