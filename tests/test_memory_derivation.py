from dataclasses import replace

import pytest

from memguard.models import MemorySource, VersionInfo, make_memory, new_id
from memguard.retrieval.vector_cache import vector_content_hash
from memguard.service import MemGuardService
from memguard.store.dependencies import DependencyChanged


@pytest.fixture
def service(tmp_path):
    instance = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    yield instance
    instance.close()


def write(service, text, parents=(), **kwargs):
    record = make_memory(text, "procedure" if parents else "knowledge",
                         MemorySource("verified_procedure" if parents else "manual"),
                         scope=service.namespace,
                         dependencies=[{"memory_id": p.memory_id, "version_id": p.version.version_id,
                                        "relation": "derived_from"} for p in parents], **kwargs)
    return service._persist_new_memory(record)


def test_dependency_roundtrip_and_detail(service):
    source = write(service, "Release preparation requires an inventory review.")
    child = write(service, "Review inventory before creating release notes.", [source])
    assert child.from_json(child.to_json()).dependencies == child.dependencies
    detail = service.memory_detail(child.memory_id, light=True)
    assert detail["dependencies"][0]["parent_version_id"] == source.version.version_id
    assert service.dependency_impact([source.memory_id])["derived_memories"][0]["memory_id"] == child.memory_id


def test_source_change_atomically_invalidates_descendants_not_related(service):
    source = write(service, "Inventory preparation uses a read-only review.")
    child = write(service, "Prepare the reviewed inventory output.", [source])
    grandchild = write(service, "Publish the reviewed inventory output.", [child])
    independent = write(service, "Independent inventory notes can be searched.")
    service.search("inventory")  # Warm cache must not bypass current eligibility.
    assert service.update_memory(source.memory_id, "Inventory preparation requires two reviewers.")["updated"]
    assert service.meta.get_memory(child.memory_id)["governance_state"] == "source_invalidated"
    assert service.meta.get_memory(grandchild.memory_id)["governance_state"] == "source_invalidated"
    assert service.lifecycle_snapshot(child.memory_id)["atom"]["lifecycle_state"] == "source_invalidated"
    assert service.meta.get_memory(independent.memory_id)["governance_state"] == "active"
    assert not {child.memory_id, grandchild.memory_id} & {r["memory_id"] for r in service.search("inventory", 20)}
    assert service.meta.get_memory(child.memory_id)["deleted"] == 0


def test_hard_derivatives_erased_independent_sibling_retained(service):
    source = write(service, "Invoice workflow is based on an approved inventory.")
    child = write(service, "Approved inventory invoice workflow.", [source])
    grandchild = write(service, "Prepare invoice draft from approved workflow.", [child])
    independent = write(service, "An independent customer invoice template.")
    outcome = service.forget_memory_ids([source.memory_id], "source withdrawn", compact=True)
    assert outcome["ok"]
    assert set(outcome["forgotten_memory_ids"]) == {source.memory_id, child.memory_id, grandchild.memory_id}
    assert set(outcome["derived_memory_ids"]) == {child.memory_id, grandchild.memory_id}
    assert all(item["irreversible_ok"] for item in outcome["verification"])
    assert service.meta.get_memory(independent.memory_id)["deleted"] == 0
    assert service.meta.memory_dependencies(child.memory_id) == []


def test_preview_snapshots_include_derivatives_and_require_confirmation(service):
    source = write(service, "Inventory source for review.")
    child = write(service, "Inventory derived review workflow.", [source])
    result = service.forget_memory_ids([source.memory_id], "withdraw", expected_versions={source.memory_id: source.version.version_id})
    assert result["reason"] == "stale_preview"
    assert result["stale_memory_ids"] == [child.memory_id]
    assert not service.meta.get_memory(source.memory_id)["deleted"]
    result = service.forget_memory_ids([source.memory_id], "withdraw", expected_versions={
        source.memory_id: source.version.version_id, child.memory_id: child.version.version_id,
    })
    assert result["ok"]


def test_new_derivative_after_erasure_snapshot_blocks_whole_transaction(service):
    source = write(service, "Source before a concurrent derivation.")
    child = write(service, "Concurrent derived result.", [source])
    with pytest.raises(DependencyChanged):
        service.meta.begin_memory_erasure(source.memory_id, new_id("ver"), compact=True,
                                         plaintext=source.content, expected_version_id=source.version.version_id)
    assert not service.meta.get_memory(source.memory_id)["deleted"]
    assert service.meta.get_memory(child.memory_id)["governance_state"] == "active"


def test_derivative_arriving_after_confirmation_is_not_silently_added(service, monkeypatch):
    source = write(service, "Source before confirmation race.")
    original = service._begin_memory_erasures
    created = []

    def interleave(records, **kwargs):
        created.append(write(service, "Derivative arriving after confirmation.", [source]))
        return original(records, **kwargs)

    monkeypatch.setattr(service, "_begin_memory_erasures", interleave)
    with pytest.raises(DependencyChanged):
        service.forget_memory_ids([source.memory_id], "withdraw", expected_versions={source.memory_id: source.version.version_id})
    assert not service.meta.get_memory(source.memory_id)["deleted"]
    assert service.meta.get_memory(created[0].memory_id)["governance_state"] == "active"


