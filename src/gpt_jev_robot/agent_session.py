"""One observation -> one agent-authored proposal -> one Jev-selected action.

No detector, grasp generator, object coordinates, stage machine, or GPT API.
The calling conversation must actually inspect images and supply each proposal.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import imageio.v2 as imageio
from .actions import execute
from .decision import JevClient, DecisionError, load_private_key, decide_proposal


class AgentSession:
    def __init__(self, sim):
        self.sim = sim
        self.path = sim.path
        self.receipt_path = self.path / "pending_observation.json"

    def observe(self, label="agent"):
        oid = uuid.uuid4().hex[:12]
        observation = self.sim.observe(f"{label}_{oid}")
        receipt = {"observation_id": oid, "simulation_time": self.sim.data.time,
                   "images": observation["images"],
                   "image_sha256": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in observation["images"].items()},
                   "robot_feedback": {"tcp_world_m": observation["tcp"], "contact_pairs": observation["contacts"], "contact_source": "simulated finger contact sensor"},
                   "consumed": False}
        self.receipt_path.write_text(json.dumps(receipt, indent=2))
        return receipt

    def step(self, proposal, client=None):
        if self.sim.writer is not None:
            raise ValueError("Agent step owns recording; do not combine with serve --video")
        if not self.receipt_path.exists():
            raise ValueError("Observe and inspect the camera images first")
        receipt = json.loads(self.receipt_path.read_text())
        if receipt["consumed"] or proposal.get("observation_id") != receipt["observation_id"]:
            raise ValueError("Proposal must reference the latest unused observation")
        if abs(receipt["simulation_time"]-self.sim.data.time) > 1e-6:
            raise ValueError("Robot moved since observation; inspect a fresh observation")
        viewed = proposal.get("viewed_cameras", [])
        if not isinstance(viewed, list) or not viewed or any(k not in receipt["images"] for k in viewed):
            raise ValueError("List the camera images actually inspected")
        for k in viewed:
            if hashlib.sha256(Path(receipt["images"][k]).read_bytes()).hexdigest() != receipt["image_sha256"][k]:
                raise ValueError("Observation image changed since capture")
        for field in ("visual_assessment", "decision_summary"):
            if not isinstance(proposal.get(field), str) or len(proposal[field].strip()) < 15:
                raise ValueError(f"Provide an agent-authored {field}")
        candidates = proposal.get("candidates", {})
        if len(candidates) < 2 or candidates.get("observe", {}).get("command", {}).get("op") != "observe":
            raise ValueError("Supply at least two candidates including an observe-only fallback")
        # The wrapper adds proprioception, never object poses or detector output.
        state = {"goal": "Put the tabletop objects in the tray", "simulation_time": receipt["simulation_time"],
                 "observation_id": receipt["observation_id"], "robot_feedback": receipt["robot_feedback"],
                 "visual_assessment": proposal["visual_assessment"], "decision_summary": proposal["decision_summary"],
                 "policy_source": "live_conversation_agent_visual_judgment", "viewed_cameras": viewed}
        api_proposal = {"state": state, "candidates": candidates}
        try:
            chosen, record = decide_proposal(api_proposal, client or JevClient(load_private_key()), self.path / "decisions.jsonl")
        except DecisionError as exc:
            self.sim.event("agent_decision_failed", observation_id=receipt["observation_id"], message=str(exc), motion_executed=False)
            raise
        receipt["consumed"] = True
        self.receipt_path.write_text(json.dumps(receipt, indent=2))
        self.sim.label = f"Live visual agent / Jev: {chosen}"
        segment_dir = self.path / "video_segments"
        segment_dir.mkdir(exist_ok=True)
        self.sim.steps = 0
        self.sim.writer = imageio.get_writer(str(segment_dir / f"{len(list(segment_dir.glob('*.mp4'))):04d}.mp4"), fps=30, codec="libx264", quality=8, macro_block_size=1)
        outcome = {"proposal": proposal, "decision": record["decision"], "accepted_action": chosen, "observation_id": receipt["observation_id"]}
        try:
            outcome["result"] = execute(self.sim, candidates[chosen]["command"])
            outcome["status"] = "executed"
        except Exception as exc:
            outcome.update(status="execution_failed", error=str(exc))
        finally:
            self.sim.writer.close(); self.sim.writer = None
            self.sim.save()
        outcome["next_observation"] = self.observe("feedback")
        with (self.path / "agent_steps.jsonl").open("a") as f:
            f.write(json.dumps(outcome, ensure_ascii=False, allow_nan=False)+"\n")
        with (self.path / "decision_summary.md").open("a") as f:
            f.write(f"\n## {receipt['observation_id']} → {chosen}\n\n视觉判断：{proposal['visual_assessment']}\n\n任务级依据：{proposal['decision_summary']}\n\nJev confidence={record['decision']['confidence']}；结果={outcome['status']}。\n")
        return outcome

    def export_video(self):
        segments = [p for p in sorted((self.path / "video_segments").glob("*.mp4")) if p.stat().st_size > 100]
        if not segments: return None
        # Names are internal numeric filenames, not user-provided shell text.
        listing = self.path / "video_segments" / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in segments))
        output = self.path / "episode_4x.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "1", "-i", str(listing), "-c", "copy", str(output)], check=True)
        return str(output)

    def evaluate(self):
        """Explicit final evaluator; no evaluator state is supplied to decisions."""
        result = self.sim.oracle()
        result.update(mode="live_conversation_agent_visual_judgment", simulation_time=self.sim.data.time, policy="No detector, no fixed grasp sequence; each proposal authored after image inspection", video=self.export_video())
        (self.path / "report.json").write_text(json.dumps(result, indent=2))
        receipt = json.loads(self.receipt_path.read_text())
        for camera, path in receipt["images"].items():
            shutil.copyfile(path, self.path / f"after_{camera}.png")
        return result
