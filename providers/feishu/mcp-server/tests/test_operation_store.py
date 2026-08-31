from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capability_contracts import CapabilityError, CapabilityErrorCode
from feishu_provider.operation_store import (
    OperationState,
    OperationStore,
    ProtectedTarget,
    RevisionState,
    RevisionStep,
    WriteStep,
)
from feishu_provider.sheet_delivery import PlacementMode


class FakeProtector:
    def protect(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(plaintext[::-1].encode()).decode()

    def unprotect(self, protected_value: str) -> str:
        return base64.urlsafe_b64decode(protected_value.encode()).decode()[::-1]


def _target(**changes: object) -> ProtectedTarget:
    payload: dict[str, object] = {
        "source_locator": "https://example.feishu.cn/sheets/shtcnSecret",
        "spreadsheet_token": "shtcnSecret",
        "workbook_title": "敏感工作簿标题",
        "workbook_url": "https://example.feishu.cn/sheets/shtcnSecret",
        "worksheet_selector": None,
        "requested_sheet_title": "测试用例",
        "sheet_id": None,
        "sheet_title": None,
        "sheet_index": None,
        "initial_revision": "7",
        "initial_sheet_count": 2,
        "initial_state_hash": "sha256:initial",
    }
    payload.update(changes)
    return ProtectedTarget.model_validate(payload)


def _store(tmp_path: Path, now: list[datetime]) -> OperationStore:
    return OperationStore(
        tmp_path / "operations.sqlite3",
        FakeProtector(),
        clock=lambda: now[0],
    )


def test_operation_store_keeps_target_encrypted_and_content_outside_database(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 25, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)

    record = store.create_preview(
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.CREATE_NEW_SHEET,
        spec_hash="sha256:spec",
        preview_hash="sha256:preview",
        target=_target(),
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref="wop_0123456789abcdef0123456789abcdef",
    )

    assert record.state is OperationState.PREVIEWED
    assert record.target.spreadsheet_token == "shtcnSecret"
    database_bytes = (tmp_path / "operations.sqlite3").read_bytes()
    assert b"shtcnSecret" not in database_bytes
    assert "敏感工作簿标题".encode() not in database_bytes
    assert b"worksheet body must never be stored" not in database_bytes


def test_operation_store_checkpoints_created_workbook_before_sheet_registration(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 28, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)
    operation_ref = "wop_abcdefabcdefabcdefabcdefabcdefab"
    spec_hash = "sha256:" + "2" * 64
    pending = ProtectedTarget(
        source_locator="https://example.feishu.cn/wiki/MlK5wn103ikcd1kA1JScXTFCnOb",
        workbook_title="正式用例-20260828",
        requested_workbook_title="正式用例-20260828",
        wiki_space_id="7527507018619224092",
        parent_wiki_node_token="MlK5wn103ikcd1kA1JScXTFCnOb",
        parent_wiki_title="测试用例",
        initial_sheet_count=0,
        initial_child_count=0,
        initial_state_hash="sha256:initial",
    )
    store.create_preview(
        task_ref="task-new-workbook",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.CREATE_NEW_WORKBOOK,
        spec_hash=spec_hash,
        preview_hash="sha256:preview",
        target=pending,
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=operation_ref,
    )
    store.authorize_execution(
        operation_ref=operation_ref,
        task_ref="task-new-workbook",
        spec_hash=spec_hash,
    )
    created = pending.model_copy(
        update={
            "spreadsheet_token": "shtcnCreatedWorkbook123",
            "workbook_url": "https://example.feishu.cn/wiki/wikcnCreatedWorkbook123",
            "created_wiki_node_token": "wikcnCreatedWorkbook123",
            "created_wiki_url": "https://example.feishu.cn/wiki/wikcnCreatedWorkbook123",
        }
    )
    checkpoint = store.register_workbook(operation_ref, created)

    assert checkpoint.last_completed_step is WriteStep.WORKBOOK_CREATED
    assert checkpoint.target.spreadsheet_token == "shtcnCreatedWorkbook123"
    assert checkpoint.target.sheet_id is None

    registered = store.register_target(
        operation_ref,
        created.model_copy(
            update={
                "sheet_id": "created-default-sheet",
                "sheet_title": "Sheet1",
                "sheet_index": 0,
            }
        ),
    )
    assert registered.last_completed_step is WriteStep.TARGET_REGISTERED
    assert registered.target.sheet_id == "created-default-sheet"


def test_operation_store_consumes_grants_and_recovers_forward(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 25, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)
    operation_ref = "wop_0123456789abcdef0123456789abcdef"
    spec_hash = "sha256:" + "1" * 64
    store.create_preview(
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.CREATE_NEW_SHEET,
        spec_hash=spec_hash,
        preview_hash="sha256:preview",
        target=_target(),
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=operation_ref,
    )

    executing = store.authorize_execution(
        operation_ref=operation_ref,
        task_ref="task-one",
        spec_hash=spec_hash,
    )
    assert executing.state is OperationState.EXECUTING
    target = _target(sheet_id="sheet-one", sheet_title="测试用例", sheet_index=2)
    store.update_target(operation_ref, target, remote_revision="8")
    store.mark_step(operation_ref, WriteStep.VALUES_WRITTEN, remote_revision="9")
    recovery = store.require_recovery(
        operation_ref,
        ambiguous=False,
        diagnostic_code="styles_rate_limited",
    )
    assert recovery.state is OperationState.RECOVERY_REQUIRED
    assert recovery.last_completed_step is WriteStep.VALUES_WRITTEN

    with pytest.raises(CapabilityError) as second_grant:
        store.authorize_execution(
            operation_ref=operation_ref,
            task_ref="task-one",
            spec_hash=spec_hash,
        )
    assert second_grant.value.code is CapabilityErrorCode.RECONCILIATION_REQUIRED

    resumed = store.resume_execution(
        operation_ref=operation_ref,
        task_ref="task-one",
        spec_hash=spec_hash,
    )
    assert resumed.state is OperationState.EXECUTING
    store.mark_step(operation_ref, WriteStep.EXPORT_VERIFIED, remote_revision="15")
    delivered = store.mark_delivered(
        operation_ref,
        spec_hash=spec_hash,
        spec_summary={"rows": 2, "columns": 2},
        delivery_hash="sha256:" + "a" * 64,
        remote_revision="15",
    )

    assert delivered.state is OperationState.DELIVERED
    assert delivered.delivery_hash == "sha256:" + "a" * 64
    assert delivered.target.sheet_id == "sheet-one"
    assert store.managed_registration_count() == 1
    registration = store.load_registration_for_operation(operation_ref)
    assert registration.current_version == 1
    assert registration.spec_hash == spec_hash
    assert registration.spec_summary["rows"] == 2
    assert store.list_versions(registration.registration_ref)[0].managed_version == 1
    with sqlite3.connect(tmp_path / "operations.sqlite3") as connection:
        grants = connection.execute(
            "SELECT issued_at, consumed_at FROM write_grants ORDER BY issued_at"
        ).fetchall()
    assert len(grants) == 1
    assert all(consumed_at is not None for _, consumed_at in grants)


def test_operation_store_rejects_expired_or_mismatched_preview(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 25, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)
    operation_ref = "wop_0123456789abcdef0123456789abcdef"
    store.create_preview(
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.ADOPT_BLANK_SHEET,
        spec_hash="sha256:spec",
        preview_hash="sha256:preview",
        target=_target(sheet_id="sheet-one", sheet_title="空白页", sheet_index=0),
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=operation_ref,
    )

    with pytest.raises(CapabilityError) as mismatch:
        store.load_matching(
            operation_ref=operation_ref,
            task_ref="task-one",
            spec_hash="sha256:different",
        )
    assert mismatch.value.code is CapabilityErrorCode.WRITE_CONFLICT

    now[0] += timedelta(minutes=11)
    with pytest.raises(CapabilityError) as expired:
        store.load_matching(
            operation_ref=operation_ref,
            task_ref="task-one",
            spec_hash="sha256:spec",
        )
    assert expired.value.code is CapabilityErrorCode.PREVIEW_EXPIRED


def test_operation_store_migrates_v1_with_missing_delivery_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operations.sqlite3"
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    protector = FakeProtector()
    target = _target(
        sheet_id="sheet-one",
        sheet_title="测试用例",
        sheet_index=0,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE operations (
                operation_ref TEXT PRIMARY KEY,
                task_ref TEXT NOT NULL,
                profile_ref TEXT NOT NULL,
                placement_mode TEXT NOT NULL,
                spec_hash TEXT NOT NULL,
                preview_hash TEXT NOT NULL,
                target_hash TEXT NOT NULL,
                protected_target TEXT NOT NULL,
                state TEXT NOT NULL,
                last_completed_step TEXT NOT NULL,
                ambiguous INTEGER NOT NULL,
                remote_revision TEXT,
                diagnostic_code TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            """
            INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wop_0123456789abcdef0123456789abcdef",
                "task-one",
                "profile_0123456789abcdef0123",
                PlacementMode.CREATE_NEW_SHEET.value,
                "sha256:spec",
                "sha256:preview",
                "sha256:target",
                protector.protect(target.model_dump_json()),
                OperationState.DELIVERED.value,
                WriteStep.EXPORT_VERIFIED.value,
                0,
                "15",
                None,
                now.isoformat(),
                (now + timedelta(minutes=10)).isoformat(),
                now.isoformat(),
            ),
        )

    store = OperationStore(database, protector, clock=lambda: now)
    record = store.load("wop_0123456789abcdef0123456789abcdef")

    assert record.delivery_hash is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)")
        }
    assert "delivery_hash" in columns


def test_operation_store_migrates_v2_registration_without_reencrypting_target(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operations.sqlite3"
    now = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    protector = FakeProtector()
    operation_ref = "wop_0123456789abcdef0123456789abcdef"
    registration_ref = "managed_0123456789abcdef0123456789abcdef"
    spec_hash = "sha256:" + "2" * 64
    delivery_hash = "sha256:" + "3" * 64
    protected_target = protector.protect(
        _target(
            sheet_id="sheet-one",
            sheet_title="测试用例",
            sheet_index=0,
        ).model_dump_json()
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE operations (
                operation_ref TEXT PRIMARY KEY, task_ref TEXT NOT NULL,
                profile_ref TEXT NOT NULL, placement_mode TEXT NOT NULL,
                spec_hash TEXT NOT NULL, preview_hash TEXT NOT NULL,
                delivery_hash TEXT, target_hash TEXT NOT NULL,
                protected_target TEXT NOT NULL, state TEXT NOT NULL,
                last_completed_step TEXT NOT NULL, ambiguous INTEGER NOT NULL,
                remote_revision TEXT, diagnostic_code TEXT,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE write_grants (
                grant_hash TEXT PRIMARY KEY, operation_ref TEXT NOT NULL,
                issued_at TEXT NOT NULL, consumed_at TEXT
            );
            CREATE TABLE managed_sheets (
                registration_ref TEXT PRIMARY KEY, operation_ref TEXT NOT NULL UNIQUE,
                profile_ref TEXT NOT NULL, target_hash TEXT NOT NULL,
                sheet_hash TEXT NOT NULL, protected_target TEXT NOT NULL,
                spec_hash TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(profile_ref, target_hash, sheet_hash)
            );
            PRAGMA user_version = 2;
            """
        )
        timestamp = now.isoformat()
        connection.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_ref,
                "task-one",
                "profile_0123456789abcdef0123",
                PlacementMode.CREATE_NEW_SHEET.value,
                spec_hash,
                "sha256:preview",
                delivery_hash,
                "sha256:target",
                protected_target,
                OperationState.DELIVERED.value,
                WriteStep.EXPORT_VERIFIED.value,
                0,
                "15",
                None,
                timestamp,
                (now + timedelta(minutes=10)).isoformat(),
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO managed_sheets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                registration_ref,
                operation_ref,
                "profile_0123456789abcdef0123",
                "sha256:target",
                "sha256:sheet",
                protected_target,
                spec_hash,
                OperationState.DELIVERED.value,
                timestamp,
                timestamp,
            ),
        )

    store = OperationStore(database, protector, clock=lambda: now)
    registration = store.load_registration(registration_ref)

    assert registration.current_version == 1
    assert registration.delivery_hash == delivery_hash
    assert registration.target.sheet_id == "sheet-one"
    assert store.list_versions(registration_ref)[0].operation_ref == operation_ref
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        stored = connection.execute(
            "SELECT protected_target FROM managed_sheets WHERE registration_ref = ?",
            (registration_ref,),
        ).fetchone()[0]
    assert stored == protected_target


