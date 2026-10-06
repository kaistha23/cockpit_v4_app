"""REAL DATABASE · NO MODEL.

Selection persistence and run pinning are durable, per principal facts."""
from __future__ import annotations

from backend.cockpit_v4.run_store import RunStore


def test_preferences_are_scoped_to_tenant_and_principal(tmp_path) -> None:
    store = RunStore(tmp_path / "state.sqlite3")
    store.set_model_preference(tenant_id="tenant-a", principal_id="alice", provider="omlx", model="local")
    assert store.model_preference(tenant_id="tenant-a", principal_id="alice") == {"provider": "omlx", "model": "local"}
    assert store.model_preference(tenant_id="tenant-a", principal_id="bob") is None
    assert store.model_preference(tenant_id="tenant-b", principal_id="alice") is None


def test_accepted_run_keeps_its_provider_and_model(tmp_path) -> None:
    store = RunStore(tmp_path / "state.sqlite3")
    record, created = store.accept_run(thread_id="thread", tenant_id="tenant", principal_id="user", question="q", mode="standard", release_id="r", ui_filters={}, idempotency_key="", body_digest="", startup_sha="", deadline_at="", provider="omlx", model="qualified")
    assert created
    restored = store.get_run(record.run_id)
    assert restored is not None
    assert (restored.provider, restored.model) == ("omlx", "qualified")
