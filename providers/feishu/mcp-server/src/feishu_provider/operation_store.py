from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from capability_contracts.errors import CapabilityError, CapabilityErrorCode
from pydantic import BaseModel, ConfigDict, ValidationError

from feishu_provider.sheet_delivery import PlacementMode

OPERATION_STORE_SCHEMA_VERSION = 3
DEFAULT_OPERATION_STORE_PARTS = (
    "WorkspaceCapabilities",
    "providers",
    "feishu",
    "operations-v1.sqlite3",
)


class OperationState(StrEnum):
    PREVIEWED = "previewed"
    EXECUTING = "executing"
    RECOVERY_REQUIRED = "recovery_required"
    VERIFICATION_INCOMPLETE = "verification_incomplete"
    DELIVERED = "delivered"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class WriteStep(StrEnum):
    NONE = "none"
    WORKBOOK_CREATED = "workbook_created"
    TARGET_REGISTERED = "target_registered"
    GRID_EXTENDED = "grid_extended"
    VALUES_WRITTEN = "values_written"
    STYLES_CLEARED = "styles_cleared"
    BASE_STYLE_WRITTEN = "base_style_written"
    STYLE_RANGES_WRITTEN = "style_ranges_written"
    DIMENSIONS_WRITTEN = "dimensions_written"
    FREEZE_WRITTEN = "freeze_written"
    MERGES_WRITTEN = "merges_written"
    API_VERIFIED = "api_verified"
    EXPORT_VERIFIED = "export_verified"


WRITE_STEP_ORDER = tuple(WriteStep)


class RevisionState(StrEnum):
    PREVIEWED = "previewed"
    EXECUTING = "executing"
    RECOVERY_REQUIRED = "recovery_required"
    VERIFICATION_INCOMPLETE = "verification_incomplete"
    DELIVERED = "delivered"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class RevisionStep(StrEnum):
    NONE = "none"
    REVISION_RESERVED = "revision_reserved"
    GRID_EXTENDED = "grid_extended"
    BASE_MERGES_REMOVED = "base_merges_removed"
    NEXT_VALUES_WRITTEN = "next_values_written"
    RETIRED_VALUES_CLEARED = "retired_values_cleared"
    UNION_STYLES_CLEARED = "union_styles_cleared"
    NEXT_BASE_STYLE_WRITTEN = "next_base_style_written"
    NEXT_STYLE_RANGES_WRITTEN = "next_style_ranges_written"
    DIMENSIONS_WRITTEN = "dimensions_written"
    FREEZE_WRITTEN = "freeze_written"
    NEXT_MERGES_WRITTEN = "next_merges_written"
    API_VERIFIED = "api_verified"
    EXPORT_VERIFIED = "export_verified"
    VERSION_COMMITTED = "version_committed"


REVISION_STEP_ORDER = tuple(RevisionStep)
ACTIVE_REVISION_STATES = (
    RevisionState.PREVIEWED,
    RevisionState.EXECUTING,
    RevisionState.RECOVERY_REQUIRED,
    RevisionState.VERIFICATION_INCOMPLETE,
)


class ProtectedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_locator: str
    spreadsheet_token: str | None = None
    workbook_title: str
    workbook_url: str | None = None
    requested_workbook_title: str | None = None
    wiki_space_id: str | None = None
    parent_wiki_node_token: str | None = None
    parent_wiki_title: str | None = None
    created_wiki_node_token: str | None = None
    created_wiki_url: str | None = None
    worksheet_selector: str | None = None
    requested_sheet_title: str | None = None
    sheet_id: str | None = None
    sheet_title: str | None = None
    sheet_index: int | None = None
    initial_revision: str | None = None
    initial_sheet_count: int
    initial_child_count: int | None = None
    initial_state_hash: str


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_ref: str
    task_ref: str
    profile_ref: str
    placement_mode: PlacementMode
    spec_hash: str
    preview_hash: str
    delivery_hash: str | None
    target: ProtectedTarget
    state: OperationState
    last_completed_step: WriteStep
    ambiguous: bool
    remote_revision: str | None
    diagnostic_code: str | None
    created_at: datetime
    expires_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ManagedSheetRegistration:
    registration_ref: str
    initial_operation_ref: str
    profile_ref: str
    target: ProtectedTarget
    current_version: int
    spec_hash: str
    spec_summary: dict[str, int | str]
    delivery_hash: str | None
    remote_revision: str | None
    state: OperationState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ManagedSheetVersion:
    registration_ref: str
    managed_version: int
    parent_spec_hash: str | None
    spec_hash: str
    spec_summary: dict[str, int | str]
    operation_ref: str
    delivery_hash: str | None
    remote_revision: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    operation_ref: str
    registration_ref: str
    task_ref: str
    base_spec_hash: str
    next_spec_hash: str
    next_spec_summary: dict[str, int | str]
    preview_hash: str
    diff_summary: dict[str, Any]
    candidate_version: int
    state: RevisionState
    last_completed_step: RevisionStep
    ambiguous: bool
    remote_revision: str | None
    diagnostic_code: str | None
    base_api_hash: str
    base_export_hash: str
    delivery_hash: str | None
    created_at: datetime
    expires_at: datetime
    updated_at: datetime


class SecretProtector(Protocol):
    def protect(self, plaintext: str) -> str: ...

    def unprotect(self, protected_value: str) -> str: ...


class OperationStoreError(RuntimeError):
    """Raised when persistent write state cannot be trusted."""


