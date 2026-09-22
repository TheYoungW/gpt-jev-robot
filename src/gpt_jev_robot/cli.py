import argparse
import json
import math
from pathlib import Path
import sys
from .sim import RobotSim
from .actions import execute
from .decision import JevClient, load_private_key, decide_proposal
from .agent_session import AgentSession


def respond(sim, request):
    op = request.get("op")
    if op == "agent_observe": return AgentSession(sim).observe()
    if op == "agent_step": return AgentSession(sim).step(request["proposal"])
    if op == "agent_evaluate": return AgentSession(sim).evaluate()
    if op == "baseline_perceive":
        from .perception import detect_objects
        return detect_objects(sim)
    if op == "evaluate": return sim.oracle()
    if op == "decide":
        proposal = request["proposal"]
        observed = proposal["state"].get("simulation_time")
        if not isinstance(observed, (int, float)) or not math.isfinite(observed) or abs(observed - sim.data.time) > .01:
            raise ValueError("Proposal must cite the current simulation_time")
        if proposal["candidates"].get("observe", {}).get("command", {}).get("op") != "observe":
            raise ValueError("The observe fallback must be an observation command")
        action, record = decide_proposal(proposal, JevClient(load_private_key()), sim.path / "decisions.jsonl")
        sim.label = action.replace("_", " ")
        result = execute(sim, proposal["candidates"][action]["command"])
        return {"decision": record, "result": result, "simulation_time": sim.data.time}
    return execute(sim, request)


def main():
    p = argparse.ArgumentParser(description="Simulation-only agent + Jev robot tools")
    p.add_argument("--run-dir", default="runs/session")
    sub = p.add_subparsers(dest="mode")
    sub.add_parser("agent-start")
    sub.add_parser("agent-observe")
    sub.add_parser("agent-evaluate")
    agent_step = sub.add_parser("agent-step"); agent_step.add_argument("json_file")
    sub.add_parser("init")
    sub.add_parser("observe")
    sub.add_parser("baseline-perceive")
    sub.add_parser("evaluate")
    act = sub.add_parser("act"); act.add_argument("json_file")
    serve = sub.add_parser("serve"); serve.add_argument("--reset", action="store_true"); serve.add_argument("--video", action="store_true")
    demo = sub.add_parser("baseline-demo"); demo.add_argument("--online", action="store_true"); demo.add_argument("--video", action="store_true")
    args = p.parse_args()
    if args.mode is None:
        args.mode = "agent-observe" if (Path(args.run_dir)/"scene.xml").exists() else "agent-start"
    if args.mode == "baseline-demo":
        from .episode import run_episode
        run_episode(args.run_dir, online=args.online, video=args.video)
        return
    if args.mode == "agent-start" and (Path(args.run_dir)/"scene.xml").exists():
        raise ValueError("Use a new run directory to preserve existing observations")
    sim = RobotSim(args.run_dir, reset=args.mode in ("init", "agent-start") or getattr(args, "reset", False), video=getattr(args, "video", False))
    try:
        if args.mode.startswith("agent-"):
            session = AgentSession(sim)
            if args.mode == "agent-step": result = session.step(json.loads(Path(args.json_file).read_text()))
            elif args.mode == "agent-evaluate": result = session.evaluate()
            else:
                result = session.observe("before" if args.mode == "agent-start" else "agent")
                if args.mode == "agent-start":
                    import shutil
                    for name, image in result["images"].items(): shutil.copyfile(image, sim.path / f"before_{name}.png")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return
        if args.mode == "serve":
            print(json.dumps({"ready": True, "simulation_time": sim.data.time}), flush=True)
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    if request.get("op") == "quit": break
                    print(json.dumps(respond(sim, request), ensure_ascii=False), flush=True)
                except Exception as e:
                    sim.event("error", error_type=type(e).__name__, message=str(e))
                    print(json.dumps({"error": type(e).__name__, "message": str(e)}), flush=True)
        else:
            request = json.loads(Path(args.json_file).read_text()) if args.mode == "act" else {"op": "observe" if args.mode == "init" else args.mode.replace("-", "_")}
            print(json.dumps(respond(sim, request), indent=2, ensure_ascii=False))
    finally: sim.close()


if __name__ == "__main__": main()
