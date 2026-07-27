"""Retention and exception clauses must mean what the user said.

Two failure modes are pinned here because both silently do the opposite of the
request: a retention clause that protects everything (turning the request into a
no-op) and an exception clause that becomes the delete target.
"""

from memguard.forgetting.natural_intent import ErasureIntentCompiler
from memguard.service import MemGuardService


def test_retention_clause_protects_only_the_named_dimension(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        preference = service.ingest_text(
            "用户偏好：X 项目里回答一律先给命令再解释。", source_kind="manual"
        )["record"]
        knowledge = service.ingest_text(
            "知识：X 项目的部署拓扑是双节点主备，切换需要手动确认。", source_kind="manual"
        )["record"]

        plan = service.forget("忘记 X 项目的偏好，但保留 X 项目的知识", confirm=False)
        hit = {item["memory_id"] for item in plan["candidates"]}

        assert preference.memory_id in hit, "用户明确要求删除的偏好不能被保留子句挡住"
        assert knowledge.memory_id not in hit, "用户明确要求保留的知识不能被删除"
    finally:
        service.close()


def test_exception_clause_is_protected_not_targeted():
    compiler = ErasureIntentCompiler()

    intent = compiler.compile("除了安全策略以外，忘掉所有部署相关记忆")

    target = str(intent.get("target_phrase") or "")
    assert "部署" in target, "打击目标应当是部署相关记忆"
    assert "安全策略" not in target, "被排除的对象不能出现在打击目标里"
    constraints = intent.get("retention_constraints") or []
    assert constraints, "例外子句必须转成保留约束"
    assert any(
        "安全策略" in " ".join(str(term) for term in (item.get("terms") or []))
        for item in constraints
        if isinstance(item, dict)
    )


def test_exception_clause_keeps_the_excluded_memory(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        policy = service.ingest_text(
            "安全策略：部署前执行高风险命令必须先让我确认。", source_kind="manual"
        )["record"]
        deploy = service.ingest_text(
            "知识：部署流程需要先同步配置再滚动重启服务节点。", source_kind="manual"
        )["record"]

        plan = service.forget("除了安全策略以外，忘掉所有部署相关记忆", confirm=False)
        hit = {item["memory_id"] for item in plan["candidates"]}

        assert policy.memory_id not in hit, "被明确排除的安全策略不能被删除"
        assert deploy.memory_id in hit, "部署知识才是打击目标，不能用空候选掩盖解析失败"
    finally:
        service.close()
