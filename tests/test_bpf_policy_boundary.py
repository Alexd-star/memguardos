from pathlib import Path
import json

from scripts.generate_bpf_lsm_policy import render_header


def test_bpf_json_denylist_demo_is_path_boundary_not_content_dlp():
    policy_path = Path("native/bpf_lsm/memguard_lsm_policy.example.json")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    assert policy["schema"] == "memguardos-bpf-path-denylist-v1"
    assert policy["boundary"] == "path_substring_denylist"
    assert "content_dlp" not in policy["claims"]
    assert "/tmp/memguardos-bpf-deny" in policy["deny_substrings"]

    header = render_header(policy["deny_substrings"], policy_path.name)
    assert 'contains(path, "/tmp/memguardos-bpf-deny")' in header
