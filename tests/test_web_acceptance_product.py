import json

import pytest

from memguard.os_guard import openkylin_native
from memguard.service import MemGuardService
from memguard.web.acceptance import (
    ACCEPTANCE_ITEMS,
    acceptance_plan,
    acceptance_report,
    report_to_markdown,
    run_acceptance_item,
)


_STATUS_OK = {
    "passed": True,
    "failed": False,
    "warning": True,
    "skipped": True,
    "not_run": False,
    "manual": True,
}


def _complete_results(**status_overrides):
    results = []
    for item in ACCEPTANCE_ITEMS:
        status = status_overrides.get(item.id, "passed")
        results.append(
            {
                "id": item.id,
                "status": status,
                "ok": _STATUS_OK[status],
                "summary": f"{item.id} result",
                "details": {},
            }
        )
    return results


def test_acceptance_plan_uses_delivery_language_without_contest_or_raw_field_copy():
    plan = acceptance_plan()
    visible_copy = " ".join(
        str(item.get(key, ""))
        for item in plan["items"]
        for key in ("group", "title", "purpose", "operation", "pass_standard")
    )

    assert "交付就绪检查" in visible_copy
    assert "赛题" not in visible_copy
    for raw_term in ("stored=true", "memory_id", "active/running", "memories", "explainability"):
        assert raw_term not in visible_copy


def test_acceptance_report_uses_delivery_readiness_name():
    first_item = ACCEPTANCE_ITEMS[0]

    report = acceptance_report([{"id": first_item.id, "status": "passed", "ok": True, "details": {}}])

    assert report["summary"]["passed"] == 1
    assert report["summary"]["not_run"] == sum(item.mode == "automatic" for item in ACCEPTANCE_ITEMS) - 1
    assert report["summary"]["manual"] == sum(item.mode == "manual" for item in ACCEPTANCE_ITEMS)
    assert report["summary"]["ready_for_delivery"] is False
    assert "ready_for_final_demo" not in report["summary"]


def test_acceptance_report_normalizes_empty_input_to_the_complete_acceptance_list():
    report = acceptance_report([])
    statuses = {item["id"]: item["status"] for item in report["results"]}

    assert report["summary"] == {
        "total": len(ACCEPTANCE_ITEMS),
        "passed": 0,
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "not_run": sum(item.mode == "automatic" for item in ACCEPTANCE_ITEMS),
        "manual": sum(item.mode == "manual" for item in ACCEPTANCE_ITEMS),
        "ready_for_delivery": False,
    }
    assert [item["id"] for item in report["results"]] == [item.id for item in ACCEPTANCE_ITEMS]
    assert all(statuses[item.id] == ("manual" if item.mode == "manual" else "not_run") for item in ACCEPTANCE_ITEMS)


def test_acceptance_report_is_ready_only_when_every_acceptance_item_passes():
    report = acceptance_report(_complete_results())

    assert report["schema"] == "memguardos-acceptance-report-v2"
    assert report["summary"] == {
        "total": len(ACCEPTANCE_ITEMS),
        "passed": len(ACCEPTANCE_ITEMS),
        "failed": 0,
        "warning": 0,
        "skipped": 0,
        "not_run": 0,
        "manual": 0,
        "ready_for_delivery": True,
    }


@pytest.mark.parametrize(
    ("item_id", "status"),
    [
        (ACCEPTANCE_ITEMS[0].id, "failed"),
        (ACCEPTANCE_ITEMS[1].id, "not_run"),
        (ACCEPTANCE_ITEMS[0].id, "warning"),
        (ACCEPTANCE_ITEMS[1].id, "skipped"),
        (next(item.id for item in ACCEPTANCE_ITEMS if item.mode == "manual"), "manual"),
    ],
)
def test_acceptance_report_blocks_delivery_for_non_passing_statuses(item_id, status):
    report = acceptance_report(_complete_results(**{item_id: status}))

    assert report["summary"]["passed"] == len(ACCEPTANCE_ITEMS) - 1
    assert report["summary"][status] == 1
    assert report["summary"]["ready_for_delivery"] is False


@pytest.mark.parametrize("results", [None, {}, "results", 1])
def test_acceptance_report_rejects_non_list_top_level_input_with_value_error(results):
    with pytest.raises(ValueError, match="results must be a list"):
        acceptance_report(results)


@pytest.mark.parametrize("result", [None, [], "result", 1])
def test_acceptance_report_rejects_non_dict_elements_with_value_error(result):
    with pytest.raises(ValueError, match="result must be an object"):
        acceptance_report([result])


@pytest.mark.parametrize(
    "result",
    [
        {"status": "passed"},
        {"id": "", "status": "passed"},
        {"id": 1, "status": "passed"},
        {"id": "UNKNOWN_ITEM", "status": "passed"},
    ],
)
def test_acceptance_report_rejects_missing_invalid_and_unknown_ids(result):
    with pytest.raises(ValueError, match="acceptance item"):
        acceptance_report([result])


def test_acceptance_report_rejects_duplicate_results():
    known_id = ACCEPTANCE_ITEMS[0].id

    with pytest.raises(ValueError, match="duplicate acceptance item"):
        acceptance_report(
            [
                {"id": known_id, "status": "passed", "ok": True, "details": {}},
                {"id": known_id, "status": "failed", "ok": False, "details": {}},
            ]
        )


