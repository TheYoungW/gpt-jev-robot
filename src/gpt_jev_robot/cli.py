import argparse
import json
import math
from pathlib import Path
import sys
from .sim import RobotSim
from .actions import execute
from .decision import JevClient, load_private_key, decide_proposal
from .perception import detect_objects


def respond(sim, request):
    op = request.get("op")
    if op == "perceive": return detect_objects(sim)
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
    sub = p.add_subparsers(dest="mode", required=True)
    sub.add_parser("init")
    sub.add_parser("observe")
    sub.add_parser("perceive")
    sub.add_parser("evaluate")
    act = sub.add_parser("act"); act.add_argument("json_file")
    serve = sub.add_parser("serve"); serve.add_argument("--reset", action="store_true"); serve.add_argument("--video", action="store_true")
    demo = sub.add_parser("demo"); demo.add_argument("--online", action="store_true"); demo.add_argument("--video", action="store_true")
    args = p.parse_args()
    if args.mode == "demo":
        from .episode import run_episode
        run_episode(args.run_dir, online=args.online, video=args.video)
        return
    sim = RobotSim(args.run_dir, reset=args.mode == "init" or getattr(args, "reset", False), video=getattr(args, "video", False))
    try:
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
            request = json.loads(Path(args.json_file).read_text()) if args.mode == "act" else {"op": "observe" if args.mode == "init" else args.mode}
            print(json.dumps(respond(sim, request), indent=2, ensure_ascii=False))
    finally: sim.close()


if __name__ == "__main__": main()
