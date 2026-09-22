"""Reproducible agent-authored playbook. GPT is not called by this Python module.

The interactive CLI/MCP permits arbitrary agent tool selection. This bounded
playbook is a transparent baseline for repeating the demonstration and testing
the Jev decision layer. It is not presented as an autonomous GPT runtime.
"""
import json
from pathlib import Path
import shutil
from .actions import execute
from .decision import JevClient, load_private_key, decide_proposal
from .perception import detect_objects
from .sim import RobotSim, MotionError
from .scene import OBJECTS

DESCRIPTIONS = {
    "approach": "The selected target remains on the table; gripper is open and has not reached its approach pose. Move above it with clearance.",
    "descend": "Open gripper has reached the approach pose above the selected target. Descend vertically to the grasp height.",
    "close": "Open gripper has reached grasp height around the selected target. Close the fingers.",
    "test_lift": "Fingers have closed with bilateral target contact but the grasp has not yet been lift-tested. Lift vertically to test retention.",
    "transfer": "Lift test succeeded with bilateral target contact. Carry the held object horizontally to the free tray slot.",
    "lower": "The held object is above its free tray slot at transfer height. Lower a little for release.",
    "release": "The held object is over its free tray slot at release height. Open fingers.",
    "retreat": "Fingers have opened over the tray. Withdraw upward so the released object can settle independently.",
    "observe": "The observations are missing, inconsistent, or the appropriate next motion cannot be determined. Acquire another observation without motion.",
}


def bilateral(sim, target):
    pairs = sim.contacts()
    return all(any(f"l_pad{i}" in p and any(n.startswith(target + "_") for n in p) for p in pairs) for i in (8, 9))


