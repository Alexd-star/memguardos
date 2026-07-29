from memguard.cli import memguard


def test_user_cli_parser_exposes_product_level_shortcuts():
    parser = memguard.build_parser()

    actions = {
        action.dest: action
        for action in parser._actions
        if getattr(action, "dest", None) == "command"
    }
    choices = set(actions["command"].choices)

    assert {"status", "memory", "search", "context", "forget", "evidence", "demo", "shell"} <= choices


def test_user_cli_demo_prints_memory_effect_summary(tmp_path, capsys):
    code = memguard.main(
        [
            "--store",
            str(tmp_path / "memory.segment"),
            "--db",
            str(tmp_path / "memguard.sqlite3"),
            "demo",
            "--task",
            "查找精准遗忘的实现位置",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "记忆注入" in out
    assert "工具调用" in out
    assert "新增记忆" in out
    assert "遗忘验证" in out
    assert "code_search_tool" in out or "rg" in out


def test_user_cli_forget_preview_does_not_delete_without_confirm(tmp_path, capsys):
    common = [
        "--store",
        str(tmp_path / "memory.segment"),
        "--db",
        str(tmp_path / "memguard.sqlite3"),
    ]

    assert memguard.main([*common, "remember", "临时项目代号 blue-river 只用于预览遗忘"]) == 0
    assert memguard.main([*common, "forget", "忘掉 blue-river"]) == 0

    out = capsys.readouterr().out
    assert "影响范围预览" in out
    assert "确认执行请追加 --yes" in out
    assert "已执行遗忘" not in out


def test_user_cli_interactive_shell_handles_product_commands(monkeypatch, tmp_path, capsys):
    commands = iter(["/help", "/status", "/preferences", "/workspace", "/model", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    code = memguard.main(
        [
            "--store",
            str(tmp_path / "memory.segment"),
            "--db",
            str(tmp_path / "memguard.sqlite3"),
            "shell",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "/preferences" in out
    assert "本机状态" in out
    assert "当前工作区" in out
    assert "当前模型" in out


def test_interactive_shell_routes_natural_forgetting_and_accepts_yes(monkeypatch, tmp_path, capsys):
    commands = iter([
        "记住：临时项目代号 blue-river 只用于交互遗忘",
        "忘掉 blue-river",
        "yes",
        "/exit",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    code = memguard.main(
        [
            "--store",
            str(tmp_path / "memory.segment"),
            "--db",
            str(tmp_path / "memguard.sqlite3"),
            "shell",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "已识别为精准遗忘请求" in out
    assert "候选正确时直接输入 yes 或 确认" in out
    assert "已遗忘 1 条目标记忆" in out


def test_interactive_shell_intercepts_forgetting_mistakenly_prefixed_with_run(monkeypatch, tmp_path, capsys):
    common = [
        "--store",
        str(tmp_path / "memory.segment"),
        "--db",
        str(tmp_path / "memguard.sqlite3"),
    ]
    assert memguard.main([*common, "remember", "以后搜索代码时优先使用 grep"]) == 0
    capsys.readouterr()
    commands = iter(["/run 忘掉 grep 的习惯", "取消", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert memguard.main([*common, "shell"]) == 0

    out = capsys.readouterr().out
    assert "已识别为记忆管理请求" in out
    assert "影响范围预览" in out
    assert "已取消本次遗忘" in out
    assert "真实 Agent 运行" not in out


def test_interactive_shell_does_not_store_yes_without_pending_operation(monkeypatch, tmp_path, capsys):
    commands = iter(["yes", "/memory", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert memguard.main(
        [
            "--store",
            str(tmp_path / "memory.segment"),
            "--db",
            str(tmp_path / "memguard.sqlite3"),
            "shell",
        ]
    ) == 0

    out = capsys.readouterr().out
    assert "当前没有等待确认的遗忘操作" in out
    assert "暂无可显示的记忆" in out


def test_natural_shell_intent_routes_common_requests_without_silent_writes():
    assert memguard._natural_shell_intent("查看记忆") == ("memory_list", "")
    assert memguard._natural_shell_intent("我的偏好有哪些") == ("preference_list", "")
    assert memguard._natural_shell_intent("搜索关于蓝河的记忆") == ("search", "蓝河")
    assert memguard._natural_shell_intent("运行任务：查找服务定义") == ("run", "查找服务定义")
    assert memguard._natural_shell_intent("记住：项目代号是蓝河")[0] == "remember"
    assert memguard._natural_shell_intent("项目代号是蓝河")[0] == ""


def test_natural_remember_creates_actionable_structured_preferences(tmp_path, capsys):
    service = memguard.MemGuardService(
        store_path=tmp_path / "memory.segment",
        db_path=tmp_path / "memory.sqlite3",
    )
    try:
        code = memguard._remember(
            service,
            "记住：以后搜索项目代码优先使用 rg，并且先给可复制命令，再解释结果。",
        )
        rows = service.list_memories(governance_state="active")
    finally:
        service.close()

    out = capsys.readouterr().out
    assert code == 0
    assert {row["preference_topic"] for row in rows} == {"code_search_tool", "response_order"}
    assert "工具偏好" in out
    assert "回答偏好" in out
    assert "检测到并保存 2 项" in out


def test_ambiguous_plain_text_is_not_silently_saved_in_shell(monkeypatch, tmp_path, capsys):
    commands = iter(["项目代号是蓝河", "/memory", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert memguard.main(
        [
            "--store",
            str(tmp_path / "memory.segment"),
            "--db",
            str(tmp_path / "memory.sqlite3"),
            "shell",
        ]
    ) == 0

    out = capsys.readouterr().out
    assert "请确认它是 Agent 任务还是要保存的记忆" in out
    assert "暂无可显示的记忆" in out