@pytest.mark.parametrize("source_kwargs,child_kwargs", [
    ({"scope": "foreign"}, {}),
    ({"project_root": "/project/a"}, {"project_root": "/project/b"}),
    ({"project_root": "/project/a"}, {}),
    ({"retention_session_id": "session-a", "tier": "session"}, {}),
    ({"trust_level": "untrusted"}, {}),
    ({"governance_state": "quarantined"}, {}),
])
def test_source_scope_and_trust_cannot_be_laundered(service, source_kwargs, child_kwargs):
    source = make_memory("A scoped source for derivation.", "knowledge", MemorySource("manual"),
                         **({"scope": service.namespace} | source_kwargs))
    service._persist_new_memory(source)
    with pytest.raises(DependencyChanged):
        write(service, "Derived result cannot broaden its source.", [source], **child_kwargs)


def test_staged_derived_write_cancelled_after_source_change(service):
    source = write(service, "Review source for staged workflow.")
    child = make_memory("Staged workflow result.", "procedure", MemorySource("verified_procedure"),
                        scope=service.namespace, dependencies=[{
                            "memory_id": source.memory_id, "version_id": source.version.version_id,
                            "relation": "derived_from",
                        }])
    service.meta.stage_new_memory_write(child, service.EMBEDDING_BACKEND,
        service.EMBEDDING_MODEL_REVISION, vector_content_hash(child.summary, child.content))
    current = replace(source, version=VersionInfo(), content="Source changed concurrently.")
    service.meta.upsert_memory(current)
    assert service._recover_pending_memory_writes() == 1
    assert service.meta.pending_memory_writes() == []
    assert service.store.get_latest(child.memory_id) is None
    assert service.meta.get_memory(child.memory_id) is None


def test_cyclic_edges_and_similarity_edges_rejected(service):
    source = write(service, "Inventory source.")
    child = write(service, "Inventory derived result.", [source])
    cyclic = replace(source, dependencies=[{"memory_id": child.memory_id,
        "version_id": child.version.version_id, "relation": "derived_from"}])
    with pytest.raises(DependencyChanged):
        service.meta.upsert_memory(cyclic)
    related = replace(source, dependencies=[{"memory_id": child.memory_id,
        "version_id": child.version.version_id, "relation": "similar_to"}])
    with pytest.raises(DependencyChanged):
        service.meta.upsert_memory(related)


def test_erasure_cancels_uncommitted_derived_plaintext_in_same_transaction(service):
    source = write(service, "The private source passage must not remain in staging.")
    child = make_memory(source.content, "procedure", MemorySource("verified_procedure"),
                        scope=service.namespace, dependencies=[{
                            "memory_id": source.memory_id, "version_id": source.version.version_id,
                            "relation": "derived_from",
                        }])
    service.meta.stage_new_memory_write(child, service.EMBEDDING_BACKEND,
        service.EMBEDDING_MODEL_REVISION, vector_content_hash(child.summary, child.content))
    service.meta.begin_memory_erasure(source.memory_id, new_id("ver"), compact=True,
                                     plaintext=source.content, expected_version_id=source.version.version_id)
    assert service.meta.pending_memory_writes() == []
    service._recover_pending_erasures()
    assert not service.store.raw_contains(source.content)
    assert service.meta.get_memory(child.memory_id) is None


def test_invalidated_derivative_can_be_physically_erased(service):
    source = write(service, "Inventory source before change.")
    child = write(service, "Derived inventory result before change.", [source])
    service.update_memory(source.memory_id, "Inventory source after change.")
    result = service.forget_memory_ids([source.memory_id], "revoke obsolete source", compact=True)
    assert child.memory_id in result["forgotten_memory_ids"]
    assert not service.store.raw_contains(child.content)


def test_feedback_is_versioned_idempotent_and_removed_by_forgetting(service):
    source = write(service, "Invoice workflow source.")
    child = write(service, "Invoice workflow checked output.", [source])
    first = service.meta.record_procedure_feedback(child.memory_id, child.version.version_id, "s1", "u1", "pass")
    second = service.meta.record_procedure_feedback(child.memory_id, child.version.version_id, "s1", "u1", "pass")
    assert first["recorded"] and not second["recorded"]
    with pytest.raises(ValueError):
        service.meta.record_procedure_feedback(child.memory_id, child.version.version_id, "s1", "u1", "fail")
    service.meta.record_procedure_feedback(child.memory_id, child.version.version_id, "s1", "u2", "unknown")
    assert service.meta.procedure_stats(child.memory_id) == {"pass": 1, "fail": 0, "unknown": 1, "measured": 1, "success_rate": 1.0}
    service.forget_memory_ids([source.memory_id], "withdraw")
    assert service.meta.procedure_stats(child.memory_id)["measured"] == 0


def test_derived_record_cannot_be_edited_without_verification(service):
    source = write(service, "Source for checked workflow.")
    child = write(service, "Checked workflow.", [source])
    assert service.update_memory(child.memory_id, "Unverified replacement.")["reason"] == "procedure_requires_revalidation"


def test_natural_forget_preview_reports_hard_derivation(service):
    source = write(service, "Invoice inventory source.")
    child = write(service, "Derived procedure for packing slips.", [source])
    preview = service.forget("Forget invoice inventory")
    candidates = {r["memory_id"]: r for r in preview["candidates"]}
    assert candidates[child.memory_id]["derived"]
    assert candidates[child.memory_id]["version_id"] == child.version.version_id
