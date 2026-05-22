import json
import socket
import sys
import threading
import time
from pathlib import Path

from memguard.api.server import handle_request, serve_unix_socket
from memguard.cli.memctl import main as memctl_main
from memguard.config import default_db_path, default_store_path, list_namespaces, namespace_status, normalize_namespace
from memguard.ingestion.behavior import parse_shell_history
from memguard.llm.adapter import generate_answer
from memguard.llm.prompt import render_generation_prompt
from memguard.service import MemGuardService
from memguard.ingestion.tool_result import ToolResult
from memguard.os_guard.doctor import doctor_report
from memguard.os_guard.auditd import audit_rules_file, auditctl_rules, ausearch_command
from memguard.os_guard.auditd_events import parse_auditd_events
from memguard.extraction.entity import extract_entities, extract_relations
from memguard.eval.run_eval import run as run_eval
from memguard.retrieval.hybrid import HybridRetriever
from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend, kylin_sdk_status
from memguard.retrieval.conflict import preference_topic
from memguard.daemon import daemon_health_snapshot, run_daemon
from memguard.store.sqlite_meta import SQLiteMetaStore
from scripts.demo_smoke import main as demo_smoke_main


def make_service(tmp_path: Path) -> MemGuardService:
    return MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memguard.sqlite3")


