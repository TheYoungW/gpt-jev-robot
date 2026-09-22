"""stdio MCP server; owns one persistent simulation and never accesses hardware."""
import os
import threading
from mcp.server.fastmcp import FastMCP
from .sim import RobotSim
from .perception import detect_objects
from .cli import respond

mcp = FastMCP("yunyi-mujoco-muscles")
_sim = None
_lock = threading.RLock()


def sim():
    global _sim
    if _sim is None: _sim = RobotSim(os.environ.get("ROBOT_RUN_DIR", "runs/mcp"))
    return _sim


@mcp.tool()
def observe() -> dict:
    """Save synchronized center/left-wrist/right-wrist RGB-D and calibration; meters, world frame."""
    with _lock: return sim().observe()


@mcp.tool()
def perceive() -> dict:
    """Known-color RGB-D baseline; explicitly limited to this demonstration's objects."""
    with _lock: return detect_objects(sim())


@mcp.tool()
def move_to(x: float, y: float, z: float, arm: str = "l", yaw: float = 1.5707963267948966) -> dict:
    """Simulation TCP Cartesian move, XYZ meters, yaw radians. IK preflight and tracking checks."""
    with _lock: return sim().move([x, y, z], arm, yaw)


@mcp.tool()
def nudge(direction: str, distance: float = .025, arm: str = "l") -> dict:
    """up/down/forward/back/left/right in world frame, at most 0.05 meters."""
    with _lock: return sim().nudge(direction, distance, arm)


@mcp.tool()
def set_gripper(closed: bool, arm: str = "l") -> dict:
    """Open/close physical simulated finger joints; return contact pairs."""
    with _lock: return sim().gripper(closed, arm)


@mcp.tool()
def decide_next(proposal: dict) -> dict:
    """Ask live Jev to choose from an agent-authored proposal, gate confidence, execute one allowed action. Requires current simulation_time and an observe fallback. API key from TYPESAFE_API_KEY."""
    with _lock: return respond(sim(), {"op": "decide", "proposal": proposal})


@mcp.tool()
def verify_completion() -> dict:
    """Privileged simulation evaluator, kept separate from camera observations."""
    with _lock: return sim().oracle()


def main():
    try: mcp.run(transport="stdio")
    finally:
        if _sim is not None: _sim.close()


if __name__ == "__main__": main()
