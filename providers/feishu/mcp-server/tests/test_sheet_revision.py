from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest
from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from feishu_provider.locator import classify_locator
from feishu_provider.managed_sheets import ConfirmationAction, RemoteMutationFailure
from feishu_provider.operation_store import (
    OperationStore,
    ProtectedTarget,
    RevisionStep,
    WriteStep,
)
from feishu_provider.sheet_delivery import (
    SHEET_DELIVERY_SCHEMA_VERSION,
    PlacementMode,
    SheetDeliverySpec,
)
from feishu_provider.sheet_revision import (
    ManagedSheetRevisionService,
    ResolvedWorkbook,
    ResolvedWorksheet,
    RevisionProof,
    build_revision_diff,
    revision_retired_ranges,
)

PROFILE_REF = "profile_0123456789abcdef0123"
SHEET_TOKEN = "shtcn1234567890"
SHEET_ID = "sheet-one"
LOCATOR = f"https://example.feishu.cn/sheets/{SHEET_TOKEN}?sheet={SHEET_ID}"


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _spec(
    *,
    rows: int = 2,
    columns: int = 2,
    marker: str = "v1",
) -> SheetDeliverySpec:
    values = [
        [f"{marker}-{row}-{column}" for column in range(columns)]
        for row in range(rows)
    ]
    return SheetDeliverySpec.model_validate(
        {
            "schema_version": SHEET_DELIVERY_SCHEMA_VERSION,
            "row_count": rows,
            "column_count": columns,
            "values": values,
            "base_style": {
                "font_size_pt": 10,
                "text_color": "#000000",
                "horizontal_alignment": "left",
                "vertical_alignment": "top",
            },
            "default_row_height_px": 24,
            "default_column_width_px": 100,
        }
    )


def _target(
    *,
    title: str = "用例",
    sheet_id: str = SHEET_ID,
) -> ProtectedTarget:
    return ProtectedTarget(
        source_locator=LOCATOR,
        spreadsheet_token=SHEET_TOKEN,
        workbook_title="工作簿",
        workbook_url=f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
        worksheet_selector=sheet_id,
        sheet_id=sheet_id,
        sheet_title=title,
        sheet_index=0,
        initial_revision="7",
        initial_sheet_count=1,
        initial_state_hash="sha256:initial",
    )


def _seed_registration(
    store: OperationStore,
    base: SheetDeliverySpec,
    *,
    operation_ref: str = "wop_0123456789abcdef0123456789abcdef",
    target: ProtectedTarget | None = None,
) -> str:
    target = target or _target()
    store.create_preview(
        task_ref="task-initial",
        profile_ref=PROFILE_REF,
        placement_mode=PlacementMode.ADOPT_BLANK_SHEET,
        spec_hash=base.content_hash,
        preview_hash="sha256:preview",
        target=target,
        expires_at=datetime(2026, 8, 25, 0, 10, tzinfo=UTC),
        operation_ref=operation_ref,
    )
    store.authorize_execution(
        operation_ref=operation_ref,
        task_ref="task-initial",
        spec_hash=base.content_hash,
    )
    store.mark_step(operation_ref, WriteStep.EXPORT_VERIFIED, remote_revision="7")
    store.mark_delivered(
        operation_ref,
        spec_hash=base.content_hash,
        spec_summary=base.summary(),
        delivery_hash="sha256:" + "1" * 64,
        remote_revision="7",
    )
    return store.load_registration_for_operation(operation_ref).registration_ref