def test_revision_state_machine_commits_one_append_only_version(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 25, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)
    operation_ref = "wop_0123456789abcdef0123456789abcdef"
    base_hash = "sha256:" + "4" * 64
    next_hash = "sha256:" + "5" * 64
    target = _target(sheet_id="sheet-one", sheet_title="测试用例", sheet_index=0)
    store.create_preview(
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.CREATE_NEW_SHEET,
        spec_hash=base_hash,
        preview_hash="sha256:preview",
        target=target,
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=operation_ref,
    )
    store.authorize_execution(
        operation_ref=operation_ref,
        task_ref="task-one",
        spec_hash=base_hash,
    )
    store.mark_step(operation_ref, WriteStep.EXPORT_VERIFIED)
    store.mark_delivered(
        operation_ref,
        spec_hash=base_hash,
        spec_summary={"rows": 2, "columns": 2},
        delivery_hash="sha256:" + "6" * 64,
        remote_revision="10",
    )
    registration = store.load_registration_for_operation(operation_ref)
    revision_ref = "rev_0123456789abcdef0123456789abcdef"
    revision = store.create_revision(
        registration_ref=registration.registration_ref,
        task_ref="task-revision",
        base_spec_hash=base_hash,
        next_spec_hash=next_hash,
        next_spec_summary={"rows": 1, "columns": 1},
        preview_hash="sha256:" + "7" * 64,
        diff_summary={"retired_ranges": ["A2:B2", "B1:B1"]},
        base_api_hash="sha256:" + "8" * 64,
        base_export_hash="sha256:" + "9" * 64,
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=revision_ref,
    )
    assert revision.candidate_version == 2
    with pytest.raises(CapabilityError) as conflict:
        store.create_revision(
            registration_ref=registration.registration_ref,
            task_ref="task-competing",
            base_spec_hash=base_hash,
            next_spec_hash="sha256:" + "b" * 64,
            next_spec_summary={"rows": 3, "columns": 3},
            preview_hash="sha256:" + "c" * 64,
            diff_summary={"retired_ranges": []},
            base_api_hash="sha256:" + "d" * 64,
            base_export_hash="sha256:" + "e" * 64,
            expires_at=now[0] + timedelta(minutes=10),
            operation_ref="rev_fedcba9876543210fedcba9876543210",
        )
    assert conflict.value.code is CapabilityErrorCode.WRITE_CONFLICT
    executing = store.authorize_revision(
        operation_ref=revision_ref,
        task_ref="task-revision",
        base_spec_hash=base_hash,
        next_spec_hash=next_hash,
    )
    assert executing.last_completed_step is RevisionStep.REVISION_RESERVED
    for step in tuple(RevisionStep)[2:-1]:
        store.mark_revision_step(revision_ref, step, remote_revision="11")
    committed, current = store.commit_revision(
        revision_ref,
        delivery_hash="sha256:" + "a" * 64,
        remote_revision="11",
        target=target,
    )

    assert committed.state is RevisionState.DELIVERED
    assert committed.last_completed_step is RevisionStep.VERSION_COMMITTED
    assert current.current_version == 2
    assert current.spec_hash == next_hash
    assert [item.managed_version for item in store.list_versions(registration.registration_ref)] == [1, 2]
    assert store.load_active_revision(registration.registration_ref) is None


def test_declining_initial_confirmation_closes_that_preview(tmp_path: Path) -> None:
    now = [datetime(2026, 8, 25, 0, 0, tzinfo=UTC)]
    store = _store(tmp_path, now)
    operation_ref = "wop_0123456789abcdef0123456789abcdef"
    store.create_preview(
        task_ref="task-one",
        profile_ref="profile_0123456789abcdef0123",
        placement_mode=PlacementMode.CREATE_NEW_SHEET,
        spec_hash="sha256:spec",
        preview_hash="sha256:preview",
        target=_target(),
        expires_at=now[0] + timedelta(minutes=10),
        operation_ref=operation_ref,
    )

    declined = store.record_unaccepted_confirmation(
        operation_ref,
        action=OperationState.DECLINED,
    )

    assert declined.state is OperationState.DECLINED
    with pytest.raises(CapabilityError) as error:
        store.load_matching(
            operation_ref=operation_ref,
            task_ref="task-one",
            spec_hash="sha256:spec",
        )
    assert error.value.code is CapabilityErrorCode.CONFIRMATION_REQUIRED
