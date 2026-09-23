"""One observation -> one agent-authored proposal -> one Jev-selected action.

No detector, grasp generator, object coordinates, stage machine, or GPT API.
The calling conversation must actually inspect images and supply each proposal.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import imageio.v2 as imageio
from .actions import execute
from .decision import JevClient, DecisionError, load_private_key, decide_proposal
from .protocol import ProposalV2, compile_proposal


class AgentSession:
    def __init__(self, sim):
        self.sim = sim
        self.path = sim.path
        self.receipt_path = self.path / "pending_observation.json"

    def observe(self, label="agent", last_action=None):
        previous = json.loads(self.receipt_path.read_text()) if self.receipt_path.exists() else None
        if last_action is None and previous:
            last_action = previous.get("last_action")
        oid = uuid.uuid4().hex[:12]
        observation = self.sim.observe(f"{label}_{oid}")
        grippers = {}
        for arm in ("l", "r"):
            controls = [float(self.sim.data.ctrl[self.sim.model.actuator(f"{arm}-joint{i}").id]) for i in (8, 9)]
            pads = {name for pair in observation["contacts"] for name in pair}
            grippers[arm] = {"commanded_closed": any(abs(c) > .01 for c in controls),
                             "both_pad_contacts": all(f"{arm}_pad{i}" in pads for i in (8, 9))}
        receipt = {"observation_id": oid, "simulation_time": self.sim.data.time, "capture_wall_time": time.time(),
                   "previous_observation_id": previous["observation_id"] if previous else None,
                   "last_action": last_action,
                   "images": observation["images"],
                   "image_sha256": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() for k,v in observation["images"].items()},
                   "robot_feedback": {"tcp_world_m": observation["tcp"], "contact_pairs": observation["contacts"], "contact_source": "simulated finger contact sensor", "grippers": grippers},
                   "consumed": False}
        self.receipt_path.write_text(json.dumps(receipt, indent=2))
        archive = self.path / "observation_receipts"
        archive.mkdir(exist_ok=True)
        (archive / f"{oid}.json").write_text(json.dumps(receipt, indent=2))
        return receipt

    def step(self, proposal, client=None):
        received_at = time.time()
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
        version = proposal.get("schema_version", "1.0")
        if version == "2.0":
            parsed = ProposalV2.model_validate(proposal)
            history = self._history(receipt)
            for ref in parsed.compared_images:
                old = history.get(ref.observation_id)
                if old is None or ref.camera not in old["images"]:
                    raise ValueError("Compared image must reference a recent archived observation")
                if hashlib.sha256(Path(old["images"][ref.camera]).read_bytes()).hexdigest() != old["image_sha256"][ref.camera]:
                    raise ValueError("Historical image changed since capture")
            api_proposal = compile_proposal(proposal, receipt, history, self.sim)
            candidates = api_proposal["candidates"]
        elif version == "1.0":
            for field in ("visual_assessment", "decision_summary"):
                if not isinstance(proposal.get(field), str) or len(proposal[field].strip()) < 15:
                    raise ValueError(f"Provide an agent-authored {field}")
            candidates = proposal.get("candidates", {})
            if len(candidates) < 2 or candidates.get("observe", {}).get("command", {}).get("op") != "observe":
                raise ValueError("Supply at least two candidates including an observe-only fallback")
            # Historical V1 compatibility. V2 never forwards a recommended action.
            state = {"schema_version": "1.0", "goal": "Put the tabletop objects in the tray", "simulation_time": receipt["simulation_time"],
                     "observation_id": receipt["observation_id"], "robot_feedback": receipt["robot_feedback"],
                     "visual_assessment": proposal["visual_assessment"], "decision_summary": proposal["decision_summary"],
                     "policy_source": "live_conversation_agent_visual_judgment", "viewed_cameras": viewed}
            api_proposal = {"state": state, "candidates": candidates}
        else:
            raise ValueError("Unsupported proposal schema_version")
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
        outcome = {"schema_version": version, "proposal": proposal, "decision": record["decision"], "accepted_action": chosen, "gate_reason": record["gate_reason"], "observation_id": receipt["observation_id"],
                   "timing": {"proposal_received_wall_time": received_at, "observation_to_submission_s": received_at - receipt["capture_wall_time"] if "capture_wall_time" in receipt else None}}
        try:
            outcome["result"] = execute(self.sim, candidates[chosen]["command"])
            outcome["status"] = "executed"
        except Exception as exc:
            outcome.update(status="execution_failed", error=str(exc))
        finally:
            self.sim.writer.close(); self.sim.writer = None
            self.sim.save()
        last_action = {"action": chosen, "command": candidates[chosen]["command"], "execution_status": outcome["status"],
                       "from_observation_id": receipt["observation_id"], "completed_simulation_time": self.sim.data.time}
        if version == "2.0":
            last_action.update(target_id=proposal["target_id"], expected_observation=candidates[chosen]["expected_observation"], failure_signals=candidates[chosen]["failure_signals"])
        outcome["next_observation"] = self.observe("feedback", last_action)
        outcome["timing"]["step_wall_time_s"] = time.time() - received_at
        with (self.path / "agent_steps.jsonl").open("a") as f:
            f.write(json.dumps(outcome, ensure_ascii=False, allow_nan=False)+"\n")
        with (self.path / "decision_summary.md").open("a") as f:
            assessment = proposal.get("visual_assessment", json.dumps(proposal.get("visual_facts", []), ensure_ascii=False))
            note = proposal.get("audit_note", proposal.get("decision_summary", ""))
            f.write(f"\n## {receipt['observation_id']} → {chosen} (schema {version})\n\n视觉判断：{assessment}\n\n任务级记录（V2 不发送给 Jev）：{note}\n\nJev confidence={record['decision']['confidence']}；结果={outcome['status']}；门控={record['gate_reason']}。\n")
        return outcome

    def _history(self, receipt):
        """At most eight linked receipts; no object truth or arbitrary path reads."""
        history = {}
        oid = receipt.get("previous_observation_id")
        for _ in range(8):
            if not oid or not isinstance(oid, str) or len(oid) != 12 or any(c not in "0123456789abcdef" for c in oid):
                break
            path = self.path / "observation_receipts" / f"{oid}.json"
            if not path.exists(): break
            old = json.loads(path.read_text())
            history[oid] = old
            oid = old.get("previous_observation_id")
        return history

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
