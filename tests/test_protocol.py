"""Protocol contract tests; mock Jev responses are only used in temporary runs."""
import copy
import json
import httpx
import numpy as np
import pytest
from pydantic import ValidationError
from gpt_jev_robot.protocol import ProposalV2, compile_proposal, template
from gpt_jev_robot.decision import JevClient, decide_proposal
from gpt_jev_robot.sim import RobotSim, MotionError
from gpt_jev_robot.agent_session import AgentSession


def receipt():
    return {"observation_id": "123456abcdef", "simulation_time": 2.,
            "robot_feedback": {"tcp_world_m": {"l": [.3, .2, .8], "r": [.3, -.2, .8]},
                               "grippers": {a: {"commanded_closed": False, "both_pad_contacts": False} for a in ("l", "r")}}}


def proposal(r=None):
    r = r or receipt()
    p = template(r)
    p["viewed_cameras"] = ["l_wrist"]
    p["objects"][0].update(appearance="Teal block with a side protrusion", visibility="clear")
    p["visual_facts"][0].update(value="yes", reason="", evidence=[{"observation_id": r["observation_id"], "camera": "l_wrist"}])
    return p


class NoMotion:
    def plan_move(self, *args): pass
    def tcp(self, arm): return np.array([.3, .2, .8])


def movement(p, direction="up", distance=.02):
    p["candidates"]["a01"] = {
        "intent": "retreat", "command": {"op": "nudge", "direction": direction, "distance": distance},
        "requires": [], "clearance": {"value": "yes", "evidence": [{"observation_id": p["observation_id"], "camera": "l_wrist"}]},
        "expected_observation": [{"relation": "visible", "value": "yes"}],
        "failure_signals": [{"relation": "visible", "value": "no"}]}
    return p


def test_wire_payload_contains_parameters_but_no_recommendation(tmp_path):
    p = movement(proposal(), "back", .025)
    p["audit_note"] = "AUDIT_ONLY_RECOMMENDATION_DO_NOT_TRANSMIT"
    compiled = compile_proposal(p, receipt(), {}, NoMotion())
    def handler(request):
        body = json.loads(request.content)
        assert "AUDIT_ONLY" not in request.content.decode()
        assert "audit_note" not in body["state"] and "decision_summary" not in body["state"]
        c = body["state"]["candidate_actions"]["a01"]
        assert c["command"] == {"op": "nudge", "arm": "l", "direction": "back", "distance": .025}
        assert body["state"]["coordinates"]["frame"] == "world"
        assert c["expected_observation"] and c["failure_signals"] and c["eligible"]
        return httpx.Response(200, json={"model": "test-only", "answers": {"next_action": {
            "type": "choice", "choice": "a01", "confidence": .9, "probabilities": {"a01": .9, "observe": .1}}}})
    client = JevClient("test-only", transport=httpx.MockTransport(handler))
    chosen, _ = decide_proposal(compiled, client, tmp_path / "decisions.jsonl")
    assert chosen == "a01"


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(decision_summary="Choose the recommended action"),
    lambda p: p.update(robot_feedback={"both_pad_contacts": True}),
    lambda p: p["visual_facts"].pop(),
    lambda p: p["visual_facts"][1].update(reason=""),
    lambda p: p["visual_facts"][0].update(confidence=.99),
    lambda p: p["visual_facts"].append(copy.deepcopy(p["visual_facts"][0])),
    lambda p: p["candidates"]["a01"].update(description="The obviously correct action"),
    lambda p: p["candidates"].update(best_action=p["candidates"].pop("a01")),
    lambda p: p["candidates"]["a01"]["command"].update(seconds=float("nan")),
])
def test_schema_rejects_missing_facts_and_untrusted_fields(mutate):
    p = proposal(); mutate(p)
    with pytest.raises(ValidationError): ProposalV2.model_validate(p)


def test_unknown_requirement_blocks_action_and_high_confidence_cannot_override(tmp_path):
    p = proposal()
    p["candidates"]["a01"]["requires"] = [{"fact": "object_01.moves_with_gripper", "equals": "yes"}]
    compiled = compile_proposal(p, receipt(), {}, NoMotion())
    assert not compiled["candidates"]["a01"]["eligible"]
    assert compiled["candidates"]["a01"]["requires"][0]["status"] == "unknown"
    client = JevClient("test-only", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "test-only", "answers": {"next_action": {"type": "choice", "choice": "a01", "confidence": .99,
        "probabilities": {"a01": .99, "observe": .01}}}})))
    chosen, record = decide_proposal(compiled, client, tmp_path / "decisions.jsonl")
    assert chosen == "observe" and record["gate_reason"] == "ineligible_action"
    assert record["decision"]["action"] == "a01"


