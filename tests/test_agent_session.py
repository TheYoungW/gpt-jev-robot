import json
import sys
import pytest
from gpt_jev_robot.agent_session import AgentSession
from gpt_jev_robot.sim import RobotSim


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    sim = RobotSim(tmp_path_factory.mktemp("visual_agent"), reset=True)
    agent = AgentSession(sim)
    agent.observe()
    yield agent
    sim.close()


def proposal(session):
    receipt = json.loads(session.receipt_path.read_text())
    return {"observation_id": receipt["observation_id"], "viewed_cameras": ["center"],
            "visual_assessment": "The open fingers are above the objects; inspect before approaching.",
            "decision_summary": "No grasp is confirmed; another observation will clarify the target.",
            "candidates": {"observe": {"description": "Observe", "command": {"op": "observe"}}, "up": {"description": "Raise", "command": {"op": "nudge", "direction": "up"}}}}


@pytest.mark.parametrize("change", [
    lambda p: p.update(observation_id="old-image"),
    lambda p: p.update(viewed_cameras=[]),
    lambda p: p.update(visual_assessment=""),
    lambda p: p["candidates"]["observe"].update(command={"op":"gripper", "closed":True}),
])
def test_missing_visual_evidence_rejected_before_jev(session, change):
    p = proposal(session); change(p)
    before=session.sim.data.qpos.copy(); time=session.sim.data.time
    with pytest.raises(ValueError): session.step(p, client=object())
    assert session.sim.data.time==time
    assert (session.sim.data.qpos==before).all()


def test_images_modified_after_capture_cannot_be_cited(session):
    from pathlib import Path
    receipt=json.loads(session.receipt_path.read_text())
    path=Path(receipt["images"]["center"]); original=path.read_bytes()
    try:
        path.write_bytes(b"different image")
        with pytest.raises(ValueError,match="changed"): session.step(proposal(session),client=object())
    finally: path.write_bytes(original)


def test_robot_movement_invalidates_view(session):
    previous=session.sim.data.time
    try:
        session.sim.data.time+=.01
        with pytest.raises(ValueError,match="moved"): session.step(proposal(session),client=object())
    finally: session.sim.data.time=previous


def test_default_entrypoint_does_not_import_grasp_baseline():
    import subprocess
    code="import gpt_jev_robot.cli, sys; assert 'gpt_jev_robot.perception' not in sys.modules; assert 'gpt_jev_robot.episode' not in sys.modules"
    subprocess.run([sys.executable,"-c",code],check=True)


def test_network_failure_preserves_unconsumed_view_and_does_not_move(session):
    from gpt_jev_robot.decision import DecisionError
    class Unavailable:
        def choose(self, *args): raise DecisionError("test network unavailable")
    time=session.sim.data.time
    with pytest.raises(DecisionError): session.step(proposal(session),client=Unavailable())
    assert session.sim.data.time==time
    assert not json.loads(session.receipt_path.read_text())["consumed"]
    record=json.loads((session.path/"events.jsonl").read_text().splitlines()[-1])
    assert record["kind"]=="agent_decision_failed" and record["motion_executed"] is False


def test_conflicting_recorder_rejected_before_jev_or_consuming_view(session):
    session.sim.writer = object()
    try:
        with pytest.raises(ValueError, match="owns recording"):
            session.step(proposal(session), client=object())
        assert not json.loads(session.receipt_path.read_text())["consumed"]
    finally:
        session.sim.writer = None