def test_namespace_paths_are_safe_and_default_is_backward_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    assert normalize_namespace(None) == "default"
    assert default_store_path() == tmp_path / "home" / "memory.segment"
    assert default_db_path() == tmp_path / "home" / "memguard.sqlite3"
    assert default_store_path("project-a") == tmp_path / "home" / "namespaces" / "project-a" / "memory.segment"

    for invalid in ("../escape", ".hidden", "bad/name", "bad name", "CON", "CON.txt"):
        try:
            normalize_namespace(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid namespace was accepted: %s" % invalid)


def test_read_only_namespace_status_and_cli_do_not_create_storage(tmp_path, monkeypatch, capsys):
    home = tmp_path / "dry-home"
    monkeypatch.setenv("MEMGUARD_HOME", str(home))
    monkeypatch.delenv("MEMGUARD_NAMESPACE", raising=False)

    status = namespace_status("demo")
    assert status["namespace"] == "demo"
    assert status["home_exists"] is False
    assert status["namespace_home_exists"] is False
    assert status["record_count"] == 0
    assert not home.exists()

    names = list_namespaces()
    assert [item["namespace"] for item in names] == ["default"]
    assert not home.exists()

    report = doctor_report(namespace="demo")
    assert report["namespace"] == "demo"
    assert report["namespace_status"]["namespace_home_exists"] is False
    assert not home.exists()

    assert memctl_main(["namespaces"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["namespace"] == "default"
    assert not home.exists()


def test_namespace_isolation_keeps_records_patterns_and_audit_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    alpha = MemGuardService(namespace="alpha")
    beta = MemGuardService(namespace="beta")
    try:
        alpha_record = alpha.ingest_text("alpha 会话只保存 openKylin systemd MemoryMax 方案。")["record"]
        beta_record = beta.ingest_text("beta 会话只保存 auditd shadow access 方案。")["record"]
        alpha.try_add_pattern("alpha_code", r"ALPHA-[0-9]{4}")

        assert alpha.search("MemoryMax", limit=3)[0]["memory_id"] == alpha_record.memory_id
        assert beta.search("MemoryMax", limit=3) == []
        assert beta.search("shadow access", limit=3)[0]["memory_id"] == beta_record.memory_id
        assert alpha.search("shadow access", limit=3) == []
        assert alpha.list_patterns() == {"alpha_code": r"ALPHA-[0-9]{4}"}
        assert beta.list_patterns() == {}

        alpha_status = alpha.namespace_status()
        beta_status = beta.namespace_status()
        assert alpha_status["namespace"] == "alpha"
        assert beta_status["namespace"] == "beta"
        assert alpha_status["namespace_home"] != beta_status["namespace_home"]
        assert "namespaces\\alpha" in alpha_status["namespace_home"] or "namespaces/alpha" in alpha_status["namespace_home"]
        assert alpha.audit(limit=1)[0]["detail"].find('"namespace": "alpha"') != -1
        assert beta.audit(limit=1)[0]["detail"].find('"namespace": "beta"') != -1

        names = {item["namespace"] for item in list_namespaces()}
        assert {"default", "alpha", "beta"}.issubset(names)
        assert namespace_status("alpha")["store_exists"] is True
        assert namespace_status("beta")["db_exists"] is True
    finally:
        alpha.close()
        beta.close()


def test_ingest_and_search_preference(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text("以后回答 Linux 命令时直接给命令和风险提示，不要写太长背景。")
        assert result["stored"] is True
        record = result["record"]
        assert record.type == "preference"
        assert record.subtype in {"tool_preference", "output_style"}

        hits = service.search("Linux 命令回答偏好", limit=5)
        assert hits
        assert hits[0]["memory_id"] == record.memory_id
    finally:
        service.close()


def test_sensitive_credential_is_blocked(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text("HF token is hf_abcdefghijklmnopqrstuvwxyz123456", source_uri="/home/u/.env")
        assert result["stored"] is False
        assert result["reason"] == "sensitive_block"
        assert service.list_memories() == []
        audit = service.audit(event_type="sensitive_block")
        assert audit
    finally:
        service.close()


def test_phone_is_redacted_and_stored(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text("我的联系电话是 13800138000，用于测试。")
        assert result["stored"] is True
        record = result["record"]
        assert "[REDACTED:phone_cn]" in record.content
        assert record.sensitivity.redacted is True
    finally:
        service.close()


def test_quality_validation_rejects_empty_and_normalizes_text(tmp_path):
    service = make_service(tmp_path)
    try:
        rejected = service.ingest_text(" \x00 \n\t ")
        assert rejected["stored"] is False
        assert rejected["reason"] == "quality_reject"
        assert rejected["quality"]["issues"] == ["empty_after_normalization"]
        assert service.audit(event_type="quality_reject")

        result = service.ingest_text("  普通知识：   systemd   可用 MemoryMax 限制服务内存。\n\n\n")
        assert result["stored"] is True
        record = result["record"]
        assert "   " not in record.content
        assert "normalized_whitespace_or_control_chars" in result["quality"]["warnings"]
        assert record.confidence <= 1.0
    finally:
        service.close()


def test_contextual_name_address_and_bank_card_are_redacted(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text("联系人姓名是张三，收货地址是北京市海淀区中关村东路1号2号楼305室，银行卡号 6222 0202 0202 0202 020。")
        assert result["stored"] is True
        record = result["record"]
        assert record.sensitivity.redacted is True
        assert "[REDACTED:person_name_cn]" in record.content
        assert "[REDACTED:address_cn]" in record.content
        assert "[REDACTED:bank_card]" in record.content
    finally:
        service.close()


def test_aws_access_key_is_blocked_but_token_bucket_is_not(tmp_path):
    service = make_service(tmp_path)
    try:
        blocked = service.ingest_text("AWS access key AKIAABCDEFGHIJKLMNOP should never enter memory.")
        assert blocked["stored"] is False
        assert "aws_access_key_id" in blocked["sensitivity"].labels
        safe = service.ingest_text("token bucket 算法是一种限流算法，不是访问令牌。")
        assert safe["stored"] is True
        assert safe["record"].sensitivity.level == "none"
    finally:
        service.close()


def test_forget_removes_from_search_and_marks_deleted(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_text("以后关于 HuggingFace token 的回答都要提醒我不要写入长期记忆。")
        record = result["record"]
        dry = service.forget("忘掉 HuggingFace token 的偏好")
        assert dry["candidates"]
        done = service.forget("忘掉 HuggingFace token 的偏好", confirm=True)
        assert record.memory_id in done["forgotten"]

        hits = service.search("HuggingFace token 偏好")
        assert all(hit["memory_id"] != record.memory_id for hit in hits)
        verified = service.verify_forget(record.memory_id)
        assert verified["deleted"] is True
        assert verified["metadata_plaintext_leak"] is False
    finally:
        service.close()


def test_forget_compact_removes_plaintext_from_segment(tmp_path):
    service = make_service(tmp_path)
    secret_phrase = "temporary project codename blue-river"
    try:
        record = service.ingest_text(secret_phrase)["record"]
        done = service.forget("忘掉 temporary project codename", confirm=True)
        assert record.memory_id in done["forgotten"]
        before = service.verify_forget_probe(record.memory_id, secret_phrase)
        assert before["segment_plaintext_leak"] is True
        service.compact()
        after = service.verify_forget_probe(record.memory_id, secret_phrase)
        assert after["irreversible_ok"] is True
        assert after["segment_plaintext_leak"] is False
    finally:
        service.close()


def test_versions_update_and_rollback(tmp_path):
    service = make_service(tmp_path)
    try:
        created = service.ingest_text("以后回答命令时使用中文。")["record"]
        first_version = created.version.version_id
        updated = service.update_memory(created.memory_id, "以后回答命令时使用英文。")
        assert updated["updated"] is True

        versions = service.versions(created.memory_id)
        assert len(versions) == 2
        assert versions[-1]["op"] == "update"

        rolled = service.rollback(created.memory_id, first_version)
        assert rolled["rolled_back"] is True
        latest = service.store.get_latest(created.memory_id)
        assert latest is not None
        assert "中文" in latest.content
        assert latest.version.op == "rollback"
    finally:
        service.close()


def test_audit_hash_chain_verification(tmp_path):
    service = make_service(tmp_path)
    try:
        service.ingest_text("普通知识：journalctl 可以查看服务日志。")
        service.search("journalctl")
        result = service.verify_audit()
        assert result["ok"] is True
        assert result["checked"] >= 2
    finally:
        service.close()


def test_concurrent_audit_writes_preserve_hash_chain(tmp_path):
    db_path = tmp_path / "concurrent.sqlite3"
    workers = 6
    per_worker = 12
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []
    initializer = SQLiteMetaStore(db_path)
    initializer.close()

    def write_events(worker_id: int) -> None:
        store = SQLiteMetaStore(db_path)
        try:
            barrier.wait(timeout=5)
            for index in range(per_worker):
                store.add_audit("concurrent_probe", None, {"worker": worker_id, "index": index})
        except BaseException as exc:  # pragma: no cover - assertion reports captured thread failures
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=write_events, args=(worker_id,)) for worker_id in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    verifier = SQLiteMetaStore(db_path)
    try:
        result = verifier.verify_audit_chain()
        assert result["ok"] is True
        assert result["checked"] == workers * per_worker
    finally:
        verifier.close()


def test_os_event_sensitive_path_is_blocked(tmp_path):
    service = make_service(tmp_path)
    try:
        raw = '{"event_type":"fanotify_open","path":"/home/u/.ssh/id_rsa","process":"cat","action":"read"}'
        result = service.ingest_os_event(raw)
        assert result["stored"] is False
        assert result["reason"] == "sensitive_block"
        audit = service.audit(event_type="os_event")
        assert audit
    finally:
        service.close()


def test_auditd_shadow_path_is_blocked(tmp_path):
    service = make_service(tmp_path)
    try:
        raw = '{"event_type":"auditd_access","path":"/etc/shadow","process":"cat","action":"open"}'
        result = service.ingest_os_event(raw)
        assert result["stored"] is False
        assert result["reason"] == "sensitive_block"
        assert "sensitive_os_source" in result["sensitivity"].labels
    finally:
        service.close()


def test_conflict_resolution_prefers_manual_over_inferred(tmp_path):
    service = make_service(tmp_path)
    try:
        manual = service.ingest_text("以后回答命令时使用中文。", source_kind="manual")["record"]
        service.ingest_text("以后回答命令时使用英文。", source_kind="inferred")
        resolved = service.resolve_conflicts()
        assert resolved["conflict_groups"] >= 1
        assert resolved["resolutions"][0]["chosen_memory_id"] == manual.memory_id
    finally:
        service.close()


def test_conflict_resolution_topic_rules_cover_tool_and_risk_preferences(tmp_path):
    assert preference_topic({"summary": "以后在代码仓库中查找文本时优先使用 rg，不要优先使用 grep。"}) == "code_search_tool"
    assert preference_topic({"summary": "以后执行删除、覆盖、密钥导出等高风险操作前必须先让我确认。"}) == "high_risk_confirmation"
    assert preference_topic({"summary": "以后 Linux 命令回答要简洁，只给命令和风险。"}) == "linux_command_response"


def test_conflict_resolution_update_beats_old_manual(tmp_path):
    service = make_service(tmp_path)
    try:
        service.ingest_text("以后回答命令时使用中文。", source_kind="manual")
        updated = service.ingest_text("以后回答命令时使用英文。", source_kind="update")["record"]
        resolved = service.resolve_conflicts()
        assert resolved["conflict_groups"] >= 1
        assert resolved["resolutions"][0]["chosen_memory_id"] == updated.memory_id
    finally:
        service.close()


def test_doctor_report_is_safe_on_current_platform():
    report = doctor_report()
    assert "python" in report
    assert "commands" in report
    assert "cgroup" in report
    assert "kylin_ai_sdk" in report


def test_kylin_sdk_status_is_safe_on_current_platform():
    status = kylin_sdk_status(probe_embedding=False)
    assert status["embedding"]["backend"] == "kylin_coreai_embedding"
    assert status["vector_store"]["backend"] == "kysdk_vector_engine_client"
    assert "fallback" in status["embedding"]
    assert "fallback" in status["vector_store"]


def test_default_retrieval_backend_is_hash(tmp_path):
    service = make_service(tmp_path)
    try:
        record = service.ingest_text("systemd MemoryMax 可以限制 Linux 服务内存。")["record"]
        hits = service.search("Linux 服务内存限制", limit=3)
        assert hits
        assert hits[0]["memory_id"] == record.memory_id
        assert hits[0]["vector_backend"] == "hash"
        status = service.retrieval_status()
        assert status["requested_backend"] == "hash"
        assert status["hash"]["available"] is True
        assert status["policy"]["default_backend"] == "hash"
    finally:
        service.close()


def test_auto_retrieval_falls_back_when_kylin_embedding_fails(monkeypatch):
    def fake_embed(_text, _timeout_seconds, include_vector=True):
        return {"ok": False, "error": "sdk unavailable in test", "include_vector": include_vector}

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(fake_embed))
    retriever = HybridRetriever(embedding_backend="auto")
    records = [
        {
            "memory_id": "m1",
            "type": "knowledge",
            "summary": "systemd MemoryMax",
            "content": "限制 Linux 服务内存",
        },
        {
            "memory_id": "m2",
            "type": "knowledge",
            "summary": "auditd",
            "content": "审计敏感路径访问",
        },
    ]
    ranked = retriever.rank("Linux 内存限制", records, limit=2)
    assert ranked
    assert all(record["_vector_backend"] == "hash" for _, record in ranked)
    assert retriever.status()["kylin"]["disabled_reason"] == "sdk unavailable in test"


def test_auto_retrieval_can_use_successful_kylin_embedding_probe(monkeypatch):
    def fake_embed(text, _timeout_seconds, include_vector=True):
        lowered = text.lower()
        if "systemd" in lowered or "内存" in text or "memory" in lowered:
            vector = [1.0, 0.0, 0.0]
        elif "audit" in lowered or "审计" in text:
            vector = [0.0, 1.0, 0.0]
        else:
            vector = [0.0, 0.0, 1.0]
        result = {"ok": True, "dim": 3}
        if include_vector:
            result["vector"] = vector
        return result

    monkeypatch.setattr(KylinEmbeddingBackend, "embed_in_subprocess", staticmethod(fake_embed))
    retriever = HybridRetriever(embedding_backend="auto")
    records = [
        {
            "memory_id": "m1",
            "type": "knowledge",
            "summary": "systemd MemoryMax",
            "content": "限制 Linux 服务内存",
        },
        {
            "memory_id": "m2",
            "type": "knowledge",
            "summary": "auditd",
            "content": "审计敏感路径访问",
        },
    ]
    ranked = retriever.rank("systemd memory", records, limit=2)
    assert ranked
    assert ranked[0][1]["memory_id"] == "m1"
    assert all(record["_vector_backend"] == "kylin_sdk" for _, record in ranked)
    assert retriever.status()["last_backend_used"] == "kylin_sdk"


def test_custom_sensitive_pattern(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    service = make_service(tmp_path)
    try:
        added = service.try_add_pattern("lab_account", r"LAB-[0-9]{4}")
        assert added["ok"] is True
        result = service.ingest_text("我的实验室账号是 LAB-1234。")
        assert result["stored"] is True
        assert result["record"].sensitivity.redacted is True
        assert "[REDACTED:lab_account]" in result["record"].content
    finally:
        service.close()


def test_custom_sensitive_pattern_validation_rejects_bad_regex(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    service = make_service(tmp_path)
    try:
        invalid = service.try_add_pattern("bad", "(")
        assert invalid["ok"] is False
        broad = service.try_add_pattern("too_broad", ".*")
        assert broad["ok"] is False
        assert service.list_patterns() == {}
        assert service.audit(event_type="pattern_reject")
    finally:
        service.close()


def test_memory_tier_promote_and_evict(tmp_path):
    service = make_service(tmp_path)
    try:
        working = service.ingest_text("临时工作记忆：只在当前任务中使用。", tier="working")["record"]
        assert working.tier == "working"
        assert service.list_memories(tier="working")[0]["memory_id"] == working.memory_id

        promoted = service.promote_memory(working.memory_id, "long_term")
        assert promoted["promoted"] is True
        assert promoted["record"].tier == "long_term"
        assert service.list_memories(tier="working") == []
        assert service.list_memories(tier="long_term")

        session = service.ingest_text("会话记忆：本轮调试使用。", tier="session")["record"]
        dry = service.evict_tier("session", confirm=False)
        assert dry["candidate_count"] == 1
        done = service.evict_tier("session", confirm=True)
        assert session.memory_id in done["memory_ids"]
        assert service.list_memories(tier="session") == []
    finally:
        service.close()


def test_auditd_rule_generation():
    commands = auditctl_rules(paths=["/home/test/.ssh"], key="memguard_test")
    assert commands == ["auditctl -w /home/test/.ssh -p rwxa -k memguard_test"]
    rules_file = audit_rules_file(paths=["/home/test/.ssh"], key="memguard_test")
    assert "-w /home/test/.ssh -p rwxa -k memguard_test" in rules_file
    assert auditctl_rules(paths=["/definitely/missing/memguard"], key="memguard_test", existing_only=True) == []
    assert ausearch_command("memguard_test") == "ausearch -k memguard_test -i"


def test_auditd_event_parser_groups_syscall_and_path():
    lines = [
        'type=SYSCALL msg=audit(1779091150.387:323): arch=c000003e syscall=257 comm="sudo" exe="/usr/bin/sudo" key="memguard_sensitive" ARCH=x86_64 SYSCALL=openat',
        'type=CWD msg=audit(1779091150.387:323): cwd="/home/luo"',
        'type=PATH msg=audit(1779091150.387:323): item=0 name="/etc/shadow" inode=1',
    ]
    events = parse_auditd_events(lines)
    assert len(events) == 1
    assert events[0].path == "/etc/shadow"
    assert events[0].process == "/usr/bin/sudo"
    assert events[0].action == "open"


def test_structured_tool_result_ingestion(tmp_path):
    service = make_service(tmp_path)
    try:
        result = service.ingest_tool_result(
            ToolResult(
                command="pytest -q",
                cwd="/repo",
                exit_code=0,
                stdout="17 passed in 1.99s",
                stderr="",
                duration_ms=1990,
                generated_files=["docs/test_report.md"],
                tool_name="pytest",
            ),
            source_uri="tool_result_success.json",
        )
        assert result["stored"] is True
        record = result["record"]
        assert record.type == "knowledge"
        assert record.subtype == "tool_trace"
        assert record.tier == "session"
        hits = service.search("pytest passed test_report")
        assert any(hit["memory_id"] == record.memory_id for hit in hits)
    finally:
        service.close()


def test_manual_config_ingestion_builds_preference_policy_and_knowledge(tmp_path):
    config_path = tmp_path / "user_config.json"
    config_path.write_text(
        '{"answer":{"language":"中文"},"tools":{"code_search":"优先使用 rg"},"security":{"memory_policy":"不要保存 token"},"workflow":{"service_check":"systemctl status"}}',
        encoding="utf-8",
    )
    service = make_service(tmp_path)
    try:
        result = service.ingest_config(config_path)
        assert result["entries"] == 4
        assert result["stored_count"] >= 3
        rows = service.list_memories()
        source_kinds = {row["source_kind"] for row in rows}
        assert "manual_config" in source_kinds
        subtypes = {row["subtype"] for row in rows}
        assert "output_style" in subtypes
        assert "tool_preference" in subtypes
        assert "security_policy" in subtypes
    finally:
        service.close()


def test_manual_config_ingestion_reports_quality_skips(tmp_path):
    config_path = tmp_path / "user_config.json"
    config_path.write_text('{"answer":{"language":""},"tools":{"code_search":"rg"}}', encoding="utf-8")
    service = make_service(tmp_path)
    try:
        result = service.ingest_config(config_path)
        assert result["entries"] == 2
        assert result["stored_count"] == 1
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["reason"] == "empty_value"
    finally:
        service.close()


def test_shell_history_ingestion_as_user_behavior(tmp_path):
    service = make_service(tmp_path)
    history = tmp_path / ".bash_history"
    history.write_text(
        "\n".join(
            [
                "ls",
                "rg TODO memguard",
                "pytest -q",
                "sudo rm -rf /tmp/memguard-test",
                "history",
            ]
        ),
        encoding="utf-8",
    )
    try:
        parsed = parse_shell_history(history.read_text(encoding="utf-8").splitlines())
        assert [event.command for event in parsed] == ["rg TODO memguard", "pytest -q", "sudo rm -rf /tmp/memguard-test"]
        result = service.ingest_shell_history(history)
        assert result["stored_count"] == 3
        hits = service.search("pytest shell command", limit=5)
        assert any(hit["subtype"] == "behavior_trace" for hit in hits)
        risky = service.search("sudo rm high risk", limit=5)
        assert any(hit["subtype"] == "behavior_risk" for hit in risky)
    finally:
        service.close()


def test_build_context_returns_redacted_generation_package(tmp_path):
    service = make_service(tmp_path)
    try:
        service.ingest_text("以后回答 Linux 命令时直接给命令和风险提示。")
        service.ingest_text("我的联系电话是 13800138000，用于测试。")
        context = service.build_context("Linux 命令 联系电话", limit=5, max_chars=1200, token_budget=700)
        assert context["memory_count"] >= 1
        assert "generation_policy" in context
        assert context["budget"]["estimated_context_tokens"] > 0
        assert context["budget"]["token_budget"] == 700
        assert context["budget"]["over_token_budget"] is False
        assert "13800138000" not in context["context"]
        assert "[REDACTED:phone_cn]" in context["context"]
        phone_memory = [item for item in context["memories"] if item["sensitivity"]["level"] == "medium"][0]
        assert phone_memory["sensitivity"]["redacted"] is True
        prompt = render_generation_prompt(context)
        assert "OS Agent" in prompt
        assert "13800138000" not in prompt
        assert "[REDACTED:phone_cn]" in prompt
        audit = service.audit(event_type="context_build")
        assert audit
    finally:
        service.close()


def test_optional_llm_adapter_is_explicit_and_safe_by_default():
    dry = generate_answer("hello", provider="dry-run")
    assert dry.generated is False
    assert dry.prompt == "hello"
    assert dry.answer == ""

    command = f'"{sys.executable}" -c "import sys; print(sys.stdin.read().upper())"'
    local = generate_answer("memguard", provider="local-command", command=command)
    assert local.generated is True
    assert local.answer == "MEMGUARD"


def test_entity_relation_extraction():
    entities = extract_entities("openKylin systemd MemoryMax journald auditd 精准遗忘 mmap COW")
    assert "openKylin" in entities
    assert "systemd" in entities
    assert "cgroup" in entities
    assert "journald" in entities
    assert "auditd" in entities
    assert "precise_forgetting" in entities
    relations = extract_relations(entities)
    assert relations


def test_graph_search_and_related_memories(tmp_path):
    service = make_service(tmp_path)
    try:
        first = service.ingest_text("systemd MemoryMax 可以限制 Linux 服务内存。")["record"]
        second = service.ingest_text("openKylin 使用 systemctl status 查看 systemd 服务。")["record"]
        hits = service.graph_search("systemd")
        ids = {hit["memory_id"] for hit in hits}
        assert first.memory_id in ids
        assert second.memory_id in ids
        related = service.related(first.memory_id)
        related_ids = {item["memory_id"] for item in related["related"]}
        assert second.memory_id in related_ids
        assert "systemd" in related["graph"]["entities"]
    finally:
        service.close()


def test_eval_latency_load_smoke():
    result = run_eval(latency_size=20)
    assert result["latency_load"]["memory_count"] == 20
    assert result["latency_load"]["query_count"] >= 100
    assert result["latency_load"]["p95_latency_ms"] < 500
    assert "baseline_comparison" in result
    assert "text_only_recall_at_5" in result["baseline_comparison"]
    assert "full_memguardos_recall_at_5" in result["baseline_comparison"]
    assert result["resource_usage"]["eval_cpu_time_ms"] >= 0
    assert "label_f1" in result["sensitive_detection"]


def test_demo_smoke_script_runs():
    assert demo_smoke_main() == 0


def test_daemon_health_and_once_mode():
    snapshot = daemon_health_snapshot()
    assert snapshot["status"] == "ready"
    assert snapshot["namespace"] == "default"
    assert "cgroup_v2" in snapshot
    assert run_daemon(once=True) == 0


def test_local_api_handler_and_unix_socket(tmp_path):
    service = make_service(tmp_path)
    try:
        ingest = handle_request(service, {"command": "ingest", "text": "以后回答 Linux 命令时直接给命令和风险提示。"})
        assert ingest["ok"] is True
        assert ingest["result"]["scope"] == "default"
        assert ingest["result"]["namespace"] == "default"
        denied = handle_request(service, {"command": "health"}, auth_token="secret")
        assert denied["ok"] is False
        allowed = handle_request(service, {"command": "health", "token": "secret"}, auth_token="secret")
        assert allowed["ok"] is True
        assert allowed["result"]["namespace"] == "default"
        assert allowed["result"]["store_path"] == str(tmp_path / "memory.segment")
        assert allowed["result"]["db_path"] == str(tmp_path / "memguard.sqlite3")
        namespace = handle_request(service, {"command": "namespace_status"})
        assert namespace["ok"] is True
        assert namespace["result"]["namespace"] == "default"
        assert namespace["result"]["uses_custom_paths"] is True
        assert namespace["result"]["store_path"] == str(tmp_path / "memory.segment")
        assert namespace["result"]["db_path"] == str(tmp_path / "memguard.sqlite3")
        context = handle_request(service, {"command": "build_context", "query": "Linux 命令", "limit": 3})
        assert context["ok"] is True
        assert context["result"]["memory_count"] >= 1
        if not hasattr(socket, "AF_UNIX"):
            return

        stop = {"value": False}
        socket_path = tmp_path / "memguard.sock"
        thread = threading.Thread(target=serve_unix_socket, args=(socket_path, service, lambda: stop["value"]), daemon=True)
        thread.start()
        for _ in range(50):
            if socket_path.exists():
                break
            time.sleep(0.02)
        assert socket_path.exists()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps({"command": "health"}).encode("utf-8") + b"\n")
            response = client.recv(4096).decode("utf-8")
        stop["value"] = True
        thread.join(timeout=2.0)
        assert json.loads(response)["ok"] is True
    finally:
        service.close()