class FakeRevisionGateway:
    def __init__(
        self,
        *,
        fail_once: bool = False,
        baseline_failure: bool = False,
    ) -> None:
        self.fail_once = fail_once
        self.baseline_failure = baseline_failure
        self.resolve_calls = 0
        self.baseline_calls = 0
        self.execute_calls = 0
        self.reconcile_calls = 0
        self._failed = False
        self.closed = False

    async def resolve_workbook(self, *, locator, task_ref, profile_ref):  # type: ignore[no-untyped-def]
        del task_ref
        self.resolve_calls += 1
        return ResolvedWorkbook(
            source=classify_locator(locator),
            profile_ref=profile_ref or PROFILE_REF,
            spreadsheet_token=SHEET_TOKEN,
            workbook_title="重命名工作簿",
            workbook_url=f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
            worksheets=(
                ResolvedWorksheet(
                    sheet_id=SHEET_ID,
                    title="重命名用例",
                    index=1,
                    hidden=False,
                    resource_type="sheet",
                ),
            ),
            observed_at=datetime(2026, 8, 25, 0, 1, tzinfo=UTC),
        )

    async def verify_revision_baseline(self, *, registration, task_ref, spec):  # type: ignore[no-untyped-def]
        del task_ref, spec
        self.baseline_calls += 1
        if self.baseline_failure:
            raise RemoteMutationFailure(
                "xlsx_export_poll_timeout",
                ambiguous=False,
                verification_incomplete=True,
            )
        return RevisionProof(
            target=registration.target,
            api_hash="sha256:" + "2" * 64,
            export_hash="sha256:" + "3" * 64,
            remote_revision="8",
            observed_at=datetime(2026, 8, 25, 0, 2, tzinfo=UTC),
        )

    async def execute_revision(
        self, *, record, registration, base_spec, next_spec, diff, on_step
    ):  # type: ignore[no-untyped-def]
        del base_spec, next_spec, diff
        self.execute_calls += 1
        if self.fail_once and not self._failed:
            self._failed = True
            on_step(RevisionStep.GRID_EXTENDED, "9")
            raise RemoteMutationFailure("revision_values_transport_unknown", ambiguous=True)
        for step in tuple(RevisionStep)[2:-1]:
            if tuple(RevisionStep).index(step) > tuple(RevisionStep).index(
                record.last_completed_step
            ):
                on_step(step, "10")
        return self._proof(registration.target)

    async def reconcile_revision_final(self, *, registration, **_):  # type: ignore[no-untyped-def]
        self.reconcile_calls += 1
        if self.fail_once and self._failed:
            return self._proof(registration.target)
        return None

    async def aclose(self) -> None:
        self.closed = True

    @staticmethod
    def _proof(target: ProtectedTarget) -> RevisionProof:
        return RevisionProof(
            target=target,
            api_hash="sha256:" + "4" * 64,
            export_hash="sha256:" + "5" * 64,
            remote_revision="10",
            observed_at=datetime(2026, 8, 25, 0, 3, tzinfo=UTC),
        )


def _service(
    tmp_path: Path,
    gateway: FakeRevisionGateway,
    base: SheetDeliverySpec,
) -> tuple[ManagedSheetRevisionService, OperationStore, str]:
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    store = OperationStore(
        tmp_path / "operations.sqlite3",
        FakeProtector(),
        clock=lambda: now,
    )
    registration_ref = _seed_registration(store, base)
    return (
        ManagedSheetRevisionService(
            gateway=gateway,
            store=store,
            clock=lambda: now,
        ),
        store,
        registration_ref,
    )


def test_revision_diff_decomposes_bottom_and_right_retirement() -> None:
    base = _spec(rows=4, columns=4)
    next_ = _spec(rows=2, columns=2, marker="v2")
    retired = revision_retired_ranges(base, next_)
    diff = build_revision_diff(base, next_)

    assert [item.a1("sheet").partition("!")[2] for item in retired] == [
        "A3:D4",
        "C1:D2",
    ]
    assert diff.retired_ranges == ("A3:D4", "C1:D2")
    assert diff.retired_cells == 12
    assert diff.neutral_rows == (2, 3)
    assert diff.neutral_columns == (2, 3)


def test_registration_resolve_tolerates_display_metadata_change(tmp_path: Path) -> None:
    base = _spec()
    gateway = FakeRevisionGateway()
    service, _, registration_ref = _service(tmp_path, gateway, base)

    result = asyncio.run(
        service.resolve_registration(
            locator=LOCATOR,
            task_ref="task-resolve",
            profile_ref=PROFILE_REF,
        )
    )

    assert result.registration_ref == registration_ref
    assert result.managed_version == 1
    assert result.target.worksheet_id == SHEET_ID
    assert result.target.worksheet_title == "重命名用例"
    assert result.evidence.warnings == ("display_metadata_changed",)


def test_registration_resolve_rejects_unselected_multi_registration_workbook(
    tmp_path: Path,
) -> None:
    base = _spec()
    gateway = FakeRevisionGateway()
    service, store, _ = _service(tmp_path, gateway, base)
    _seed_registration(
        store,
        base,
        operation_ref="wop_fedcba9876543210fedcba9876543210",
        target=_target(title="第二张", sheet_id="sheet-two"),
    )

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            service.resolve_registration(
                locator=f"https://example.feishu.cn/sheets/{SHEET_TOKEN}",
                task_ref="task-ambiguous",
                profile_ref=PROFILE_REF,
            )
        )

    assert error.value.code is CapabilityErrorCode.AMBIGUOUS_WRITE
    assert error.value.details["managed_registration_count"] == 2


