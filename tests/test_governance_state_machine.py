import pytest

from memguard.api.server import handle_request
from memguard.governance.state import (
    InvalidGovernanceTransition,
    UnknownGovernanceState,
    governance_state_catalog,
)
from memguard.service import MemGuardService


def service_at(tmp_path):
    return MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )


def test_governance_transition_is_atomic_and_rejects_reactivation(tmp_path):
    service = service_at(tmp_path)
    try:
        record = service.ingest_text(
            "保留经过核验的本地项目约束。", source_kind="manual",
        )["record"]

        transition = service.meta.set_governance_state(
            record.memory_id, "quarantined", "untrusted", 0.0, "manual review",
        )
        assert transition.to_dict() == {
            "from": "active", "to": "quarantined", "changed": True,
        }
        assert service.meta.get_memory(record.memory_id)["governance_state"] == "quarantined"

        with pytest.raises(InvalidGovernanceTransition):
            service.meta.set_governance_state(
                record.memory_id, "active", "trusted", 1.0, "reactivate in place",
            )
        with pytest.raises(InvalidGovernanceTransition):
            service.meta.upsert_memory(record)
        assert service.meta.get_memory(record.memory_id)["governance_state"] == "quarantined"
    finally:
        service.close()


def test_forgotten_state_is_terminal_and_unknown_state_fails_closed(tmp_path):
    service = service_at(tmp_path)
    try:
        record = service.ingest_text("一条待遗忘的知识。", source_kind="manual")["record"]
        service.meta.mark_deleted(record.memory_id)
        assert service.meta.get_memory(record.memory_id)["governance_state"] == "forgotten"

        with pytest.raises(InvalidGovernanceTransition):
            service.meta.set_governance_state(
                record.memory_id, "active", "trusted", 1.0, "must not revive",
            )
        with pytest.raises(UnknownGovernanceState):
            service.meta.set_governance_state(
                record.memory_id, "invented", "trusted", 1.0, "invalid state",
            )
    finally:
        service.close()


def test_governance_catalog_is_exposed_for_cli_and_api_surfaces(tmp_path):
    catalog = governance_state_catalog()
    assert "forgotten" in catalog["terminal_states"]
    assert "superseded" in catalog["transitions"]["active"]

    service = service_at(tmp_path)
    try:
        response = handle_request(service, {"command": "governance_states"})
        assert response["ok"] is True
        assert response["result"] == catalog
    finally:
        service.close()