def test_current_and_temporal_image_provenance():
    p = proposal()
    f = next(f for f in p["visual_facts"] if f["relation"] == "moves_with_gripper")
    f.update(value="yes", evidence=[{"observation_id": p["observation_id"], "camera": "l_wrist"}])
    with pytest.raises(ValueError, match="before/after"): compile_proposal(p, receipt(), {}, NoMotion())
    old = copy.deepcopy(receipt()); old.update(observation_id="111111111111", simulation_time=1.)
    old["robot_feedback"]["tcp_world_m"]["l"][2] -= .04
    ref = {"observation_id": old["observation_id"], "camera": "l_wrist"}
    f["evidence"].append(ref)
    with pytest.raises(ValueError, match="not declared"): compile_proposal(p, receipt(), {old["observation_id"]: old}, NoMotion())
    p["compared_images"] = [ref]
    assert compile_proposal(p, receipt(), {old["observation_id"]: old}, NoMotion())
    old["simulation_time"] = receipt()["simulation_time"]
    with pytest.raises(ValueError, match="before/after"): compile_proposal(p, receipt(), {old["observation_id"]: old}, NoMotion())


def test_ik_rejection_and_clearance_are_candidate_specific():
    p = movement(proposal())
    p["candidates"]["a02"] = copy.deepcopy(p["candidates"]["a01"])
    p["candidates"]["a02"]["command"]["direction"] = "down"
    p["candidates"]["a02"]["clearance"].update(value="unknown", reason="Occluded descending corridor")
    compiled = compile_proposal(p, receipt(), {}, NoMotion())
    assert compiled["candidates"]["a01"]["eligible"] and not compiled["candidates"]["a02"]["eligible"]
    class Unreachable(NoMotion):
        def plan_move(self, *args): raise MotionError("IK unreachable")
    compiled = compile_proposal(p, receipt(), {}, Unreachable())
    assert not compiled["candidates"]["a01"]["eligible"]
    assert compiled["candidates"]["observe"]["eligible"]


def test_transport_cannot_omit_retention_requirement():
    p = movement(proposal()); p["candidates"]["a01"]["intent"] = "transport"
    compiled = compile_proposal(p, receipt(), {}, NoMotion())
    c = compiled["candidates"]["a01"]
    assert not c["eligible"]
    assert any(r["fact"] == "object_01.moves_with_gripper" for r in c["requires"])


def test_previous_action_review_preserves_unknown_and_records_failure_evidence():
    r = receipt()
    r["last_action"] = {"target_id": "object_01", "expected_observation": [{"relation": "moves_with_gripper", "value": "yes"}],
                        "failure_signals": [{"relation": "visible", "value": "no"}]}
    p = proposal(r)
    p["visual_facts"][0]["value"] = "no"
    state = compile_proposal(p, r, {}, NoMotion())["state"]
    assert state["previous_action_review"]["expected_observation"][0]["match"] == "unknown"
    assert state["previous_action_review"]["failure_signals"][0]["match"] == "yes"


def test_real_preflight_preserves_physics_and_v2_step_records_audit(tmp_path):
    sim = RobotSim(tmp_path, reset=True)
    try:
        session = AgentSession(sim); r = session.observe()
        before = (sim.data.qpos.copy(), sim.data.qvel.copy(), sim.data.ctrl.copy(), sim.data.time)
        sim.plan_move(sim.tcp() + [0, 0, .01])
        assert np.array_equal(before[0], sim.data.qpos)
        assert np.array_equal(before[1], sim.data.qvel)
        assert np.array_equal(before[2], sim.data.ctrl) and sim.data.time == before[3]
        p = proposal(r); p["audit_note"] = "LOCAL_REVIEW_ONLY"
        client = JevClient("test-only", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "model": "test-only", "answers": {"next_action": {"type": "choice", "choice": "observe", "confidence": .9,
            "probabilities": {"observe": .9, "a01": .1}}}})))
        out = session.step(p, client)
        assert out["status"] == "executed" and sim.data.time == before[3]
        assert "LOCAL_REVIEW_ONLY" in (tmp_path / "agent_steps.jsonl").read_text()
        assert "LOCAL_REVIEW_ONLY" not in (tmp_path / "decisions.jsonl").read_text()
        assert out["next_observation"]["last_action"]["command"]["op"] == "observe"
        assert out["timing"]["observation_to_submission_s"] >= 0
        with pytest.raises(ValueError, match="latest unused"): session.step(p, client)
        # Exercise the normalized V2 command through real simulation muscles.
        move_p = movement(proposal(out["next_observation"]), distance=.01)
        move_client = JevClient("test-only", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "model": "test-only", "answers": {"next_action": {"type": "choice", "choice": "a01", "confidence": .9,
            "probabilities": {"observe": .1, "a01": .9}}}})))
        z = sim.tcp()[2]
        moved = session.step(move_p, move_client)
        assert moved["status"] == "executed" and moved["accepted_action"] == "a01"
        assert .008 < sim.tcp()[2] - z < .012
        assert moved["next_observation"]["last_action"]["command"]["distance"] == .01
        # Historical images must remain the images the receipt originally described.
        current = moved["next_observation"]
        next_p = proposal(current)
        next_p["compared_images"] = [{"observation_id": r["observation_id"], "camera": "l_wrist"}]
        from pathlib import Path
        old_image = Path(r["images"]["l_wrist"])
        old_image.write_bytes(b"modified historical image")
        with pytest.raises(ValueError, match="Historical image changed"):
            session.step(next_p, client=object())
        assert not json.loads(session.receipt_path.read_text())["consumed"]
    finally:
        sim.close()
