import os
os.environ.setdefault("MUJOCO_GL", "egl")
import json
import numpy as np
import pytest
import mujoco
from gpt_jev_robot.sim import RobotSim, MotionError
from gpt_jev_robot.actions import execute
from gpt_jev_robot.cli import respond
from gpt_jev_robot.perception import unproject


@pytest.fixture
def sim(tmp_path):
    s = RobotSim(tmp_path / "run", reset=True)
    yield s
    s.close()


def test_supplied_model_limits_and_original_tcp_are_preserved(sim):
    assert sim.model.nu == 18
    assert sim.model.body("l-tool0").id > 0
    np.testing.assert_allclose(sim.model.joint("l-joint7").range, [-1.3956,1.3956])
    for name in ("center", "l_wrist", "r_wrist"): assert sim.model.camera(name).id >= 0
    assert sim.model.neq == 0  # No object attachments.


@pytest.mark.parametrize("command", [
    {"op":"move", "position":[1.5,0,.7]},
    {"op":"move", "position":[float('nan'),0,.7]},
    {"op":"move", "position":[.3,0,.3]},
    {"op":"nudge", "direction":"down", "distance":.2},
    {"op":"gripper", "closed":"false"},
    {"op":"shell", "code":"print(1)"},
    {"op":"wait", "seconds":float('inf')},
])
def test_invalid_actions_do_not_advance_simulation(sim, command):
    q = sim.data.qpos.copy(); time = sim.data.time
    with pytest.raises(MotionError): execute(sim, command)
    np.testing.assert_array_equal(sim.data.qpos, q)
    assert sim.data.time == time


def test_stale_proposal_is_rejected_before_network(sim):
    for timestamp in (-100, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="current simulation_time"):
            respond(sim, {"op":"decide", "proposal":{"state":{"simulation_time":timestamp}}})


def test_observe_label_cannot_escape_run_directory(sim):
    with pytest.raises(ValueError): sim.observe("../../escape")


def test_low_confidence_fallback_cannot_be_motion(sim):
    with pytest.raises(ValueError, match="observation command"):
        respond(sim, {"op":"decide", "proposal":{"state":{"simulation_time":sim.data.time}, "candidates":{"observe":{"command":{"op":"move"}}}}})


def test_calibration_projects_and_unprojects_world_points(sim):
    for camera in ("center", "l_wrist", "r_wrist"):
        c = sim.calibration(camera); T=np.asarray(c["T_world_camera"]);K=np.asarray(c["K"])
        pc=np.array([[.03,-.02,.5],[-.04,.05,.7]])
        world=pc@T[:3,:3].T+T[:3,3]
        projected=pc@K.T;uv=projected[:,:2]/projected[:,2,None]
        np.testing.assert_allclose(unproject(uv,pc[:,2],c),world,atol=1e-10)


def test_physical_grasp_lift_release_and_persistent_state(sim):
    before=sim.data.body("coral").xpos.copy()
    sim.move([.375,.16,.76]);sim.move([.375,.16,.65]);sim.gripper(True)
    assert any("coral_core" in p for p in sim.contacts())
    sim.move([.375,.16,.76])
    assert sim.data.body("coral").xpos[2] > before[2]+.075
    sim.move([.30,-.01,.76]);sim.move([.30,-.01,.72]);sim.gripper(False)
    sim.move([.30,-.01,.82]);sim.step(1);sim.save()
    result=sim.oracle()["objects"]["coral"]
    assert result["in_tray"] and result["speed"] < .02
    loaded=RobotSim(sim.path)
    try: np.testing.assert_array_equal(loaded.data.qpos,sim.data.qpos)
    finally: loaded.close()