def run_episode(run_dir, online=False, video=False):
    path = Path(run_dir)
    if (path / "scene.xml").exists():
        raise ValueError("Use a new run directory to preserve existing episode evidence")
    client = JevClient(load_private_key()) if online else None
    sim = RobotSim(path, reset=True, video=video)
    mode = "live_jev_agent_authored_playbook" if online else "deterministic_baseline_no_jev"
    report = {"mode": mode, "success": False, "objects_completed": [], "policy": "agent-authored finite playbook; no GPT API calls", "video_speed": 4.02, "perception": "RGB color thresholds + calibrated depth; known colors and core height", "physics": "actuated URDF joints and frictional contacts; no weld, mocap attachment or object teleport"}
    notes = ["# 决策与验证摘要\n", f"运行模式：`{mode}`。以下是任务级决策摘要和实测结果。\n"]
    try:
        first = sim.observe("before")
        for camera, image in first["images"].items(): shutil.copyfile(image, path / f"before_{camera}.png")
        slots = {"coral": [.30, -.01], "teal": [.395, -.005], "gold": [.34, -.075]}
        for name in OBJECTS:
            detections = detect_objects(sim)
            target = detections[name]
            if not target["visible"] or target["pixels"] < 80:
                raise MotionError(f"Insufficient visible target: {name}; request agent review")
            x, y = target["surface_center"][:2]
            sx, sy = slots[name]
            commands = {
                "approach": {"op": "move", "position": [x, y, .76]},
                "descend": {"op": "move", "position": [x, y, .650]},
                "close": {"op": "gripper", "closed": True},
                "test_lift": {"op": "move", "position": [x, y, .76]},
                "transfer": {"op": "move", "position": [sx, sy, .76]},
                "lower": {"op": "move", "position": [sx, sy, .72]},
                "release": {"op": "gripper", "closed": False},
                "retreat": {"op": "move", "position": [sx, sy, .82]},
                "observe": {"op": "observe", "label": "jev_requested_observation"},
            }
            completed = []
            notes.append(f"\n## {name}\n\n依据中央 RGB-D 测得表面中心 `{target['surface_center']}`，从上方接近；先试提，确认夹持后才转移。\n")
            for expected in list(commands)[:-1]:
                if expected in ("test_lift", "transfer", "lower", "release") and not bilateral(sim, name):
                    raise MotionError(f"{name}: bilateral contact missing before {expected}; request re-observation/regrasp")
                summaries = {
                    None: "Fresh camera observation localizes the selected object on the tabletop. Fingers are open. The gripper is at a safe pose away from this target. This target has not been approached. Its destination slot is available.",
                    "approach": "Open fingers are directly above the selected tabletop object at approach height. Approach motion completed with measured tracking error below 12 mm. Descent has not happened.",
                    "descend": "Open fingers surround the selected object at grasp height. Vertical descent completed successfully. Fingers have not closed yet.",
                    "close": "Fingers have closed and both pads touch the selected object. The object has not yet been lifted or tested for retention.",
                    "test_lift": "Vertical lift completed. Both finger pads still touch the selected object at transfer height, verifying retention. The held object is still above its pickup location, away from the tray slot.",
                    "transfer": "Horizontal transfer completed. Both pads retain the object directly above its free tray slot at transfer height. It has not been lowered to release height.",
                    "lower": "Lowering completed. Both pads retain the object directly above the designated tray slot at release height. Fingers remain closed.",
                    "release": "Opening completed over the tray slot. Fingers are open and no longer touch the object. The gripper is still at the release pose and has not withdrawn.",
                }
                state = {"task": "Put each irregular object in a tray, release it, withdraw and verify stable support", "simulation_time": sim.data.time, "target": name, "observation_summary": summaries[completed[-1] if completed else None], "initial_rgbd_detection": target, "tcp_world_m": sim.tcp().tolist(), "completed_actions_for_current_target": completed.copy(), "bilateral_target_contact": bilateral(sim, name), "target_slot_world_xy_m": slots[name], "constraints": "Keep holding during transfer; do not release before reaching tray release pose. Motion stages cannot be skipped. Observations after each action and actual contacts support the completed action list.", "provenance": "Agent-authored playbook using RGB-D and joint/contact feedback. Jev receives JSON, no images."}
                proposal = {"state": state, "candidates": {k: {"description": DESCRIPTIONS[k], "command": cmd} for k, cmd in commands.items()}}
                if online:
                    chosen, record = decide_proposal(proposal, client, path / "decisions.jsonl")
                    if chosen == "observe":
                        sim.observe("uncertain")
                        raise MotionError("Jev requested re-observation; playbook yields to interactive agent without guessing")
                    if chosen != expected:
                        raise MotionError(f"Jev selected {chosen}; stage precondition requires {expected}. No motion dispatched; agent review required")
                else:
                    chosen = expected
                    record = {"source": "deterministic_baseline_no_jev", "accepted_action": chosen}
                    sim.event("baseline_decision", **record)
                sim.label = f"{name.upper()} / {chosen.replace('_', ' ')} / {'Jev' if online else 'baseline'}"
                result = execute(sim, commands[chosen])
                completed.append(chosen)
                # Actual observations after each action, usable by an interactive agent.
                observation = sim.observe(f"{name}_{chosen}")
                notes.append(f"- `{chosen}`：t={sim.data.time:.2f}s；双侧接触={bilateral(sim, name)}；观测 `{Path(observation['images']['center']).relative_to(path.resolve())}`。\n")
                print(json.dumps({"object": name, "action": chosen, "sim_time": round(sim.data.time, 3), "confidence": record.get("decision", {}).get("confidence"), "bilateral_contact": bilateral(sim, name)}), flush=True)
            sim.step(.8)
            evaluation = sim.oracle()["objects"][name]
            if not evaluation["in_tray"] or evaluation["speed"] > .02:
                raise MotionError(f"{name}: released placement failed evaluator")
            report["objects_completed"].append(name)
            notes.append(f"- 松爪、退臂、静置后评估：物体在盘内，速度 {evaluation['speed']:.6f} m/s。该验收使用独立仿真真值，不冒充视觉估计。\n")
        sim.move([.30, .20, .80]); sim.step(1.)
        final = sim.observe("after")
        for camera, image in final["images"].items(): shutil.copyfile(image, path / f"after_{camera}.png")
        report.update(sim.oracle())
        report["simulation_time"] = sim.data.time
        report["joint_limit_max_violation_rad_or_m"] = max(0., max(float(max(sim.model.jnt_range[j,0]-sim.data.qpos[sim.model.jnt_qposadr[j]], sim.data.qpos[sim.model.jnt_qposadr[j]]-sim.model.jnt_range[j,1])) for j in range(sim.model.njnt) if sim.model.jnt_limited[j]))
    except Exception as e:
        report["error"] = str(e)
        sim.event("episode_failed", error_type=type(e).__name__, message=str(e))
        raise
    finally:
        report["video_frames"] = sim.frame_count
        (path / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        (path / "decision_summary.md").write_text("".join(notes))
        sim.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report
