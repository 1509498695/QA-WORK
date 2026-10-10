from __future__ import annotations

import hashlib
import base64
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from feishu_auth_service.models import AuthResult, PlatformIdentityClaim


class StateStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class StateRecord:
    request_ref: str
    created_at: float
    expires_at: float
    purpose: str = "provider"
    client_state: str | None = None
    return_uri: str | None = None
    code_challenge: str | None = None


@dataclass(frozen=True, slots=True)
class StateConsumption:
    status: StateStatus
    record: StateRecord | None = None


class OAuthStateStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._states: dict[str, StateRecord] = {}
        self._terminal: dict[str, tuple[StateStatus, float]] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        purpose: str = "provider",
        client_state: str | None = None,
        return_uri: str | None = None,
        code_challenge: str | None = None,
    ) -> tuple[str, StateRecord]:
        now = self._clock()
        state = secrets.token_urlsafe(32)
        record = StateRecord(
            request_ref=f"oauth_{secrets.token_urlsafe(12)}",
            created_at=now,
            expires_at=now + self._ttl_seconds,
            purpose=purpose,
            client_state=client_state,
            return_uri=return_uri,
            code_challenge=code_challenge,
        )
        digest = _digest(state)
        with self._lock:
            self._cleanup(now)
            self._states[digest] = record
        return state, record

    def consume(self, state: str | None) -> StateConsumption:
        if not state:
            return StateConsumption(StateStatus.INVALID)
        now = self._clock()
        digest = _digest(state)
        with self._lock:
            self._cleanup(now)
            terminal = self._terminal.get(digest)
            if terminal is not None:
                return StateConsumption(terminal[0])
            record = self._states.pop(digest, None)
            if record is None:
                return StateConsumption(StateStatus.INVALID)
            if record.expires_at <= now:
                self._terminal[digest] = (StateStatus.EXPIRED, now + self._ttl_seconds)
                return StateConsumption(StateStatus.EXPIRED)
            self._terminal[digest] = (StateStatus.REPLAYED, now + self._ttl_seconds)
            return StateConsumption(StateStatus.VALID, record)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._terminal.clear()

    def pending_count(self) -> int:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            return len(self._states)

    def _cleanup(self, now: float) -> None:
        self._cleanup_states(now)
        self._cleanup_terminal(now)

    def _cleanup_states(self, now: float) -> None:
        expired_states = [key for key, record in self._states.items() if record.expires_at <= now]
        for key in expired_states:
            self._states.pop(key, None)
            self._terminal[key] = (StateStatus.EXPIRED, now + self._ttl_seconds)

    def _cleanup_terminal(self, now: float) -> None:
        expired_terminal = [
            key for key, (_, expires_at) in self._terminal.items() if expires_at <= now
        ]
        for key in expired_terminal:
            self._terminal.pop(key, None)


@dataclass(frozen=True, slots=True)
class StoredResult:
    result: AuthResult
    expires_at: float


class AuthResultStore:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._results: dict[str, StoredResult] = {}
        self._lock = threading.Lock()

    def put(self, result: AuthResult) -> str:
        result_ref = f"result_{secrets.token_urlsafe(18)}"
        with self._lock:
            self._cleanup(self._clock())
            self._results[result_ref] = StoredResult(
                result=result,
                expires_at=self._clock() + self._ttl_seconds,
            )
        return result_ref

    def get(self, result_ref: str) -> AuthResult | None:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            stored = self._results.get(result_ref)
            return stored.result if stored is not None else None

    def clear(self) -> None:
        with self._lock:
            self._results.clear()

    def count(self) -> int:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            return len(self._results)

    def _cleanup(self, now: float) -> None:
        expired = [key for key, stored in self._results.items() if stored.expires_at <= now]
        for key in expired:
            self._results.pop(key, None)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredIdentityTicket:
    claim: PlatformIdentityClaim
    code_challenge: str
    expires_at: float


class IdentityTicketStore:
    """Short-lived, single-use PKCE-bound identity handoff tickets."""

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._tickets: dict[str, StoredIdentityTicket] = {}
        self._lock = threading.Lock()

    def issue(self, claim: PlatformIdentityClaim, *, code_challenge: str) -> str:
        ticket = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            self._tickets[_digest(ticket)] = StoredIdentityTicket(
                claim=claim,
                code_challenge=code_challenge,
                expires_at=now + self._ttl_seconds,
            )
        return ticket

    def redeem(self, ticket: str, *, code_verifier: str) -> PlatformIdentityClaim | None:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            stored = self._tickets.pop(_digest(ticket), None)
        if stored is None or stored.expires_at <= now:
            return None
        supplied_challenge = _pkce_challenge(code_verifier)
        if not hmac.compare_digest(supplied_challenge, stored.code_challenge):
            return None
        return stored.claim

    def clear(self) -> None:
        with self._lock:
            self._tickets.clear()

    def count(self) -> int:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            return len(self._tickets)

    def _cleanup(self, now: float) -> None:
        expired = [key for key, item in self._tickets.items() if item.expires_at <= now]
        for key in expired:
            self._tickets.pop(key, None)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