@pytest.mark.parametrize("status", [None, "", 1, "unexpected"])
def test_acceptance_report_rejects_missing_non_string_and_unknown_statuses(status):
    with pytest.raises(ValueError, match="acceptance status"):
        acceptance_report([{"id": ACCEPTANCE_ITEMS[0].id, "status": status}])


@pytest.mark.parametrize("ok", [None, 0, 1, "true", {}])
def test_acceptance_report_rejects_non_boolean_ok_values(ok):
    with pytest.raises(ValueError, match="acceptance ok"):
        acceptance_report(
            [
                {
                    "id": ACCEPTANCE_ITEMS[0].id,
                    "status": "passed",
                    "ok": ok,
                    "details": {},
                }
            ]
        )


def test_acceptance_report_requires_ok_for_each_provided_result():
    with pytest.raises(ValueError, match="acceptance ok is required"):
        acceptance_report(
            [
                {
                    "id": ACCEPTANCE_ITEMS[0].id,
                    "status": "passed",
                    "details": {},
                }
            ]
        )


@pytest.mark.parametrize(
    ("status", "expected_ok"),
    [
        ("passed", True),
        ("failed", False),
        ("warning", True),
        ("skipped", True),
        ("not_run", False),
        ("manual", True),
    ],
)
def test_acceptance_report_enforces_status_ok_consistency(status, expected_ok):
    item_id = ACCEPTANCE_ITEMS[0].id

    report = acceptance_report([{"id": item_id, "status": status, "ok": expected_ok, "details": {}}])

    normalized = next(item for item in report["results"] if item["id"] == item_id)
    assert normalized["ok"] is expected_ok
    with pytest.raises(ValueError, match="acceptance ok"):
        acceptance_report([{"id": item_id, "status": status, "ok": not expected_ok, "details": {}}])


@pytest.mark.parametrize("details", [None, [], "details", 1])
def test_acceptance_report_rejects_non_dict_details(details):
    with pytest.raises(ValueError, match="acceptance details"):
        acceptance_report(
            [
                {
                    "id": ACCEPTANCE_ITEMS[0].id,
                    "status": "passed",
                    "ok": True,
                    "details": details,
                }
            ]
        )


def test_acceptance_report_requires_details_for_each_provided_result():
    with pytest.raises(ValueError, match="acceptance details are required"):
        acceptance_report(
            [
                {
                    "id": ACCEPTANCE_ITEMS[0].id,
                    "status": "passed",
                    "ok": True,
                }
            ]
        )


def test_acceptance_report_markdown_displays_counts_and_normalized_detail_rows():
    first_item = ACCEPTANCE_ITEMS[0]
    second_id = ACCEPTANCE_ITEMS[1].id
    results = _complete_results(**{first_item.id: "not_run", second_id: "skipped"})
    results[0]["summary"] = "尚未运行 | 需复核\n稍后执行"
    report = acceptance_report(results)

    markdown = report_to_markdown(report)

    assert "| 跳过 | 1 |" in markdown
    assert "| 未运行 | 1 |" in markdown
    assert (
        f"| {first_item.id} | {first_item.group} | {first_item.title} | not_run | "
        "尚未运行 \\| 需复核 稍后执行 |"
    ) in markdown


def test_openkylin_acceptance_classifies_real_status_instead_of_passing_import_errors(tmp_path, monkeypatch):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")
    try:
        monkeypatch.setattr(
            openkylin_native,
            "openkylin_native_status",
            lambda **_: {
                "system": {"is_openkylin_like": True},
                "summary": {"overall": "red", "red": 2, "yellow": 0, "green": 5},
                "recommendations": ["安装缺失的桌面组件。"],
            },
        )

        result = run_acceptance_item(service, "A06_OPENKYLIN_STATUS")

        assert result["status"] == "failed"
        assert result["ok"] is False
        assert result["details"]["summary"]["overall"] == "red"
    finally:
        service.close()


def test_openkylin_acceptance_marks_non_openkylin_hosts_as_not_applicable_warning(tmp_path, monkeypatch):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")
    try:
        monkeypatch.setattr(
            openkylin_native,
            "openkylin_native_status",
            lambda **_: {
                "system": {"is_openkylin_like": False},
                "summary": {"overall": "red", "red": 7, "yellow": 0, "green": 0},
                "recommendations": [],
            },
        )

        result = run_acceptance_item(service, "A06_OPENKYLIN_STATUS")

        assert result["status"] == "warning"
        assert result["ok"] is True
        assert "非 openKylin" in result["summary"]
    finally:
        service.close()


def test_openkylin_acceptance_reports_probe_exceptions_as_failed_results(tmp_path, monkeypatch):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")

    def raise_probe_error(**_):
        raise RuntimeError("probe failed")

    try:
        monkeypatch.setattr(openkylin_native, "openkylin_native_status", raise_probe_error)

        result = run_acceptance_item(service, "A06_OPENKYLIN_STATUS")

        assert result["status"] == "failed"
        assert result["ok"] is False
        assert result["details"] == {"error": "RuntimeError", "message": "probe failed"}
    finally:
        service.close()


def test_acceptance_ingest_result_is_json_serializable_for_web_transport(tmp_path):
    service = MemGuardService(store_path=tmp_path / "memory.segment", db_path=tmp_path / "memory.sqlite3")
    try:
        result = run_acceptance_item(service, "A13_INGEST_PREF")

        encoded = json.dumps(result, ensure_ascii=False)

        assert '"status": "passed"' in encoded
        assert isinstance(result["details"]["record"], dict)
    finally:
        service.close()
