"""Strict allowlist between decisions and muscles."""
import math
from .sim import MotionError


def execute(sim, command):
    if not isinstance(command, dict): raise MotionError("Expected action object")
    op = command.get("op")
    allowed = {"observe": {"op", "label"}, "move": {"op", "position", "arm", "yaw"}, "nudge": {"op", "direction", "distance", "arm"}, "gripper": {"op", "closed", "arm"}, "wait": {"op", "seconds"}, "finish": {"op"}}
    if op not in allowed or set(command) - allowed[op]:
        raise MotionError("Unknown action or arguments")
    if op == "observe": return sim.observe(command.get("label", "observation"))
    if op == "move": return sim.move(command["position"], command.get("arm", "l"), command.get("yaw", math.pi/2))
    if op == "nudge": return sim.nudge(command["direction"], command.get("distance", .025), command.get("arm", "l"))
    if op == "gripper":
        if not isinstance(command.get("closed"), bool): raise MotionError("closed must be boolean")
        return sim.gripper(command["closed"], command.get("arm", "l"))
    if op == "wait":
        seconds = command.get("seconds", .5)
        if not isinstance(seconds, (int,float)) or not math.isfinite(seconds) or not 0 < seconds <= 3: raise MotionError("Wait must be 0..3 seconds")
        sim.step(seconds); sim.save(); return {"waited": seconds}
    if op == "finish":
        report = sim.oracle()
        if not report["success"]: raise MotionError("Completion predicate failed")
        return report
