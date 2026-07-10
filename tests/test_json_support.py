import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from memguard.json_support import jsonable


def test_jsonable_sorts_sets_deterministically():
    assert jsonable({"zulu", "alpha", "middle"}) == ["alpha", "middle", "zulu"]
    assert jsonable({("beta", 2), ("alpha", 3), ("alpha", 1)}) == [
        ["alpha", 1],
        ["alpha", 3],
        ["beta", 2],
    ]


def test_jsonable_sorts_frozensets_deterministically():
    assert jsonable(frozenset({"zulu", "alpha", "middle"})) == [
        "alpha",
        "middle",
        "zulu",
    ]


def test_jsonable_converts_non_finite_floats_to_none():
    payload = jsonable(
        {
            "finite": 1.25,
            "nan": math.nan,
            "positive_infinity": math.inf,
            "negative_infinity": -math.inf,
            "nested": [math.nan, {"value": math.inf}],
        }
    )

    assert payload == {
        "finite": 1.25,
        "nan": None,
        "positive_infinity": None,
        "negative_infinity": None,
        "nested": [None, {"value": None}],
    }
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_jsonable_normalizes_nested_frozenset_dictionary_keys_deterministically():
    payload = {
        ("scope", frozenset({("beta", 2), ("alpha", 1)})): "nested",
        frozenset({"zulu", "alpha"}): "flat",
    }

    converted = jsonable(payload)

    assert converted == {
        '__memguard_key__:{"type":"frozenset","value":["alpha","zulu"]}': "flat",
        '__memguard_key__:{"type":"tuple","value":["scope",{"type":"frozenset","value":[["alpha",1],["beta",2]]}]}': "nested",
    }


def test_jsonable_composite_dictionary_keys_are_stable_across_pythonhashseed(tmp_path):
    script = tmp_path / "render_jsonable.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "from memguard.json_support import jsonable",
                "payload = {frozenset({'zulu', 'alpha'}): 'flat', ('scope', frozenset({('beta', 2), ('alpha', 1)})): 'nested'}",
                "print(json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(',', ':')))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outputs = []
    project_root = Path(__file__).resolve().parents[1]
    for seed in ("1", "2", "8675309"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=project_root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout)

    assert len(set(outputs)) == 1


def test_jsonable_rejects_dictionary_key_collisions_after_normalization():
    composite_key = frozenset({"alpha", "zulu"})
    colliding_string = '__memguard_key__:{"type":"frozenset","value":["alpha","zulu"]}'

    with pytest.raises(ValueError, match="dictionary key collision"):
        jsonable({composite_key: "composite", colliding_string: "string"})
