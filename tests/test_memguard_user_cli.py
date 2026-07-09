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
