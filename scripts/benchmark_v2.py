"""Pre-registered pilot: fixture creation, frozen inputs, live API and isolated scoring.

Setup and scoring may use simulator state. Neither sends object coordinates to
the conversation policy or Jev. No automatic visual annotation or grasp policy.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import shutil
import time
import xml.etree.ElementTree as ET
import numpy as np
os.environ.setdefault("MUJOCO_GL", "egl")
from gpt_jev_robot.scene import build_scene
from gpt_jev_robot.sim import RobotSim
from gpt_jev_robot.actions import execute
from gpt_jev_robot.agent_session import AgentSession
from gpt_jev_robot.protocol import compile_proposal, ProposalV2
from gpt_jev_robot.decision import JevClient, DecisionError, load_private_key

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/v2_pilot_01"
MODEL = "jev-1.13.0"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))


def read(path): return json.loads(path.read_text())


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    if RUN.exists(): raise ValueError("New trial directory required")
    RUN.mkdir(parents=True)
    shutil.copyfile(ROOT / "experiments/V2_PILOT_PLAN.md", RUN / "preregistered_plan.md")
    manifest = {"created_wall_time": time.time(), "plan_sha256": sha(RUN / "preregistered_plan.md"), "model": MODEL, "cases": []}
    historical = {6: ("H01", "recover_open", "gold"), 10: ("H02", "retained_lift", "gold"),
                  12: ("H03", "release_in_tray", "gold"), 17: ("H04", "retained_lift", "coral"),
                  36: ("H05", "retreat", "teal"), 38: ("H06", "finish", "gold")}
    steps = [json.loads(s) for s in (ROOT / "examples/live_visual/agent_steps.jsonl").read_text().splitlines()]
    sim = RobotSim(RUN / "fixture_replay", reset=True)
    session = AgentSession(sim)
    try:
        for index, record in enumerate(steps, 1):
            if index in historical: session.observe("fixture_before")
            cmd = record["proposal"]["candidates"][record["accepted_action"]]["command"]
            status = "executed"
            try: execute(sim, cmd)
            except Exception:
                if record["status"] != "execution_failed": raise
                status = "execution_failed"
            if index in historical:
                cid, objective, body = historical[index]
                session.observe("fixture_after", {"command": cmd, "execution_status": status})
                sim.save()
                case = RUN / "cases" / cid
                shutil.copytree(sim.path, case, ignore=shutil.ignore_patterns("*_depth.npy"))
                # Rewrite copied receipts to refer to frozen copies rather than replay files.
                for f in [case / "pending_observation.json", *(case / "observation_receipts").glob("*.json")]:
                    f.write_text(f.read_text().replace(str(sim.path), str(case)))
                manifest["cases"].append({"id": cid, "cluster": "historical", "objective": objective, "body": body})
            if index == 38: break
    finally: sim.close()
    seed = secrets.randbits(63)
    rng = np.random.default_rng(seed)
    setup = {"seed": seed, "layouts": {}}
    for i in range(1, 7):
        cid = f"R{i:02d}"; case = RUN / "cases" / cid
        build_scene(case / "scene.xml")
        tree = ET.parse(case / "scene.xml")
        positions = []
        for name in ("coral", "teal", "gold"):
            for _ in range(10000):
                xy = rng.uniform([.275, .155], [.425, .31])
                if all(np.linalg.norm(xy-p) > .075 for p in positions): break
            else: raise RuntimeError("Layout sampling exhausted")
            positions.append(xy)
            body = tree.find(f".//body[@name='{name}']")
            body.set("pos", f"{xy[0]} {xy[1]} .65")
            angle = rng.uniform(-.45, .45)
            body.set("quat", f"{np.cos(angle/2)} 0 0 {np.sin(angle/2)}")
        tree.write(case / "scene.xml")
        setup["layouts"][cid] = {"positions": [p.tolist() for p in positions], "scene_sha256": sha(case / "scene.xml")}
        sim = RobotSim(case)
        try:
            height = [.80, .74, .70, .80, .74, .70][i-1]
            if height != .8: sim.move([.30, .20, height])
            AgentSession(sim).observe("random_fixture")
        finally: sim.close()
        manifest["cases"].append({"id": cid, "cluster": cid, "objective": "align", "body": "gold"})
    save(RUN / "setup_private.json", setup)
    manifest["setup_sha256"] = sha(RUN / "setup_private.json")
    save(RUN / "manifest.json", manifest)
    print("Created 12 frozen image cases. Coordinates and random seed were not printed.", flush=True)


def freeze():
    if (RUN / "frozen.json").exists(): raise ValueError("Already frozen")
    cases = []
    for item in read(RUN / "manifest.json")["cases"]:
        case = RUN / "cases" / item["id"]
        annotation = read(RUN / "annotations" / f"{item['id']}.json")
        p = annotation["proposal"]; ProposalV2.model_validate(p)
        receipt = read(case / "pending_observation.json")
        if p["observation_id"] != receipt["observation_id"]: raise ValueError("Stale proposal")
        sim = RobotSim(case)
        try:
            history = AgentSession(sim)._history(receipt)
            for oid, camera in [(p["observation_id"], c) for c in p["viewed_cameras"]] + [(r["observation_id"], r["camera"]) for r in p.get("compared_images", [])]:
                r = receipt if oid == receipt["observation_id"] else history[oid]
                if sha(Path(r["images"][camera])) != r["image_sha256"][camera]: raise ValueError("Image hash changed")
            compiled = compile_proposal(p, receipt, history, sim)
        finally: sim.close()
        if annotation["agent_choice"] not in compiled["candidates"]: raise ValueError("Unknown C choice")
        cases.append({**item, "compiled": compiled, "agent_choice": annotation["agent_choice"],
                      "annotation_sha256": sha(RUN / "annotations" / f"{item['id']}.json")})
    save(RUN / "frozen.json", {"frozen_wall_time": time.time(), "manifest_sha256": sha(RUN / "manifest.json"), "cases": cases})
    print("Frozen all visual annotations and Agent-only choices before API calls:", sha(RUN / "frozen.json"))


def flatten(value, path="state"):
    """Lossless leaf paths, preserving false/unknown/empty values and list order."""
    if isinstance(value, dict) and value:
        return [line for k, v in value.items() for line in flatten(v, path + "/" + str(k).replace("~", "~0").replace("/", "~1"))]
    if isinstance(value, list) and value:
        return [line for i, v in enumerate(value) for line in flatten(v, path + "/" + str(i))]
    return [f"The value of {path} is {json.dumps(value, ensure_ascii=False)}."]


def permute(compiled, seed):
    rng = random.Random(seed)
    keys = [k for k in compiled["candidates"] if k != "observe"]
    renamed = keys.copy(); rng.shuffle(renamed)
    mapping = dict(zip(keys, renamed)); mapping["observe"] = "observe"
    # Include references such as candidate.a01.path_clear, not just dictionary keys.
    def remap(x):
        if isinstance(x, dict): return {mapping.get(k, k): remap(v) for k, v in x.items()}
        if isinstance(x, list): return [remap(v) for v in x]
        if isinstance(x, str) and x.startswith("candidate."):
            bits = x.split("."); bits[1] = mapping.get(bits[1], bits[1]); return ".".join(bits)
        return x
    p = remap(copy.deepcopy(compiled))
    order = list(p["candidates"]); rng.shuffle(order)
    p["candidates"] = {k: p["candidates"][k] for k in order}
    p["state"]["candidate_actions"] = p["candidates"]
    return p, {v: k for k, v in mapping.items()}


def accepted(raw_choice, confidence, candidates):
    if confidence < .55: return "observe", "low_confidence"
    if not candidates[raw_choice]["eligible"]: return "observe", "ineligible_action"
    return raw_choice, None


def run():
    frozen = read(RUN / "frozen.json")
    jobs = []
    for i, case in enumerate(frozen["cases"]):
        for repeat in range(3):
            p, reverse = permute(case["compiled"], 9000 + i*10 + repeat)
            groups = ["A", "B"]; random.Random(i*10+repeat).shuffle(groups)
            for group in groups:
                jobs.append({"id": f"{case['id']}_{repeat}_{group}", "case": case["id"], "repeat": repeat, "group": group,
                             "state": p["state"] if group == "B" else " ".join(flatten(p["state"])),
                             "candidates": p["candidates"], "reverse": reverse})
    plan_path = RUN / "request_plan.json"
    if plan_path.exists() and read(plan_path)["frozen_sha256"] != sha(RUN / "frozen.json"):
        raise ValueError("Frozen inputs changed")
    save(plan_path, {"frozen_sha256": sha(RUN / "frozen.json"), "jobs": jobs})
    log = RUN / "api_results.jsonl"
    done = {x["id"] for x in map(json.loads, log.read_text().splitlines())} if log.exists() else set()
    client = JevClient(load_private_key(), model=MODEL)
    for job in jobs:
        if job["id"] in done: continue
        result = {"id": job["id"], "case": job["case"], "repeat": job["repeat"], "group": job["group"], "started_wall_time": time.time(), "attempts": []}
        for attempt in range(2):
            try:
                d = client.choose(job["state"], {k: v["description"] for k, v in job["candidates"].items()})
                if d.model != MODEL: raise DecisionError("Pinned model response mismatch")
                chosen, reason = accepted(d.action, d.confidence, job["candidates"])
                result.update(decision=d.to_dict(), raw_action=job["reverse"][d.action], accepted_action=job["reverse"][chosen], gate_reason=reason,
                              raw_eligible=job["candidates"][d.action]["eligible"], status="ok")
                break
            except DecisionError as exc:
                result["attempts"].append({"error": str(exc), "wall_time": time.time()})
        else: result.update(status="failed", accepted_action=None)
        result["completed_wall_time"] = time.time()
        with log.open("a") as f: f.write(json.dumps(result, ensure_ascii=False)+"\n")
        done.add(job["id"])
        if len(done) % 6 == 0: print(f"Completed {len(done)}/72 calls", flush=True)


def score():
    frozen = read(RUN / "frozen.json")
    results = [json.loads(x) for x in (RUN / "api_results.jsonl").read_text().splitlines()]
    if len(results) != 72: raise ValueError("Complete the fixed request plan first")
    scores = []
    for case in frozen["cases"]:
        candidates = case["compiled"]["candidates"]
        cc, cg = accepted(case["agent_choice"], 1., candidates)
        selected = {r["accepted_action"] for r in results if r["case"] == case["id"] and r["accepted_action"]} | {cc}
        for action in sorted(selected):
            dest = RUN / "scoring" / case["id"] / action
            if dest.exists(): raise ValueError("Scoring output already exists; do not overwrite evidence")
            dest.mkdir(parents=True)
            for name in ("scene.xml", "state.npz"): shutil.copyfile(RUN / "cases" / case["id"] / name, dest / name)
            sim = RobotSim(dest)
            body = case["body"]
            p0 = sim.data.body(body).xpos.copy(); tcp0 = sim.tcp()
            was_closed = abs(float(sim.data.ctrl[sim.model.actuator("l-joint8").id])) > .01
            cmd = candidates[action]["command"]
            status, error = "executed", None
            try:
                try: execute(sim, cmd)
                except Exception as exc: status, error = "execution_failed", str(exc)
                p1 = sim.data.body(body).xpos.copy(); tcp1 = sim.tcp()
                report = sim.oracle(); obj = report["objects"][body]
                closed = abs(float(sim.data.ctrl[sim.model.actuator("l-joint8").id])) > .01
                pads = {n for pair in sim.contacts() if any(n.startswith(body+"_") for n in pair) for n in pair}
                retained = all(f"l_pad{i}" in pads for i in (8, 9))
                delta = p1-p0; tcp_delta = tcp1-tcp0
                objective = case["objective"]
                if objective == "recover_open": success = was_closed and not closed and np.linalg.norm(delta) < .01
                elif objective == "retained_lift": success = delta[2] >= .025 and np.linalg.norm(delta-tcp_delta) < .015 and retained
                elif objective == "release_in_tray": success = not closed and obj["in_tray"] and obj["supported_by_tray"]
                elif objective == "retreat": success = tcp_delta[2] >= .03 and obj["in_tray"] and obj["supported_by_tray"] and not obj["touching_gripper"]
                elif objective == "finish": success = cmd["op"] == "finish" and report["success"]
                elif objective == "align": success = np.linalg.norm(tcp0[:2]-p0[:2])-np.linalg.norm(tcp1[:2]-p1[:2]) >= .002 and np.linalg.norm(delta) < .005 and not obj["touching_gripper"]
                else: raise ValueError(objective)
                sim.observe("scored")
                scores.append({"case": case["id"], "action": action, "objective": objective, "success": bool(success and status == "executed"),
                               "status": status, "error": error, "object_displacement_m": float(np.linalg.norm(delta)), "object_lift_m": float(delta[2]),
                               "tcp_lift_m": float(tcp_delta[2]), "held_by_both_pads": retained,
                               "alignment_improvement_m": float(np.linalg.norm(tcp0[:2]-p0[:2])-np.linalg.norm(tcp1[:2]-p1[:2]))})
            finally: sim.close()
    save(RUN / "physical_scores.json", {"frozen_sha256": sha(RUN / "frozen.json"), "scored_after_api_sha256": sha(RUN / "api_results.jsonl"), "scores": scores})
    print("Completed independent one-action physical scoring; no evaluator data was sent to decisions.")


def analyze():
    frozen = read(RUN / "frozen.json")
    rows = [json.loads(x) for x in (RUN / "api_results.jsonl").read_text().splitlines()]
    scores = {(x["case"], x["action"]): int(x["success"]) for x in read(RUN / "physical_scores.json")["scores"]}
    table = []
    for c in frozen["cases"]:
        entry = {"case": c["id"], "cluster": c["cluster"], "objective": c["objective"]}
        for group in ("A", "B"):
            rs = [r for r in rows if r["case"] == c["id"] and r["group"] == group]
            entry[group] = sum(scores.get((c["id"], r["accepted_action"]), 0) for r in rs) / 3
            entry[group+"_choices"] = [r["accepted_action"] for r in rs]
        chosen, reason = accepted(c["agent_choice"], 1., c["compiled"]["candidates"])
        entry.update(C=scores[(c["id"], chosen)], C_choice=c["agent_choice"], C_accepted=chosen, C_gate=reason)
        table.append(entry)
    clusters = sorted({r["cluster"] for r in table})
    differences = [np.array([r["B"]-r["A"] for r in table if r["cluster"] == key]) for key in clusters]
    rng = np.random.default_rng(20260923)
    boot = [float(np.concatenate([differences[i] for i in rng.integers(0, len(clusters), len(clusters))]).mean()) for _ in range(10000)]
    signs = [float(d.mean()) for d in differences]
    wins, losses = sum(d > 1e-9 for d in signs), sum(d < -1e-9 for d in signs)
    n = wins+losses
    pvalue = min(1., 2*sum(math.comb(n, k) for k in range(min(wins, losses)+1))/2**n) if n else 1.
    summary = {"states": len(table), "layout_clusters": len(clusters), "API_calls": len(rows), "table": table,
               "progress_rate": {g: float(np.mean([r[g] for r in table])) for g in ("A", "B", "C")},
               "B_minus_A": float(np.mean([r["B"]-r["A"] for r in table])), "cluster_bootstrap_95_interval": np.quantile(boot, [.025, .975]).tolist(),
               "cluster_sign_test": {"B_wins": wins, "A_wins": losses, "two_sided_p": pvalue}, "groups": {}}
    for g in ("A", "B"):
        rs = [r for r in rows if r["group"] == g]; ok = [r for r in rs if r["status"] == "ok"]
        summary["groups"][g] = {"completed": len(ok), "errors": len(rs)-len(ok), "raw_ineligible": sum(not r["raw_eligible"] for r in ok),
                                "gated": sum(r["gate_reason"] is not None for r in ok), "observe": sum(r["accepted_action"] == "observe" for r in ok),
                                "mean_input_tokens": float(np.mean([r["decision"]["usage"].get("input_tokens", 0) for r in ok])) if ok else None,
                                "mean_latency_ms": float(np.mean([r["decision"]["latency_ms"] for r in ok])) if ok else None,
                                "order_sensitive_states": sum(len(set(r[g+"_choices"])) > 1 for r in table)}
    summary["API_experiment_wall_seconds"] = max(r["completed_wall_time"] for r in rows)-min(r["started_wall_time"] for r in rows)
    save(RUN / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("mode", choices=["build", "freeze", "run", "score", "analyze"])
    globals()[parser.parse_args().mode]()
