import json

import pytest

from memguard.api.server import handle_request
from memguard.cli.memctl import main as memctl_main
from memguard.service import MemGuardService


@pytest.fixture
def scoped_memories(tmp_path):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    owner = str((tmp_path / "owner").resolve())
    foreign = str((tmp_path / "foreign").resolve())

    def add(text, project="", session=""):
        return service.ingest_text(
            text, project_root=project, tier="session" if session else "long_term",
            retention_session_id=session,
        )["record"].memory_id

    ids = {
        "seed": add("systemd 服务通过 journalctl 检查启动日志。", owner),
        "related": add("systemd 服务启动后需要核对资源配额。", owner),
        "global": add("systemd 是 Linux 系统的服务管理器。"),
        "temporary": add("systemd 本轮调试需要检查白鹭日志。", owner, "owner-session"),
        "other_session": add("systemd 另一个会话需要检查蓝河日志。", owner, "foreign-session"),
    }
    # Newer foreign records would consume the result budget without SQL scoping.
    ids["foreign"] = [
        add("systemd 外项目服务配置记录编号 %s。" % index, foreign)
        for index in range(6)
    ]
    try:
        yield service, owner, foreign, ids
    finally:
        service.close()


def test_graph_and_related_filter_scope_before_limit(scoped_memories):
    service, owner, foreign, ids = scoped_memories
    expected = {ids["seed"], ids["related"], ids["global"]}
    assert {row["memory_id"] for row in service.graph_search("systemd", project_root=owner)} == expected
    layers = service.hierarchical_graph_search("systemd", project_root=owner)["layers"]
    assert {row["memory_id"] for row in layers["summary"]} == expected
    assert {row["memory_id"] for row in layers["chunk"]} == expected
    assert {row["memory_id"] for row in service.related(
        ids["seed"], limit=2, project_root=owner,
    )["related"]} == {ids["related"], ids["global"]}
    assert service.related(ids["seed"], project_root=foreign)["graph"]["entities"] == []
    assert service.meta.related_memories(ids["seed"], project_root=foreign) == []
    assert service.related(ids["temporary"], project_root=owner)["related"] == []
    assert service.meta.related_memories(ids["temporary"], project_root=owner) == []


def test_context_and_parallel_search_keep_project_and_session(scoped_memories):
    service, owner, _foreign, ids = scoped_memories
    expected = {ids["seed"], ids["related"], ids["global"], ids["temporary"]}
    context = service.build_context(
        "systemd", limit=10, max_chars=6000,
        project_root=owner, session_id="owner-session",
    )
    assert {row["memory_id"] for row in context["memories"]} == expected
    assert context["project_root"] == owner
    batch = service.batch_search(
        ["systemd", "systemd 服务"], limit=10, workers=2,
        project_root=owner, session_id="owner-session",
    )
    assert batch["ok"] and batch["worker_service_count"] == 2
    for result in batch["results"]:
        assert {row["memory_id"] for row in result["hits"]} == expected
    assert service.verify_audit()["ok"]


@pytest.mark.parametrize("command", ["search", "graph_search", "hierarchical_graph_search", "build_context", "batch_search", "related"])
def test_api_scopes_all_retrieval_paths(scoped_memories, command):
    service, owner, _foreign, ids = scoped_memories
    request = {
        "command": command, "query": "systemd", "entity": "systemd",
        "queries": ["systemd"], "memory_id": ids["seed"],
        "limit": 10, "max_chars": 6000,
        "project_root": owner, "session_id": "owner-session",
    }
    response = handle_request(service, request)
    assert response["ok"] is True
    result = response["result"]
    rows = {
        "search": lambda: result,
        "graph_search": lambda: result,
        "hierarchical_graph_search": lambda: result["layers"]["summary"],
        "build_context": lambda: result["memories"],
        "batch_search": lambda: result["results"][0]["hits"],
        "related": lambda: result["related"],
    }[command]()
    expected = {ids["seed"], ids["related"], ids["global"], ids["temporary"]}
    if command == "related":
        expected.remove(ids["seed"])
    assert {row["memory_id"] for row in rows} == expected


def test_api_explicit_global_scope_and_invalid_scope(scoped_memories):
    service, _owner, _foreign, ids = scoped_memories
    response = handle_request(service, {"command": "graph_search", "entity": "systemd", "project_root": ""})
    assert {row["memory_id"] for row in response["result"]} == {ids["global"]}
    for value in (None, [], {}, "x" * 2049):
        response = handle_request(service, {"command": "search", "query": "systemd", "project_root": value})
        assert response["ok"] is False and response["error"] == "invalid_request"


@pytest.mark.parametrize("natural", [False, True])
def test_api_write_and_read_share_normalized_project(tmp_path, natural):
    service = MemGuardService(tmp_path / "memory.segment", tmp_path / "meta.sqlite3")
    try:
        project = str(tmp_path / "owner" / ".." / "owner")
        result = handle_request(service, {
            "command": "ingest", "text": "蓝河项目构建流程使用增量缓存。",
            "project_root": project, "natural": natural,
        })
        assert result["ok"] and result["result"]["stored"]
        memory_id = result["result"]["memory_id"]
        assert service.meta.get_memory(memory_id)["project_root"] == str((tmp_path / "owner").resolve())
        own = handle_request(service, {"command": "search", "query": "蓝河项目构建流程", "project_root": project})
        assert memory_id in {row["memory_id"] for row in own["result"]}
        assert handle_request(service, {"command": "search", "query": "蓝河项目构建流程", "project_root": ""})["result"] == []
    finally:
        service.close()


def test_cli_project_scoped_write_batch_context_and_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEMGUARD_HOME", str(tmp_path / "home"))
    project = str(tmp_path / "project")
    assert memctl_main(["ingest", "--text", "systemd 当前会话只检查青山日志。", "--project-root", project, "--tier", "session", "--session-id", "cli-session"]) == 0
    memory_id = json.loads(capsys.readouterr().out)["memory_id"]
    for command, rows in (
        (["search", "systemd"], lambda result: result),
        (["batch-search", "systemd", "--workers", "1"], lambda result: result["results"][0]["hits"]),
        (["build-context", "systemd"], lambda result: result["memories"]),
        (["graph-search", "systemd"], lambda result: result),
        (["hierarchical-graph-search", "systemd"], lambda result: result["layers"]["summary"]),
    ):
        assert memctl_main(command + ["--project-root", project, "--session-id", "cli-session"]) == 0
        assert memory_id in {row["memory_id"] for row in rows(json.loads(capsys.readouterr().out))}
        assert memctl_main(command + ["--project-root", str(tmp_path / "foreign"), "--session-id", "cli-session"]) == 0
        assert rows(json.loads(capsys.readouterr().out)) == []
