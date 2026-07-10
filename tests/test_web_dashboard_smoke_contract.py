from scripts.web_dashboard_smoke import _agent_replay_ok


def test_agent_replay_smoke_result_is_strict_boolean():
    result = _agent_replay_ok(
        {
            "ok": True,
            "result": {
                "execution_mode": "replay",
                "truthful_note": "使用已记录的真实执行轨迹进行复核。",
            },
        }
    )

    assert result is True