def test_no_change_double_verifies_without_confirmation_or_version_growth(
    tmp_path: Path,
) -> None:
    base = _spec()
    gateway = FakeRevisionGateway()
    service, store, registration_ref = _service(tmp_path, gateway, base)
    confirmations = 0

    async def confirm(_):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        return ConfirmationAction.ACCEPT

    result = asyncio.run(
        service.revise(
            registration_ref=registration_ref,
            task_ref="task-no-change",
            base_spec=base,
            next_spec=base,
            confirmer=confirm,
        )
    )

    assert result.status.value == "no_change"
    assert result.operation_ref is None
    assert result.managed_version == 1
    assert result.evidence.retrieval_complete is True
    assert confirmations == 0
    assert gateway.baseline_calls == 1
    assert gateway.execute_calls == 0
    assert len(store.list_versions(registration_ref)) == 1


def test_accepted_revision_delivers_once_and_reuses_identity(tmp_path: Path) -> None:
    base = _spec()
    next_ = _spec(marker="v2")
    gateway = FakeRevisionGateway()
    service, store, registration_ref = _service(tmp_path, gateway, base)
    confirmations = 0

    async def confirm(request):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        assert request.preview_sha256.startswith("sha256:")
        assert next_.content_hash in request.message()
        return ConfirmationAction.ACCEPT

    async def exercise():  # type: ignore[no-untyped-def]
        first = await service.revise(
            registration_ref=registration_ref,
            task_ref="task-revision",
            base_spec=base,
            next_spec=next_,
            confirmer=confirm,
        )
        second = await service.revise(
            registration_ref=registration_ref,
            task_ref="task-revision",
            base_spec=base,
            next_spec=next_,
            confirmer=confirm,
        )
        return first, second

    first, second = asyncio.run(exercise())

    assert first.status.value == "delivered"
    assert first.managed_version == 2
    assert first.last_completed_step is RevisionStep.VERSION_COMMITTED
    assert second.operation_ref == first.operation_ref
    assert second.evidence.content_hash == first.evidence.content_hash
    assert confirmations == 1
    assert gateway.baseline_calls == 1
    assert gateway.execute_calls == 1
    assert [item.managed_version for item in store.list_versions(registration_ref)] == [1, 2]


def test_ambiguous_revision_recovers_by_final_readback_without_reconfirmation(
    tmp_path: Path,
) -> None:
    base = _spec()
    next_ = _spec(marker="v2")
    gateway = FakeRevisionGateway(fail_once=True)
    service, _, registration_ref = _service(tmp_path, gateway, base)
    confirmations = 0

    async def confirm(_):  # type: ignore[no-untyped-def]
        nonlocal confirmations
        confirmations += 1
        return ConfirmationAction.ACCEPT

    async def exercise():  # type: ignore[no-untyped-def]
        first = await service.revise(
            registration_ref=registration_ref,
            task_ref="task-recovery",
            base_spec=base,
            next_spec=next_,
            confirmer=confirm,
        )
        second = await service.revise(
            registration_ref=registration_ref,
            task_ref="task-recovery",
            base_spec=base,
            next_spec=next_,
            confirmer=confirm,
        )
        return first, second

    first, second = asyncio.run(exercise())

    assert first.status.value == "recovery_required"
    assert second.status.value == "delivered"
    assert confirmations == 1
    assert gateway.execute_calls == 1
    assert gateway.reconcile_calls == 1


def test_missing_base_spec_is_stable_zero_write_failure(tmp_path: Path) -> None:
    base = _spec()
    gateway = FakeRevisionGateway()
    service, _, registration_ref = _service(tmp_path, gateway, base)

    async def confirm(_):  # type: ignore[no-untyped-def]
        raise AssertionError("missing base must not confirm")

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            service.revise(
                registration_ref=registration_ref,
                task_ref="task-missing-base",
                base_spec=None,
                next_spec=_spec(marker="v2"),
                confirmer=confirm,
            )
        )

    assert error.value.code is CapabilityErrorCode.BASE_SPEC_REQUIRED
    assert gateway.baseline_calls == 0
    assert gateway.execute_calls == 0


def test_incomplete_baseline_verification_stops_before_confirmation(
    tmp_path: Path,
) -> None:
    base = _spec()
    gateway = FakeRevisionGateway(baseline_failure=True)
    service, _, registration_ref = _service(tmp_path, gateway, base)

    async def confirm(_):  # type: ignore[no-untyped-def]
        raise AssertionError("incomplete baseline must not confirm")

    with pytest.raises(CapabilityError) as error:
        asyncio.run(
            service.revise(
                registration_ref=registration_ref,
                task_ref="task-baseline-failure",
                base_spec=base,
                next_spec=_spec(marker="v2"),
                confirmer=confirm,
            )
        )

    assert (
        error.value.code
        is CapabilityErrorCode.BASELINE_VERIFICATION_INCOMPLETE
    )
    assert error.value.details["diagnostic_code"] == "xlsx_export_poll_timeout"
    assert gateway.execute_calls == 0
