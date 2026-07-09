#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "benchmarks" / "agent_interactions.jsonl"


def event(content, session_id, role="user", event_type="user_message", **extra):
    return {
        "role": role,
        "event_type": event_type,
        "content": content,
        "session_id": session_id,
        **extra,
    }


def annotated(case):
    case.setdefault("annotator_a", case.get("expected_topics", []))
    case.setdefault("annotator_b", case.get("expected_topics", []))
    case.setdefault("adjudication", "一致")
    case.setdefault("language", "zh-CN")
    return case


def build_cases():
    cases = []
    explicit_templates = [
        ("以后搜索代码优先使用 rg。", "code_search_tool"),
        ("回答时先给命令，再解释原因。", "response_order"),
        ("后续请始终使用中文回答。", "response_language"),
        ("回答尽量简洁，只保留关键步骤。", "response_verbosity"),
        ("删除文件前必须先询问我。", "high_risk_confirmation"),
    ]
    for index in range(40):
        text, topic = explicit_templates[index % len(explicit_templates)]
        cases.append(
            annotated(
                {
                    "case_id": "explicit-%03d" % (index + 1),
                    "category": "explicit_preference",
                    "source_origin": "synthetic" if index < 30 else "real_anonymized",
                    "events": [event(text, "explicit-session-%03d" % index)],
                    "expected_topics": [topic],
                }
            )
        )

    pollution_roles = [("assistant", "assistant_message"), ("tool", "tool_result")]
    for index in range(30):
        role, event_type = pollution_roles[index % 2]
        cases.append(
            annotated(
                {
                    "case_id": "pollution-%03d" % (index + 1),
                    "category": "role_pollution",
                    "source_origin": "synthetic",
                    "events": [
                        event(
                            "以后优先使用 rg 并先给命令。",
                            "pollution-session-%03d" % index,
                            role=role,
                            event_type=event_type,
                            tool_name="terminal" if role == "tool" else "",
                            tool_result="prefer rg" if role == "tool" else "",
                        )
                    ],
                    "expected_topics": [],
                }
            )
        )

    for index in range(20):
        sessions = ["habit-%03d-a" % index, "habit-%03d-b" % index, "habit-%03d-b" % index]
        cases.append(
            annotated(
                {
                    "case_id": "habit-%03d" % (index + 1),
                    "category": "implicit_habit",
                    "source_origin": "synthetic",
                    "events": [
                        event("请用 rg 搜索这一处定义。", sessions[0]),
                        event("这次继续用 rg 查找调用位置。", sessions[1]),
                        event("再用 rg 检查相关测试。", sessions[2]),
                    ],
                    "expected_topics": ["code_search_tool"],
                }
            )
        )

    for index in range(20):
        cases.append(
            annotated(
                {
                    "case_id": "correction-%03d" % (index + 1),
                    "category": "correction_exception",
                    "source_origin": "synthetic",
                    "events": [
                        event("以后搜索代码优先使用 rg。", "correction-%03d-a" % index),
                        event("这次临时用 grep，下次仍然使用 rg。", "correction-%03d-b" % index),
                    ],
                    "expected_topics": ["code_search_tool"],
                }
            )
        )

    for index in range(30):
        session = "tool-trace-%03d" % index
        cases.append(
            annotated(
                {
                    "case_id": "tool-trace-%03d" % (index + 1),
                    "category": "tool_trace",
                    "source_origin": "real_anonymized" if index < 20 else "synthetic",
                    "events": [
                        event("请定位服务入口。", session),
                        event(
                            "",
                            session,
                            role="assistant",
                            event_type="tool_call",
                            tool_name="terminal",
                            tool_args={"command": "rg -n 'class MemGuardService' memguard"},
                        ),
                        event(
                            "",
                            session,
                            role="tool",
                            event_type="tool_result",
                            tool_name="terminal",
                            tool_result="memguard/service.py:45:class MemGuardService",
                            exit_code=0,
                        ),
                    ],
                    "expected_topics": [],
                }
            )
        )

    for index in range(20):
        secret = "sk-benchmark-secret-%03d-abcdefghijklmnop" % index
        cases.append(
            annotated(
                {
                    "case_id": "sensitive-%03d" % (index + 1),
                    "category": "sensitive_interaction",
                    "source_origin": "real_anonymized",
                    "events": [event("我的临时 API key 是 %s" % secret, "sensitive-%03d" % index)],
                    "expected_topics": [],
                    "secret_probe": secret,
                }
            )
        )

    for index in range(20):
        cases.append(
            annotated(
                {
                    "case_id": "forget-%03d" % (index + 1),
                    "category": "precise_forgetting",
                    "source_origin": "synthetic",
                    "events": [
                        event("以后回答先给命令，再解释原因。", "forget-%03d" % index),
                        event("以后搜索代码优先使用 rg。", "forget-tool-%03d" % index),
                    ],
                    "expected_topics": ["code_search_tool", "response_order"],
                    "forget_filters": {
                        "agent_name": "openhands",
                        "preference_topic": "response_order",
                    },
                }
            )
        )

    for index in range(20):
        cases.append(
            annotated(
                {
                    "case_id": "cross-%03d" % (index + 1),
                    "category": "cross_session",
                    "source_origin": "real_anonymized" if index < 10 else "synthetic",
                    "events": [
                        event("以后回答先给命令，再解释原因。", "cross-source-%03d" % index)
                    ],
                    "expected_topics": ["response_order"],
                    "context_stage": "response_generation",
                    "context_task": "说明如何执行测试命令",
                }
            )
        )
    return cases


def main() -> int:
    cases = build_cases()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "case_count": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
