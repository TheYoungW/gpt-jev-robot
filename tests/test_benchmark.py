import copy
import runpy
from pathlib import Path

b = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/benchmark_v2.py"))


def test_text_keeps_unknown_false_empty_and_special_characters():
    value = {"a/b": "unknown", "boolean": False, "list": [], "none": None, "text": "line\nbreak"}
    rendered = " ".join(b["flatten"](value))
    for fragment in ('state/a~1b', '"unknown"', 'false', '[]', 'null', '"line\\nbreak"'):
        assert fragment in rendered


def test_candidate_randomization_preserves_commands_and_requirement_references():
    original = {"state": {}, "candidates": {"observe": {"command": {"op": "observe"}},
                "a01": {"command": {"op": "nudge", "direction": "up"}, "requires": [{"fact": "candidate.a01.path_clear"}]},
                "a02": {"command": {"op": "gripper", "closed": True}, "requires": [{"fact": "candidate.a02.path_clear"}]}}}
    original["state"]["candidate_actions"] = copy.deepcopy(original["candidates"])
    snapshot = copy.deepcopy(original)
    for seed in range(10):
        p, reverse = b["permute"](original, seed)
        assert p["candidates"] == p["state"]["candidate_actions"]
        for new, old in reverse.items():
            assert p["candidates"][new]["command"] == original["candidates"][old]["command"]
            if new != "observe": assert p["candidates"][new]["requires"][0]["fact"] == f"candidate.{new}.path_clear"
    assert original == snapshot


def test_same_gate_applies_to_agent_and_jev():
    c = {"observe": {"eligible": True}, "a01": {"eligible": False}}
    assert b["accepted"]("a01", 1., c) == ("observe", "ineligible_action")
    assert b["accepted"]("observe", .1, c) == ("observe", "low_confidence")
