from __future__ import annotations

from feishu_auth_service.state import OAuthStateStore, StateStatus


def test_state_is_single_use() -> None:
    now = [100.0]
    store = OAuthStateStore(60, clock=lambda: now[0])
    state, _ = store.create()

    assert store.consume(state).status is StateStatus.VALID
    assert store.consume(state).status is StateStatus.REPLAYED


def test_expired_state_is_rejected() -> None:
    now = [100.0]
    store = OAuthStateStore(60, clock=lambda: now[0])
    state, _ = store.create()
    now[0] = 161.0

    assert store.consume(state).status is StateStatus.EXPIRED


def test_status_cleanup_preserves_expired_state_reason() -> None:
    now = [100.0]
    store = OAuthStateStore(60, clock=lambda: now[0])
    state, _ = store.create()
    now[0] = 161.0

    assert store.pending_count() == 0
    assert store.consume(state).status is StateStatus.EXPIRED