class WindowsDpapiProtector:
    def __init__(
        self,
        *,
        entropy: bytes = b"workspace-capabilities/feishu/operations/v1",
    ) -> None:
        if os.name != "nt":
            raise OperationStoreError("Windows DPAPI is only available on Windows")
        self._entropy = entropy

    def protect(self, plaintext: str) -> str:
        if not plaintext:
            raise OperationStoreError("Operation state plaintext cannot be blank")
        protected = _crypt_protect(plaintext.encode("utf-8"), self._entropy)
        return base64.urlsafe_b64encode(protected).decode("ascii")

    def unprotect(self, protected_value: str) -> str:
        try:
            protected = base64.b64decode(
                protected_value.encode("ascii"), altchars=b"-_", validate=True
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise OperationStoreError(
                "Protected operation state is not valid base64"
            ) from exc
        try:
            return _crypt_unprotect(protected, self._entropy).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OperationStoreError(
                "Protected operation state is not valid UTF-8"
            ) from exc


class OperationStore:
    def __init__(
        self,
        path: Path,
        protector: SecretProtector,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path.resolve(strict=False)
        self._protector = protector
        self._clock = clock
        self._lock = threading.RLock()
        self._initialize()

    @classmethod
    def default(cls) -> OperationStore:
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise OperationStoreError(
                "LOCALAPPDATA is required for the Feishu operation store"
            )
        return cls(
            Path(local_app_data).joinpath(*DEFAULT_OPERATION_STORE_PARTS),
            WindowsDpapiProtector(),
        )

    def create_preview(
        self,
        *,
        task_ref: str,
        profile_ref: str,
        placement_mode: PlacementMode,
        spec_hash: str,
        preview_hash: str,
        target: ProtectedTarget,
        expires_at: datetime,
        operation_ref: str | None = None,
    ) -> OperationRecord:
        now = self._now()
        if expires_at <= now:
            raise ValueError("preview expiry must be in the future")
        operation_ref = operation_ref or f"wop_{secrets.token_hex(16)}"
        _validate_operation_ref(operation_ref)
        target_payload = self._protect_target(target)
        target_hash = _operation_target_hash(profile_ref, target)
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_ref, task_ref, profile_ref, placement_mode,
                        spec_hash, preview_hash, target_hash, protected_target,
                        state, last_completed_step, ambiguous, remote_revision,
                        diagnostic_code, created_at, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?)
                    """,
                    (
                        operation_ref,
                        task_ref,
                        profile_ref,
                        placement_mode.value,
                        spec_hash,
                        preview_hash,
                        target_hash,
                        target_payload,
                        OperationState.PREVIEWED.value,
                        WriteStep.NONE.value,
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationStoreError("Operation reference already exists") from exc
        return self.load(operation_ref)

    def load(self, operation_ref: str) -> OperationRecord:
        _validate_operation_ref(operation_ref)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_ref = ?",
                (operation_ref,),
            ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The Feishu write operation does not exist in this local Provider.",
            )
        return self._record(row)

    def load_matching(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        spec_hash: str,
    ) -> OperationRecord:
        record = self.load(operation_ref)
        if record.task_ref != task_ref or not secrets.compare_digest(
            record.spec_hash, spec_hash
        ):
            raise CapabilityError(
                CapabilityErrorCode.WRITE_CONFLICT,
                "The operation is not bound to this task and delivery specification.",
            )
        if record.state is OperationState.PREVIEWED and record.expires_at <= self._now():
            raise CapabilityError(
                CapabilityErrorCode.PREVIEW_EXPIRED,
                "The Feishu write preview has expired; create a new preview.",
            )
        if record.state in {
            OperationState.DECLINED,
            OperationState.CANCELLED,
        }:
            raise CapabilityError(
                CapabilityErrorCode.CONFIRMATION_REQUIRED,
                "The previous write confirmation was not accepted; create a new preview.",
            )
        return record

    def record_unaccepted_confirmation(
        self,
        operation_ref: str,
        *,
        action: OperationState,
    ) -> OperationRecord:
        if action not in {OperationState.DECLINED, OperationState.CANCELLED}:
            raise ValueError("confirmation action must be declined or cancelled")
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            state = OperationState(row["state"])
            if state is OperationState.PREVIEWED:
                connection.execute(
                    "UPDATE operations SET state = ?, updated_at = ? WHERE operation_ref = ?",
                    (action.value, _timestamp(now), operation_ref),
                )
        return self.load(operation_ref)

    def authorize_execution(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        spec_hash: str,
    ) -> OperationRecord:
        now = self._now()
        grant = secrets.token_urlsafe(32)
        grant_hash = _sha256(grant)
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            record = self._record(row)
            if record.task_ref != task_ref or not secrets.compare_digest(
                record.spec_hash, spec_hash
            ):
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "The accepted write no longer matches its task or specification.",
                )
            if (
                record.state is OperationState.PREVIEWED
                and record.expires_at <= now
            ):
                raise CapabilityError(
                    CapabilityErrorCode.PREVIEW_EXPIRED,
                    "The Feishu write preview expired before authorization was consumed.",
                )
            if record.state is not OperationState.PREVIEWED:
                raise CapabilityError(
                    CapabilityErrorCode.RECONCILIATION_REQUIRED,
                    "Only an unconsumed preview is eligible for a new execution grant.",
                    details={"operation_state": record.state.value},
                )
            connection.execute(
                """
                INSERT INTO write_grants (
                    grant_hash, operation_ref, issued_at, consumed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (grant_hash, operation_ref, _timestamp(now), _timestamp(now)),
            )
            connection.execute(
                """
                UPDATE operations
                SET state = ?, ambiguous = 0, diagnostic_code = NULL, updated_at = ?
                WHERE operation_ref = ?
                """,
                (OperationState.EXECUTING.value, _timestamp(now), operation_ref),
            )
        del grant
        return self.load(operation_ref)

    def resume_execution(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        spec_hash: str,
    ) -> OperationRecord:
        """Resume the already-authorized operation without creating a new grant."""
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            record = self._record(row)
            if record.task_ref != task_ref or not secrets.compare_digest(
                record.spec_hash, spec_hash
            ):
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "The recovery request no longer matches its authorized operation.",
                )
            if record.state not in {
                OperationState.RECOVERY_REQUIRED,
                OperationState.VERIFICATION_INCOMPLETE,
            }:
                raise CapabilityError(
                    CapabilityErrorCode.RECONCILIATION_REQUIRED,
                    "The Feishu write operation is not eligible for recovery.",
                    details={"operation_state": record.state.value},
                )
            connection.execute(
                """
                UPDATE operations
                SET state = ?, diagnostic_code = NULL, updated_at = ?
                WHERE operation_ref = ?
                """,
                (OperationState.EXECUTING.value, _timestamp(now), operation_ref),
            )
        return self.load(operation_ref)

    def update_target(
        self,
        operation_ref: str,
        target: ProtectedTarget,
        *,
        remote_revision: str | None = None,
    ) -> OperationRecord:
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            if OperationState(row["state"]) is not OperationState.EXECUTING:
                raise OperationStoreError("Only an executing operation can update its target")
            connection.execute(
                """
                UPDATE operations
                SET protected_target = ?, remote_revision = ?, updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    self._protect_target(target),
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load(operation_ref)

    def register_target(
        self,
        operation_ref: str,
        target: ProtectedTarget,
        *,
        remote_revision: str | None = None,
    ) -> OperationRecord:
        """Atomically persist the stable worksheet identity and its checkpoint."""
        if target.sheet_id is None or target.sheet_title is None:
            raise OperationStoreError("A registered target needs a stable sheet identity")
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            if OperationState(row["state"]) is not OperationState.EXECUTING:
                raise OperationStoreError("Only an executing operation can register a target")
            current = WriteStep(row["last_completed_step"])
            if WRITE_STEP_ORDER.index(current) > WRITE_STEP_ORDER.index(
                WriteStep.TARGET_REGISTERED
            ):
                raise OperationStoreError("Target registration cannot move a checkpoint backwards")
            connection.execute(
                """
                UPDATE operations
                SET protected_target = ?, last_completed_step = ?,
                    remote_revision = COALESCE(?, remote_revision), updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    self._protect_target(target),
                    WriteStep.TARGET_REGISTERED.value,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load(operation_ref)

    def register_workbook(
        self,
        operation_ref: str,
        target: ProtectedTarget,
        *,
        remote_revision: str | None = None,
    ) -> OperationRecord:
        """Atomically persist a newly created workbook identity and checkpoint."""
        if (
            target.spreadsheet_token is None
            or target.created_wiki_node_token is None
            or target.wiki_space_id is None
            or target.parent_wiki_node_token is None
        ):
            raise OperationStoreError(
                "A created workbook checkpoint needs stable Wiki and spreadsheet identities"
            )
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            if OperationState(row["state"]) is not OperationState.EXECUTING:
                raise OperationStoreError(
                    "Only an executing operation can register a workbook"
                )
            current = WriteStep(row["last_completed_step"])
            if WRITE_STEP_ORDER.index(current) > WRITE_STEP_ORDER.index(
                WriteStep.WORKBOOK_CREATED
            ):
                raise OperationStoreError(
                    "Workbook registration cannot move a checkpoint backwards"
                )
            connection.execute(
                """
                UPDATE operations
                SET protected_target = ?, last_completed_step = ?,
                    remote_revision = COALESCE(?, remote_revision), updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    self._protect_target(target),
                    WriteStep.WORKBOOK_CREATED.value,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load(operation_ref)

    def mark_step(
        self,
        operation_ref: str,
        step: WriteStep,
        *,
        remote_revision: str | None = None,
    ) -> OperationRecord:
        now = self._now()
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            if OperationState(row["state"]) is not OperationState.EXECUTING:
                raise OperationStoreError("Only an executing operation can advance")
            current = WriteStep(row["last_completed_step"])
            if WRITE_STEP_ORDER.index(step) < WRITE_STEP_ORDER.index(current):
                raise OperationStoreError("Operation step cannot move backwards")
            connection.execute(
                """
                UPDATE operations
                SET last_completed_step = ?, remote_revision = COALESCE(?, remote_revision),
                    updated_at = ?
                WHERE operation_ref = ?
                """,
                (step.value, remote_revision, _timestamp(now), operation_ref),
            )
        return self.load(operation_ref)

    def require_recovery(
        self,
        operation_ref: str,
        *,
        ambiguous: bool,
        diagnostic_code: str,
        verification_incomplete: bool = False,
    ) -> OperationRecord:
        now = self._now()
        state = (
            OperationState.VERIFICATION_INCOMPLETE
            if verification_incomplete
            else OperationState.RECOVERY_REQUIRED
        )
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            if OperationState(row["state"]) is not OperationState.EXECUTING:
                raise OperationStoreError("Only an executing operation can require recovery")
            connection.execute(
                """
                UPDATE operations
                SET state = ?, ambiguous = ?, diagnostic_code = ?, updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    state.value,
                    int(ambiguous),
                    _safe_diagnostic(diagnostic_code),
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load(operation_ref)

    def mark_delivered(
        self,
        operation_ref: str,
        *,
        spec_hash: str,
        spec_summary: dict[str, int | str] | None = None,
        delivery_hash: str,
        remote_revision: str | None,
    ) -> OperationRecord:
        now = self._now()
        registration_ref = f"managed_{secrets.token_hex(16)}"
        delivery_hash = _safe_content_hash(delivery_hash)
        with self._transaction() as connection:
            row = self._required_row(connection, operation_ref)
            record = self._record(row)
            if record.state is not OperationState.EXECUTING:
                raise OperationStoreError("Only an executing operation can be delivered")
            if record.last_completed_step is not WriteStep.EXPORT_VERIFIED:
                raise OperationStoreError("Export verification must complete before delivery")
            if record.target.sheet_id is None:
                raise OperationStoreError("Delivered operation has no stable sheet identifier")
            if record.target.spreadsheet_token is None:
                raise OperationStoreError("Delivered operation has no stable workbook identifier")
            if not secrets.compare_digest(record.spec_hash, spec_hash):
                raise OperationStoreError("Delivered operation spec hash changed")
            connection.execute(
                """
                UPDATE operations
                SET state = ?, ambiguous = 0, diagnostic_code = NULL,
                    delivery_hash = ?,
                    remote_revision = COALESCE(?, remote_revision), updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    OperationState.DELIVERED.value,
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
            connection.execute(
                """
                INSERT INTO managed_sheets (
                    registration_ref, operation_ref, profile_ref, target_hash,
                    sheet_hash, protected_target, spec_hash, state,
                    current_version, current_spec_summary, current_delivery_hash,
                    current_remote_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    registration_ref,
                    operation_ref,
                    record.profile_ref,
                    _target_hash(
                        record.profile_ref, record.target.spreadsheet_token
                    ),
                    _sha256(record.target.sheet_id),
                    self._protect_target(record.target),
                    spec_hash,
                    OperationState.DELIVERED.value,
                    _summary_json(spec_summary, spec_hash=spec_hash),
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            connection.execute(
                """
                INSERT INTO managed_sheet_versions (
                    registration_ref, managed_version, parent_spec_hash,
                    spec_hash, spec_summary, operation_ref, delivery_hash,
                    remote_revision, created_at
                ) VALUES (?, 1, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    registration_ref,
                    spec_hash,
                    _summary_json(spec_summary, spec_hash=spec_hash),
                    operation_ref,
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                ),
            )
        return self.load(operation_ref)

    def record_delivery_evidence(
        self,
        operation_ref: str,
        *,
        delivery_hash: str,
        remote_revision: str | None,
    ) -> OperationRecord:
        now = self._now()
        delivery_hash = _safe_content_hash(delivery_hash)
        with self._transaction() as connection:
            record = self._record(self._required_row(connection, operation_ref))
            if record.state is not OperationState.DELIVERED:
                raise OperationStoreError(
                    "Only a delivered operation can record delivery evidence"
                )
            if record.last_completed_step is not WriteStep.EXPORT_VERIFIED:
                raise OperationStoreError(
                    "Delivery evidence requires completed export verification"
                )
            if (
                record.delivery_hash is not None
                and not secrets.compare_digest(record.delivery_hash, delivery_hash)
            ):
                raise OperationStoreError("Delivered operation evidence hash changed")
            connection.execute(
                """
                UPDATE operations
                SET delivery_hash = ?,
                    remote_revision = COALESCE(?, remote_revision), updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
            connection.execute(
                """
                UPDATE managed_sheets
                SET current_delivery_hash = ?,
                    current_remote_revision = COALESCE(?, current_remote_revision),
                    updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
            connection.execute(
                """
                UPDATE managed_sheet_versions
                SET delivery_hash = ?, remote_revision = COALESCE(?, remote_revision)
                WHERE operation_ref = ?
                """,
                (delivery_hash, remote_revision, operation_ref),
            )
        return self.load(operation_ref)

    def managed_registration_count(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM managed_sheets"
            ).fetchone()
        return int(row["count"])

    def load_registration(self, registration_ref: str) -> ManagedSheetRegistration:
        _validate_registration_ref(registration_ref)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_sheets WHERE registration_ref = ?",
                (registration_ref,),
            ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The managed Feishu worksheet registration does not exist in this local Provider.",
            )
        return self._registration(row)

    def load_registration_for_operation(
        self, operation_ref: str
    ) -> ManagedSheetRegistration:
        _validate_operation_ref(operation_ref)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_sheets WHERE operation_ref = ?",
                (operation_ref,),
            ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The delivered Feishu write has no managed worksheet registration.",
            )
        return self._registration(row)

    def find_registrations(
        self,
        *,
        profile_ref: str,
        spreadsheet_token: str,
        sheet_id: str | None,
    ) -> tuple[ManagedSheetRegistration, ...]:
        target_hash = _target_hash(profile_ref, spreadsheet_token)
        parameters: list[str] = [profile_ref, target_hash]
        where = "profile_ref = ? AND target_hash = ?"
        if sheet_id is not None:
            where += " AND sheet_hash = ?"
            parameters.append(_sha256(sheet_id))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM managed_sheets WHERE {where} ORDER BY created_at",
                tuple(parameters),
            ).fetchall()
        return tuple(self._registration(row) for row in rows)

    def update_registration_target(
        self,
        registration_ref: str,
        target: ProtectedTarget,
        *,
        remote_revision: str | None,
    ) -> ManagedSheetRegistration:
        if target.sheet_id is None:
            raise OperationStoreError("A managed registration needs a stable sheet identifier")
        now = self._now()
        with self._transaction() as connection:
            row = self._required_registration_row(connection, registration_ref)
            current = self._registration(row)
            if current.target.spreadsheet_token != target.spreadsheet_token:
                raise OperationStoreError("Managed worksheet workbook identity changed")
            if current.target.sheet_id != target.sheet_id:
                raise OperationStoreError("Managed worksheet identity changed")
            connection.execute(
                """
                UPDATE managed_sheets
                SET protected_target = ?, current_remote_revision = COALESCE(?, current_remote_revision),
                    updated_at = ?
                WHERE registration_ref = ?
                """,
                (
                    self._protect_target(target),
                    remote_revision,
                    _timestamp(now),
                    registration_ref,
                ),
            )
        return self.load_registration(registration_ref)

    def find_revision(
        self,
        *,
        registration_ref: str,
        task_ref: str,
        base_spec_hash: str,
        next_spec_hash: str,
    ) -> RevisionRecord | None:
        _validate_registration_ref(registration_ref)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM managed_sheet_revisions
                WHERE registration_ref = ? AND task_ref = ?
                  AND base_spec_hash = ? AND next_spec_hash = ?
                """,
                (registration_ref, task_ref, base_spec_hash, next_spec_hash),
            ).fetchone()
        return None if row is None else self._revision(row)

    def load_active_revision(
        self, registration_ref: str
    ) -> RevisionRecord | None:
        _validate_registration_ref(registration_ref)
        states = tuple(state.value for state in ACTIVE_REVISION_STATES)
        placeholders = ",".join("?" for _ in states)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM managed_sheet_revisions
                WHERE registration_ref = ? AND state IN ({placeholders})
                """,
                (registration_ref, *states),
            ).fetchone()
        return None if row is None else self._revision(row)

    def create_revision(
        self,
        *,
        registration_ref: str,
        task_ref: str,
        base_spec_hash: str,
        next_spec_hash: str,
        next_spec_summary: dict[str, int | str],
        preview_hash: str,
        diff_summary: dict[str, Any],
        base_api_hash: str,
        base_export_hash: str,
        expires_at: datetime,
        operation_ref: str | None = None,
    ) -> RevisionRecord:
        now = self._now()
        if expires_at <= now:
            raise ValueError("revision preview expiry must be in the future")
        operation_ref = operation_ref or f"rev_{secrets.token_hex(16)}"
        _validate_revision_ref(operation_ref)
        _validate_registration_ref(registration_ref)
        with self._transaction() as connection:
            registration = self._registration(
                self._required_registration_row(connection, registration_ref)
            )
            if registration.state is not OperationState.DELIVERED:
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "The managed worksheet registration is not in a delivered state.",
                )
            identical = connection.execute(
                """
                SELECT * FROM managed_sheet_revisions
                WHERE registration_ref = ? AND task_ref = ?
                  AND base_spec_hash = ? AND next_spec_hash = ?
                """,
                (
                    registration_ref,
                    task_ref,
                    base_spec_hash,
                    next_spec_hash,
                ),
            ).fetchone()
            if identical is not None:
                return self._revision(identical)
            if not secrets.compare_digest(registration.spec_hash, base_spec_hash):
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "The supplied base specification is not the current managed version.",
                    details={"current_managed_version": registration.current_version},
                )
            active = connection.execute(
                """
                SELECT operation_ref FROM managed_sheet_revisions
                WHERE registration_ref = ?
                  AND state IN ('previewed', 'executing', 'recovery_required', 'verification_incomplete')
                """,
                (registration_ref,),
            ).fetchone()
            if active is not None:
                raise CapabilityError(
                    CapabilityErrorCode.WRITE_CONFLICT,
                    "Another managed worksheet revision is still active.",
                    details={"operation_ref": active["operation_ref"]},
                )
            try:
                connection.execute(
                    """
                    INSERT INTO managed_sheet_revisions (
                        operation_ref, registration_ref, task_ref,
                        base_spec_hash, next_spec_hash, next_spec_summary,
                        preview_hash, diff_summary, candidate_version, state,
                        last_completed_step, ambiguous, remote_revision,
                        diagnostic_code, base_api_hash, base_export_hash,
                        delivery_hash, created_at, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        operation_ref,
                        registration_ref,
                        task_ref,
                        base_spec_hash,
                        next_spec_hash,
                        _summary_json(next_spec_summary, spec_hash=next_spec_hash),
                        preview_hash,
                        _json_mapping(diff_summary),
                        registration.current_version + 1,
                        RevisionState.PREVIEWED.value,
                        RevisionStep.NONE.value,
                        base_api_hash,
                        base_export_hash,
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationStoreError("Managed worksheet revision conflicts with existing state") from exc
        return self.load_revision(operation_ref)

    def load_revision(self, operation_ref: str) -> RevisionRecord:
        _validate_revision_ref(operation_ref)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_sheet_revisions WHERE operation_ref = ?",
                (operation_ref,),
            ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The managed Feishu worksheet revision does not exist.",
            )
        return self._revision(row)

    def record_unaccepted_revision(
        self,
        operation_ref: str,
        *,
        action: RevisionState,
    ) -> RevisionRecord:
        if action not in {RevisionState.DECLINED, RevisionState.CANCELLED}:
            raise ValueError("revision confirmation action must be declined or cancelled")
        now = self._now()
        with self._transaction() as connection:
            row = self._required_revision_row(connection, operation_ref)
            if RevisionState(row["state"]) is RevisionState.PREVIEWED:
                connection.execute(
                    "UPDATE managed_sheet_revisions SET state = ?, updated_at = ? WHERE operation_ref = ?",
                    (action.value, _timestamp(now), operation_ref),
                )
        return self.load_revision(operation_ref)

    def authorize_revision(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        base_spec_hash: str,
        next_spec_hash: str,
    ) -> RevisionRecord:
        now = self._now()
        with self._transaction() as connection:
            record = self._revision(self._required_revision_row(connection, operation_ref))
            self._require_revision_identity(
                record,
                task_ref=task_ref,
                base_spec_hash=base_spec_hash,
                next_spec_hash=next_spec_hash,
            )
            if record.state is not RevisionState.PREVIEWED:
                raise CapabilityError(
                    CapabilityErrorCode.RECONCILIATION_REQUIRED,
                    "Only an unconsumed managed revision preview can be authorized.",
                    details={"operation_state": record.state.value},
                )
            if record.expires_at <= now:
                raise CapabilityError(
                    CapabilityErrorCode.PREVIEW_EXPIRED,
                    "The managed worksheet revision preview expired before confirmation.",
                )
            grant_hash = _sha256(secrets.token_urlsafe(32))
            connection.execute(
                """
                INSERT INTO managed_sheet_revision_grants (
                    grant_hash, operation_ref, issued_at, consumed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (grant_hash, operation_ref, _timestamp(now), _timestamp(now)),
            )
            connection.execute(
                """
                UPDATE managed_sheet_revisions
                SET state = ?, last_completed_step = ?, ambiguous = 0,
                    diagnostic_code = NULL, updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    RevisionState.EXECUTING.value,
                    RevisionStep.REVISION_RESERVED.value,
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load_revision(operation_ref)

    def resume_revision(
        self,
        *,
        operation_ref: str,
        task_ref: str,
        base_spec_hash: str,
        next_spec_hash: str,
    ) -> RevisionRecord:
        now = self._now()
        with self._transaction() as connection:
            record = self._revision(self._required_revision_row(connection, operation_ref))
            self._require_revision_identity(
                record,
                task_ref=task_ref,
                base_spec_hash=base_spec_hash,
                next_spec_hash=next_spec_hash,
            )
            if record.state not in {
                RevisionState.RECOVERY_REQUIRED,
                RevisionState.VERIFICATION_INCOMPLETE,
            }:
                raise CapabilityError(
                    CapabilityErrorCode.RECONCILIATION_REQUIRED,
                    "The managed worksheet revision is not eligible for recovery.",
                    details={"operation_state": record.state.value},
                )
            connection.execute(
                """
                UPDATE managed_sheet_revisions
                SET state = ?, diagnostic_code = NULL, updated_at = ?
                WHERE operation_ref = ?
                """,
                (RevisionState.EXECUTING.value, _timestamp(now), operation_ref),
            )
        return self.load_revision(operation_ref)

    def mark_revision_step(
        self,
        operation_ref: str,
        step: RevisionStep,
        *,
        remote_revision: str | None = None,
    ) -> RevisionRecord:
        now = self._now()
        with self._transaction() as connection:
            row = self._required_revision_row(connection, operation_ref)
            if RevisionState(row["state"]) is not RevisionState.EXECUTING:
                raise OperationStoreError("Only an executing revision can advance")
            current = RevisionStep(row["last_completed_step"])
            if REVISION_STEP_ORDER.index(step) < REVISION_STEP_ORDER.index(current):
                raise OperationStoreError("Revision step cannot move backwards")
            connection.execute(
                """
                UPDATE managed_sheet_revisions
                SET last_completed_step = ?, remote_revision = COALESCE(?, remote_revision),
                    updated_at = ? WHERE operation_ref = ?
                """,
                (step.value, remote_revision, _timestamp(now), operation_ref),
            )
        return self.load_revision(operation_ref)

    def require_revision_recovery(
        self,
        operation_ref: str,
        *,
        ambiguous: bool,
        diagnostic_code: str,
        verification_incomplete: bool = False,
    ) -> RevisionRecord:
        now = self._now()
        state = (
            RevisionState.VERIFICATION_INCOMPLETE
            if verification_incomplete
            else RevisionState.RECOVERY_REQUIRED
        )
        with self._transaction() as connection:
            row = self._required_revision_row(connection, operation_ref)
            if RevisionState(row["state"]) is not RevisionState.EXECUTING:
                raise OperationStoreError("Only an executing revision can require recovery")
            connection.execute(
                """
                UPDATE managed_sheet_revisions
                SET state = ?, ambiguous = ?, diagnostic_code = ?, updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    state.value,
                    int(ambiguous),
                    _safe_diagnostic(diagnostic_code),
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load_revision(operation_ref)

    def commit_revision(
        self,
        operation_ref: str,
        *,
        delivery_hash: str,
        remote_revision: str | None,
        target: ProtectedTarget,
    ) -> tuple[RevisionRecord, ManagedSheetRegistration]:
        now = self._now()
        delivery_hash = _safe_content_hash(delivery_hash)
        with self._transaction() as connection:
            record = self._revision(self._required_revision_row(connection, operation_ref))
            if record.state is not RevisionState.EXECUTING:
                raise OperationStoreError("Only an executing revision can be committed")
            if record.last_completed_step is not RevisionStep.EXPORT_VERIFIED:
                raise OperationStoreError("Revision export verification must complete before commit")
            registration = self._registration(
                self._required_registration_row(connection, record.registration_ref)
            )
            if registration.current_version != record.candidate_version - 1:
                raise OperationStoreError("Managed worksheet version pointer changed")
            if not secrets.compare_digest(registration.spec_hash, record.base_spec_hash):
                raise OperationStoreError("Managed worksheet baseline changed")
            if target.sheet_id != registration.target.sheet_id or target.spreadsheet_token != registration.target.spreadsheet_token:
                raise OperationStoreError("Managed worksheet target identity changed")
            connection.execute(
                """
                INSERT INTO managed_sheet_versions (
                    registration_ref, managed_version, parent_spec_hash,
                    spec_hash, spec_summary, operation_ref, delivery_hash,
                    remote_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.registration_ref,
                    record.candidate_version,
                    record.base_spec_hash,
                    record.next_spec_hash,
                    _summary_json(record.next_spec_summary, spec_hash=record.next_spec_hash),
                    operation_ref,
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                ),
            )
            connection.execute(
                """
                UPDATE managed_sheets
                SET protected_target = ?, spec_hash = ?, current_version = ?,
                    current_spec_summary = ?, current_delivery_hash = ?,
                    current_remote_revision = COALESCE(?, current_remote_revision),
                    updated_at = ?
                WHERE registration_ref = ?
                """,
                (
                    self._protect_target(target),
                    record.next_spec_hash,
                    record.candidate_version,
                    _summary_json(record.next_spec_summary, spec_hash=record.next_spec_hash),
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    record.registration_ref,
                ),
            )
            connection.execute(
                """
                UPDATE managed_sheet_revisions
                SET state = ?, last_completed_step = ?, ambiguous = 0,
                    diagnostic_code = NULL, delivery_hash = ?,
                    remote_revision = COALESCE(?, remote_revision), updated_at = ?
                WHERE operation_ref = ?
                """,
                (
                    RevisionState.DELIVERED.value,
                    RevisionStep.VERSION_COMMITTED.value,
                    delivery_hash,
                    remote_revision,
                    _timestamp(now),
                    operation_ref,
                ),
            )
        return self.load_revision(operation_ref), self.load_registration(record.registration_ref)

    def list_versions(
        self, registration_ref: str
    ) -> tuple[ManagedSheetVersion, ...]:
        _validate_registration_ref(registration_ref)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM managed_sheet_versions
                WHERE registration_ref = ? ORDER BY managed_version
                """,
                (registration_ref,),
            ).fetchall()
        return tuple(self._version(row) for row in rows)

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in {0, 1, 2, OPERATION_STORE_SCHEMA_VERSION}:
                    raise OperationStoreError(
                        f"Unsupported operation store schema version: {version}"
                    )
                if version == 0:
                    connection.executescript(
                        """
                        CREATE TABLE operations (
                            operation_ref TEXT PRIMARY KEY,
                            task_ref TEXT NOT NULL,
                            profile_ref TEXT NOT NULL,
                            placement_mode TEXT NOT NULL,
                            spec_hash TEXT NOT NULL,
                            preview_hash TEXT NOT NULL,
                            delivery_hash TEXT,
                            target_hash TEXT NOT NULL,
                            protected_target TEXT NOT NULL,
                            state TEXT NOT NULL,
                            last_completed_step TEXT NOT NULL,
                            ambiguous INTEGER NOT NULL CHECK (ambiguous IN (0, 1)),
                            remote_revision TEXT,
                            diagnostic_code TEXT,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX operations_target_hash_idx
                            ON operations(target_hash);
                        CREATE TABLE write_grants (
                            grant_hash TEXT PRIMARY KEY,
                            operation_ref TEXT NOT NULL REFERENCES operations(operation_ref),
                            issued_at TEXT NOT NULL,
                            consumed_at TEXT
                        );
                        CREATE TABLE managed_sheets (
                            registration_ref TEXT PRIMARY KEY,
                            operation_ref TEXT NOT NULL UNIQUE REFERENCES operations(operation_ref),
                            profile_ref TEXT NOT NULL,
                            target_hash TEXT NOT NULL,
                            sheet_hash TEXT NOT NULL,
                            protected_target TEXT NOT NULL,
                            spec_hash TEXT NOT NULL,
                            state TEXT NOT NULL,
                            current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
                            current_spec_summary TEXT NOT NULL,
                            current_delivery_hash TEXT,
                            current_remote_revision TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(profile_ref, target_hash, sheet_hash)
                        );
                        CREATE INDEX managed_sheets_target_idx
                            ON managed_sheets(profile_ref, target_hash, sheet_hash);
                        CREATE TABLE managed_sheet_versions (
                            registration_ref TEXT NOT NULL REFERENCES managed_sheets(registration_ref),
                            managed_version INTEGER NOT NULL CHECK (managed_version >= 1),
                            parent_spec_hash TEXT,
                            spec_hash TEXT NOT NULL,
                            spec_summary TEXT NOT NULL,
                            operation_ref TEXT NOT NULL UNIQUE,
                            delivery_hash TEXT,
                            remote_revision TEXT,
                            created_at TEXT NOT NULL,
                            PRIMARY KEY(registration_ref, managed_version)
                        );
                        CREATE TABLE managed_sheet_revisions (
                            operation_ref TEXT PRIMARY KEY,
                            registration_ref TEXT NOT NULL REFERENCES managed_sheets(registration_ref),
                            task_ref TEXT NOT NULL,
                            base_spec_hash TEXT NOT NULL,
                            next_spec_hash TEXT NOT NULL,
                            next_spec_summary TEXT NOT NULL,
                            preview_hash TEXT NOT NULL,
                            diff_summary TEXT NOT NULL,
                            candidate_version INTEGER NOT NULL CHECK (candidate_version >= 2),
                            state TEXT NOT NULL,
                            last_completed_step TEXT NOT NULL,
                            ambiguous INTEGER NOT NULL CHECK (ambiguous IN (0, 1)),
                            remote_revision TEXT,
                            diagnostic_code TEXT,
                            base_api_hash TEXT NOT NULL,
                            base_export_hash TEXT NOT NULL,
                            delivery_hash TEXT,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(registration_ref, task_ref, base_spec_hash, next_spec_hash),
                            UNIQUE(registration_ref, candidate_version)
                        );
                        CREATE UNIQUE INDEX managed_sheet_active_revision_idx
                            ON managed_sheet_revisions(registration_ref)
                            WHERE state IN ('previewed', 'executing', 'recovery_required', 'verification_incomplete');
                        CREATE TABLE managed_sheet_revision_grants (
                            grant_hash TEXT PRIMARY KEY,
                            operation_ref TEXT NOT NULL REFERENCES managed_sheet_revisions(operation_ref),
                            issued_at TEXT NOT NULL,
                            consumed_at TEXT
                        );
                        PRAGMA user_version = 3;
                        """
                    )
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        if version == 1:
                            operation_columns = {
                                row["name"]
                                for row in connection.execute(
                                    "PRAGMA table_info(operations)"
                                )
                            }
                            if "delivery_hash" not in operation_columns:
                                connection.execute(
                                    "ALTER TABLE operations ADD COLUMN delivery_hash TEXT"
                                )
                            connection.execute(
                                "CREATE INDEX IF NOT EXISTS operations_target_hash_idx ON operations(target_hash)"
                            )
                            connection.execute(
                                """
                                CREATE TABLE IF NOT EXISTS write_grants (
                                    grant_hash TEXT PRIMARY KEY,
                                    operation_ref TEXT NOT NULL REFERENCES operations(operation_ref),
                                    issued_at TEXT NOT NULL,
                                    consumed_at TEXT
                                )
                                """
                            )
                            connection.execute(
                                """
                                CREATE TABLE IF NOT EXISTS managed_sheets (
                                    registration_ref TEXT PRIMARY KEY,
                                    operation_ref TEXT NOT NULL UNIQUE REFERENCES operations(operation_ref),
                                    profile_ref TEXT NOT NULL,
                                    target_hash TEXT NOT NULL,
                                    sheet_hash TEXT NOT NULL,
                                    protected_target TEXT NOT NULL,
                                    spec_hash TEXT NOT NULL,
                                    state TEXT NOT NULL,
                                    created_at TEXT NOT NULL,
                                    updated_at TEXT NOT NULL,
                                    UNIQUE(profile_ref, target_hash, sheet_hash)
                                )
                                """
                            )
                            version = 2
                        if version == 2:
                            self._migrate_v2_to_v3(connection)
                        connection.execute(f"PRAGMA user_version = {OPERATION_STORE_SCHEMA_VERSION}")
                        connection.execute("COMMIT")
                    except Exception as exc:
                        connection.execute("ROLLBACK")
                        if isinstance(exc, OperationStoreError):
                            raise
                        raise OperationStoreError(
                            "Feishu operation store migration failed and was rolled back"
                        ) from exc
        except (OSError, sqlite3.Error, ValueError, ValidationError) as exc:
            raise OperationStoreError("Feishu operation store cannot be initialized") from exc

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(managed_sheets)")
        }
        additions = {
            "current_version": "INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1)",
            "current_spec_summary": "TEXT NOT NULL DEFAULT '{}'",
            "current_delivery_hash": "TEXT",
            "current_remote_revision": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE managed_sheets ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS managed_sheets_target_idx ON managed_sheets(profile_ref, target_hash, sheet_hash)"
        )
        connection.execute(
            """
            CREATE TABLE managed_sheet_versions (
                registration_ref TEXT NOT NULL REFERENCES managed_sheets(registration_ref),
                managed_version INTEGER NOT NULL CHECK (managed_version >= 1),
                parent_spec_hash TEXT,
                spec_hash TEXT NOT NULL,
                spec_summary TEXT NOT NULL,
                operation_ref TEXT NOT NULL UNIQUE,
                delivery_hash TEXT,
                remote_revision TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(registration_ref, managed_version)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE managed_sheet_revisions (
                operation_ref TEXT PRIMARY KEY,
                registration_ref TEXT NOT NULL REFERENCES managed_sheets(registration_ref),
                task_ref TEXT NOT NULL,
                base_spec_hash TEXT NOT NULL,
                next_spec_hash TEXT NOT NULL,
                next_spec_summary TEXT NOT NULL,
                preview_hash TEXT NOT NULL,
                diff_summary TEXT NOT NULL,
                candidate_version INTEGER NOT NULL CHECK (candidate_version >= 2),
                state TEXT NOT NULL,
                last_completed_step TEXT NOT NULL,
                ambiguous INTEGER NOT NULL CHECK (ambiguous IN (0, 1)),
                remote_revision TEXT,
                diagnostic_code TEXT,
                base_api_hash TEXT NOT NULL,
                base_export_hash TEXT NOT NULL,
                delivery_hash TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(registration_ref, task_ref, base_spec_hash, next_spec_hash),
                UNIQUE(registration_ref, candidate_version)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX managed_sheet_active_revision_idx
                ON managed_sheet_revisions(registration_ref)
                WHERE state IN ('previewed', 'executing', 'recovery_required', 'verification_incomplete')
            """
        )
        connection.execute(
            """
            CREATE TABLE managed_sheet_revision_grants (
                grant_hash TEXT PRIMARY KEY,
                operation_ref TEXT NOT NULL REFERENCES managed_sheet_revisions(operation_ref),
                issued_at TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        rows = connection.execute(
            """
            SELECT managed_sheets.*, operations.delivery_hash AS operation_delivery_hash,
                   operations.remote_revision AS operation_remote_revision
            FROM managed_sheets
            JOIN operations ON operations.operation_ref = managed_sheets.operation_ref
            ORDER BY managed_sheets.registration_ref
            """
        ).fetchall()
        for row in rows:
            _validate_registration_ref(row["registration_ref"])
            summary = _summary_json(None, spec_hash=row["spec_hash"])
            connection.execute(
                """
                UPDATE managed_sheets
                SET current_version = 1, current_spec_summary = ?,
                    current_delivery_hash = ?, current_remote_revision = ?
                WHERE registration_ref = ?
                """,
                (
                    summary,
                    row["operation_delivery_hash"],
                    row["operation_remote_revision"],
                    row["registration_ref"],
                ),
            )
            connection.execute(
                """
                INSERT INTO managed_sheet_versions (
                    registration_ref, managed_version, parent_spec_hash,
                    spec_hash, spec_summary, operation_ref, delivery_hash,
                    remote_revision, created_at
                ) VALUES (?, 1, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["registration_ref"],
                    row["spec_hash"],
                    summary,
                    row["operation_ref"],
                    row["operation_delivery_hash"],
                    row["operation_remote_revision"],
                    row["created_at"],
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=10.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA secure_delete = ON")
            return connection
        except sqlite3.Error as exc:
            raise OperationStoreError("Feishu operation store cannot be opened") from exc

    def _transaction(self):  # type: ignore[no-untyped-def]
        return _StoreTransaction(self)

    @staticmethod
    def _required_row(
        connection: sqlite3.Connection,
        operation_ref: str,
    ) -> sqlite3.Row:
        _validate_operation_ref(operation_ref)
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_ref = ?",
            (operation_ref,),
        ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The Feishu write operation does not exist in this local Provider.",
            )
        return row

    @staticmethod
    def _required_registration_row(
        connection: sqlite3.Connection,
        registration_ref: str,
    ) -> sqlite3.Row:
        _validate_registration_ref(registration_ref)
        row = connection.execute(
            "SELECT * FROM managed_sheets WHERE registration_ref = ?",
            (registration_ref,),
        ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The managed Feishu worksheet registration does not exist in this local Provider.",
            )
        return row

    @staticmethod
    def _required_revision_row(
        connection: sqlite3.Connection,
        operation_ref: str,
    ) -> sqlite3.Row:
        _validate_revision_ref(operation_ref)
        row = connection.execute(
            "SELECT * FROM managed_sheet_revisions WHERE operation_ref = ?",
            (operation_ref,),
        ).fetchone()
        if row is None:
            raise CapabilityError(
                CapabilityErrorCode.RESOURCE_NOT_FOUND,
                "The managed Feishu worksheet revision does not exist.",
            )
        return row

    @staticmethod
    def _require_revision_identity(
        record: RevisionRecord,
        *,
        task_ref: str,
        base_spec_hash: str,
        next_spec_hash: str,
    ) -> None:
        if (
            record.task_ref != task_ref
            or not secrets.compare_digest(record.base_spec_hash, base_spec_hash)
            or not secrets.compare_digest(record.next_spec_hash, next_spec_hash)
        ):
            raise CapabilityError(
                CapabilityErrorCode.WRITE_CONFLICT,
                "The managed worksheet revision is not bound to this task and specification pair.",
            )

    def _record(self, row: sqlite3.Row) -> OperationRecord:
        try:
            target = ProtectedTarget.model_validate_json(
                self._protector.unprotect(row["protected_target"])
            )
            return OperationRecord(
                operation_ref=row["operation_ref"],
                task_ref=row["task_ref"],
                profile_ref=row["profile_ref"],
                placement_mode=PlacementMode(row["placement_mode"]),
                spec_hash=row["spec_hash"],
                preview_hash=row["preview_hash"],
                delivery_hash=row["delivery_hash"],
                target=target,
                state=OperationState(row["state"]),
                last_completed_step=WriteStep(row["last_completed_step"]),
                ambiguous=bool(row["ambiguous"]),
                remote_revision=row["remote_revision"],
                diagnostic_code=row["diagnostic_code"],
                created_at=_parse_timestamp(row["created_at"]),
                expires_at=_parse_timestamp(row["expires_at"]),
                updated_at=_parse_timestamp(row["updated_at"]),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
            OperationStoreError,
        ) as exc:
            raise OperationStoreError("Feishu operation state is corrupt") from exc

    def _registration(self, row: sqlite3.Row) -> ManagedSheetRegistration:
        try:
            target = ProtectedTarget.model_validate_json(
                self._protector.unprotect(row["protected_target"])
            )
            current_version = int(row["current_version"])
            if current_version < 1:
                raise ValueError("managed version must be positive")
            return ManagedSheetRegistration(
                registration_ref=_validate_registration_ref(row["registration_ref"]),
                initial_operation_ref=_validate_operation_ref(row["operation_ref"]),
                profile_ref=row["profile_ref"],
                target=target,
                current_version=current_version,
                spec_hash=_safe_content_hash(row["spec_hash"]),
                spec_summary=_parse_summary(row["current_spec_summary"]),
                delivery_hash=(
                    None
                    if row["current_delivery_hash"] is None
                    else _safe_content_hash(row["current_delivery_hash"])
                ),
                remote_revision=row["current_remote_revision"],
                state=OperationState(row["state"]),
                created_at=_parse_timestamp(row["created_at"]),
                updated_at=_parse_timestamp(row["updated_at"]),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
            OperationStoreError,
            CapabilityError,
        ) as exc:
            raise OperationStoreError("Managed Feishu worksheet registration is corrupt") from exc

    @staticmethod
    def _revision(row: sqlite3.Row) -> RevisionRecord:
        try:
            candidate_version = int(row["candidate_version"])
            if candidate_version < 2:
                raise ValueError("candidate version must be at least two")
            return RevisionRecord(
                operation_ref=_validate_revision_ref(row["operation_ref"]),
                registration_ref=_validate_registration_ref(row["registration_ref"]),
                task_ref=row["task_ref"],
                base_spec_hash=_safe_content_hash(row["base_spec_hash"]),
                next_spec_hash=_safe_content_hash(row["next_spec_hash"]),
                next_spec_summary=_parse_summary(row["next_spec_summary"]),
                preview_hash=_safe_content_hash(row["preview_hash"]),
                diff_summary=_parse_json_mapping(row["diff_summary"]),
                candidate_version=candidate_version,
                state=RevisionState(row["state"]),
                last_completed_step=RevisionStep(row["last_completed_step"]),
                ambiguous=bool(row["ambiguous"]),
                remote_revision=row["remote_revision"],
                diagnostic_code=row["diagnostic_code"],
                base_api_hash=_safe_content_hash(row["base_api_hash"]),
                base_export_hash=_safe_content_hash(row["base_export_hash"]),
                delivery_hash=(
                    None
                    if row["delivery_hash"] is None
                    else _safe_content_hash(row["delivery_hash"])
                ),
                created_at=_parse_timestamp(row["created_at"]),
                expires_at=_parse_timestamp(row["expires_at"]),
                updated_at=_parse_timestamp(row["updated_at"]),
            )
        except (KeyError, TypeError, ValueError, CapabilityError) as exc:
            raise OperationStoreError("Managed Feishu worksheet revision state is corrupt") from exc

    @staticmethod
    def _version(row: sqlite3.Row) -> ManagedSheetVersion:
        try:
            managed_version = int(row["managed_version"])
            if managed_version < 1:
                raise ValueError("managed version must be positive")
            return ManagedSheetVersion(
                registration_ref=_validate_registration_ref(row["registration_ref"]),
                managed_version=managed_version,
                parent_spec_hash=(
                    None
                    if row["parent_spec_hash"] is None
                    else _safe_content_hash(row["parent_spec_hash"])
                ),
                spec_hash=_safe_content_hash(row["spec_hash"]),
                spec_summary=_parse_summary(row["spec_summary"]),
                operation_ref=row["operation_ref"],
                delivery_hash=(
                    None
                    if row["delivery_hash"] is None
                    else _safe_content_hash(row["delivery_hash"])
                ),
                remote_revision=row["remote_revision"],
                created_at=_parse_timestamp(row["created_at"]),
            )
        except (KeyError, TypeError, ValueError, CapabilityError) as exc:
            raise OperationStoreError("Managed Feishu worksheet version history is corrupt") from exc

    def _protect_target(self, target: ProtectedTarget) -> str:
        return self._protector.protect(target.model_dump_json())

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OperationStoreError("Operation store clock must be timezone-aware")
        return value.astimezone(UTC)


class _StoreTransaction:
    def __init__(self, store: OperationStore) -> None:
        self._store = store
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._store._lock.acquire()
        try:
            self._connection = self._store._connect()
            self._connection.execute("BEGIN IMMEDIATE")
            return self._connection
        except Exception:
            self._store._lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
        assert self._connection is not None
        try:
            if exc_type is None:
                self._connection.execute("COMMIT")
            else:
                self._connection.execute("ROLLBACK")
        finally:
            self._connection.close()
            self._store._lock.release()
        return False


def _validate_operation_ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 36
        or not value.startswith("wop_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise CapabilityError(
            CapabilityErrorCode.RESOURCE_NOT_FOUND,
            "The Feishu write operation reference is invalid.",
        )
    return value


def _validate_revision_ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 36
        or not value.startswith("rev_")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise CapabilityError(
            CapabilityErrorCode.RESOURCE_NOT_FOUND,
            "The managed Feishu worksheet revision reference is invalid.",
        )
    return value


def _validate_registration_ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or not value.startswith("managed_")
        or any(character not in "0123456789abcdef" for character in value[8:])
    ):
        raise CapabilityError(
            CapabilityErrorCode.RESOURCE_NOT_FOUND,
            "The managed Feishu worksheet registration reference is invalid.",
        )
    return value


def _target_hash(profile_ref: str, spreadsheet_token: str) -> str:
    return _sha256(f"{profile_ref}\x1f{spreadsheet_token}")


def _operation_target_hash(profile_ref: str, target: ProtectedTarget) -> str:
    if target.spreadsheet_token is not None:
        return _target_hash(profile_ref, target.spreadsheet_token)
    if (
        target.wiki_space_id is None
        or target.parent_wiki_node_token is None
        or target.requested_workbook_title is None
    ):
        raise OperationStoreError(
            "A pending workbook target needs a stable Wiki parent and requested title"
        )
    return _sha256(
        "\x1f".join(
            (
                profile_ref,
                "pending-wiki-workbook",
                target.wiki_space_id,
                target.parent_wiki_node_token,
                target.requested_workbook_title.casefold(),
            )
        )
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_diagnostic(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(not (character.isascii() and (character.isalnum() or character in "_-.:")) for character in normalized)
    ):
        raise ValueError("diagnostic code must be a safe machine identifier")
    return normalized


def _safe_content_hash(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
        raise ValueError("content hash must be a canonical SHA-256 identifier")
    return normalized


def _summary_json(
    value: dict[str, int | str] | None,
    *,
    spec_hash: str,
) -> str:
    summary = dict(value or {})
    summary["content_hash"] = spec_hash
    return json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_mapping(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("revision metadata exceeds the safe size limit")
    return encoded


def _parse_summary(value: object) -> dict[str, int | str]:
    parsed = _parse_json_mapping(value)
    if len(parsed) > 32 or any(
        not isinstance(key, str)
        or not isinstance(item, (int, str))
        or isinstance(item, bool)
        or (isinstance(item, str) and len(item) > 256)
        for key, item in parsed.items()
    ):
        raise ValueError("managed worksheet specification summary is invalid")
    return parsed  # type: ignore[return-value]


def _parse_json_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 64 * 1024:
        raise ValueError("managed worksheet metadata must be bounded JSON text")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("managed worksheet metadata must be a JSON object")
    return parsed


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return parsed.astimezone(UTC)


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


def _crypt_protect(value: bytes, entropy: bytes) -> bytes:
    crypt32, kernel32 = _windows_crypto_libraries()
    input_blob, input_buffer = _input_blob(value)
    entropy_blob, entropy_buffer = _input_blob(entropy)
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise OperationStoreError(
            f"Windows DPAPI protect failed: {ctypes.get_last_error()}"
        )
    try:
        del input_buffer, entropy_buffer
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def _crypt_unprotect(value: bytes, entropy: bytes) -> bytes:
    crypt32, kernel32 = _windows_crypto_libraries()
    input_blob, input_buffer = _input_blob(value)
    entropy_blob, entropy_buffer = _input_blob(entropy)
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise OperationStoreError(
            f"Windows DPAPI unprotect failed: {ctypes.get_last_error()}"
        )
    try:
        del input_buffer, entropy_buffer
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def _windows_crypto_libraries():  # type: ignore[no-untyped-def]
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise OperationStoreError("Windows DPAPI libraries are unavailable") from exc
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32
