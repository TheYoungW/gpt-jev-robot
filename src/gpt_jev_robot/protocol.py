"""V2 visual evidence contract. No image detector or automatic grasp policy.

The schema validates statements and their provenance, not their visual truth.
Only normalized facts and neutral action records are sent to Jev.
"""
import json
import math
from typing import Annotated, Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from .sim import MotionError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


ObjectId = Annotated[str, Field(pattern=r"^object_[0-9]{2,4}$")]
Truth = Literal["yes", "no", "unknown"]
Relation = Literal["visible", "between_fingers", "finger_side_overlap", "moves_with_gripper",
                   "inside_tray", "resting_on_surface"]
RELATIONS = ("visible", "between_fingers", "finger_side_overlap", "moves_with_gripper",
             "inside_tray", "resting_on_surface")


class ImageRef(StrictModel):
    observation_id: str
    camera: Literal["center", "l_wrist", "r_wrist"]


class VisibleObject(StrictModel):
    object_id: ObjectId
    appearance: Annotated[str, Field(min_length=3, max_length=160)]
    visibility: Literal["clear", "partial", "occluded", "unknown"]


class EvidenceAssessment(StrictModel):
    value: Truth
    source: Literal["agent_visual_assessment"] = "agent_visual_assessment"
    evidence: list[ImageRef] = Field(max_length=6)
    reason: Annotated[str, Field(max_length=240)] = ""

    @model_validator(mode="after")
    def evidence_or_uncertainty(self):
        if self.value == "unknown" and not self.reason.strip():
            raise ValueError("Unknown facts require a reason")
        if self.value != "unknown" and not self.evidence:
            raise ValueError("Known facts require image evidence")
        return self


class VisualFact(EvidenceAssessment):
    subject: ObjectId
    relation: Relation


class Move(StrictModel):
    op: Literal["move"]
    position: list[FiniteFloat] = Field(min_length=3, max_length=3)
    arm: Literal["l", "r"] = "l"
    yaw: FiniteFloat = math.pi / 2


class Nudge(StrictModel):
    op: Literal["nudge"]
    direction: Literal["up", "down", "forward", "back", "left", "right"]
    distance: Annotated[FiniteFloat, Field(gt=0, le=.05)]
    arm: Literal["l", "r"] = "l"


class Gripper(StrictModel):
    op: Literal["gripper"]
    closed: bool
    arm: Literal["l", "r"] = "l"


class Observe(StrictModel):
    op: Literal["observe"]
    label: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")] = "recheck"


class Wait(StrictModel):
    op: Literal["wait"]
    seconds: Annotated[FiniteFloat, Field(gt=0, le=3)]


class Finish(StrictModel):
    op: Literal["finish"]


Command = Annotated[Move | Nudge | Gripper | Observe | Wait | Finish, Field(discriminator="op")]


class Requirement(StrictModel):
    fact: str
    equals: Literal["yes", "no"]


class OutcomeCheck(StrictModel):
    relation: Relation
    value: Literal["yes", "no"]


class Candidate(StrictModel):
    intent: Literal["observe", "approach", "align", "close", "test_lift", "transport",
                    "lower", "release", "retreat", "wait", "finish"]
    command: Command
    requires: list[Requirement] = Field(max_length=12)
    expected_observation: list[OutcomeCheck] = Field(min_length=1, max_length=7)
    failure_signals: list[OutcomeCheck] = Field(min_length=1, max_length=7)
    clearance: EvidenceAssessment | None = None

    @model_validator(mode="after")
    def matching_intent(self):
        op = self.command.op
        allowed = {"observe": {"observe"}, "approach": {"move", "nudge"},
                   "align": {"move", "nudge"}, "close": {"gripper"},
                   "test_lift": {"nudge"}, "transport": {"move", "nudge"},
                   "lower": {"nudge"}, "release": {"gripper"},
                   "retreat": {"move", "nudge"}, "wait": {"wait"}, "finish": {"finish"}}
        if op not in allowed[self.intent]:
            raise ValueError("Intent does not match command")
        if op in ("move", "nudge") and self.clearance is None:
            raise ValueError("Each movement requires its own visual clearance assessment")
        if op not in ("move", "nudge") and self.clearance is not None:
            raise ValueError("Clearance is only used for movement candidates")
        if self.intent in ("close", "release") and self.command.closed != (self.intent == "close"):
            raise ValueError("Intent does not match gripper command")
        if self.intent in ("test_lift", "lower") and self.command.direction != {"test_lift": "up", "lower": "down"}[self.intent]:
            raise ValueError("Intent does not match nudge direction")
        if self.intent == "observe" and self.requires:
            raise ValueError("Observe fallback must have no requirements")
        if {x.model_dump_json() for x in self.expected_observation} & {x.model_dump_json() for x in self.failure_signals}:
            raise ValueError("Expected observations and failure signals must differ")
        return self


class ProposalV2(StrictModel):
    schema_version: Literal["2.0"]
    observation_id: str
    viewed_cameras: list[Literal["center", "l_wrist", "r_wrist"]] = Field(min_length=1, max_length=3)
    compared_images: list[ImageRef] = Field(default_factory=list, max_length=12)
    target_id: ObjectId
    working_arm: Literal["l", "r"] = "l"
    objects: list[VisibleObject] = Field(min_length=1, max_length=32)
    visual_facts: list[VisualFact] = Field(min_length=6, max_length=192)
    candidates: dict[Annotated[str, Field(pattern=r"^(observe|a[0-9]{2})$")], Candidate] = Field(min_length=2, max_length=8)
    # Audit-only: never included in the API state, criteria, or candidate text.
    audit_note: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def complete_facts(self):
        ids = [x.object_id for x in self.objects]
        if len(set(ids)) != len(ids) or self.target_id not in ids:
            raise ValueError("Object IDs must be unique and include target_id")
        keys = [(f.subject, f.relation) for f in self.visual_facts]
        if len(set(keys)) != len(keys) or any(f.subject not in ids for f in self.visual_facts):
            raise ValueError("Facts must have unique subject/relation pairs and known subjects")
        if any((self.target_id, r) not in keys for r in RELATIONS):
            raise ValueError("Every target relation is required; use unknown rather than omitting it")
        if "observe" not in self.candidates or self.candidates["observe"].intent != "observe":
            raise ValueError("An observe fallback is required")
        if any(k != "observe" and c.intent == "observe" for k, c in self.candidates.items()):
            raise ValueError("Observe must use the reserved observe ID")
        return self


def template(receipt):
    """A deliberately unfilled form, never an automatic visual assessment."""
    check = lambda relation, value: {"relation": relation, "value": value}
    return {"schema_version": "2.0", "observation_id": receipt["observation_id"],
            "viewed_cameras": [], "compared_images": [], "target_id": "object_01",
            "objects": [{"object_id": "object_01", "appearance": "REPLACE after viewing images", "visibility": "unknown"}],
            "visual_facts": [{"subject": "object_01", "relation": r, "value": "unknown", "evidence": [], "reason": "Not yet assessed from images"} for r in RELATIONS],
            "candidates": {
                "observe": {"intent": "observe", "command": {"op": "observe"}, "requires": [],
                            "expected_observation": [check("visible", "yes")], "failure_signals": [check("visible", "no")]},
                "a01": {"intent": "wait", "command": {"op": "wait", "seconds": .5},
                        "requires": [{"fact": "object_01.visible", "equals": "yes"}],
                        "expected_observation": [check("resting_on_surface", "yes")], "failure_signals": [check("visible", "no")]}}
            }


def compile_proposal(raw, receipt, history, sim):
    """Normalize V2 and compute eligibility locally; never trust supplied status."""
    p = ProposalV2.model_validate(raw)
    current = receipt["observation_id"]
    viewed = {(current, c) for c in p.viewed_cameras}
    viewed.update((r.observation_id, r.camera) for r in p.compared_images)
    for ref in p.compared_images:
        if ref.observation_id not in history:
            raise ValueError("Compared image must reference a recent archived observation")
    assessments = list(p.visual_facts) + [c.clearance for c in p.candidates.values() if c.clearance is not None]
    for f in assessments:
        refs = {(r.observation_id, r.camera) for r in f.evidence}
        if not refs <= viewed:
            raise ValueError("Fact cites an image not declared as viewed")
        if f.value != "unknown" and not any(oid == current for oid, _ in refs):
            raise ValueError("Known visual facts require current image evidence")
        if getattr(f, "relation", None) == "moves_with_gripper" and f.value != "unknown":
            prior = [history[oid] for oid, _ in refs if oid in history and history[oid]["simulation_time"] < receipt["simulation_time"]]
            arm = working_arm(p)
            if not any(np.linalg.norm(np.array(o["robot_feedback"]["tcp_world_m"][arm]) - receipt["robot_feedback"]["tcp_world_m"][arm]) > .005 for o in prior):
                raise ValueError("moves_with_gripper needs before/after images across >5 mm TCP motion")
    facts = {f"{f.subject}.{f.relation}": f.value for f in p.visual_facts}
    for arm, feedback in receipt["robot_feedback"].get("grippers", {}).items():
        for name in ("both_pad_contacts", "commanded_closed"):
            facts[f"robot.{arm}.{name}"] = "yes" if feedback[name] else "no"
    normalized = {}
    for key, c in p.candidates.items():
        cmd = c.command.model_dump()
        arm = cmd.get("arm", working_arm(p))
        requirements = {(r.fact, r.equals) for r in c.requires}
        required = []
        if cmd["op"] in ("move", "nudge"):
            facts[f"candidate.{key}.path_clear"] = c.clearance.value
            required.append((f"candidate.{key}.path_clear", "yes"))
        if c.intent == "close":
            required += [(f"{p.target_id}.{r}", "yes") for r in ("between_fingers", "finger_side_overlap")]
        if c.intent in ("test_lift", "transport", "lower"):
            required += [(f"robot.{arm}.both_pad_contacts", "yes"), (f"{p.target_id}.between_fingers", "yes")]
        if c.intent == "transport":
            required.append((f"{p.target_id}.moves_with_gripper", "yes"))
        if c.intent == "release":
            required.append((f"{p.target_id}.inside_tray", "yes"))
        if c.intent == "finish":
            # All declared objects, not an oracle-derived list of scene contents.
            required += [(f"{o.object_id}.{r}", v) for o in p.objects for r, v in
                         (("inside_tray", "yes"), ("resting_on_surface", "yes"), ("between_fingers", "no"))]
        requirements.update(required)
        checks = []
        for fact, expected in sorted(requirements):
            if fact not in facts:
                raise ValueError(f"Missing required fact: {fact}")
            actual = facts[fact]
            checks.append({"fact": fact, "equals": expected, "observed": actual,
                           "status": "unknown" if actual == "unknown" else "met" if actual == expected else "unmet"})
        preflight = {"status": "passed", "scope": "command bounds and IK; not collision or grasp success"}
        try:
            if cmd["op"] == "move":
                sim.plan_move(cmd["position"], arm, cmd["yaw"])
            elif cmd["op"] == "nudge":
                axes = {"up": [0,0,1], "down": [0,0,-1], "forward": [1,0,0], "back": [-1,0,0], "left": [0,1,0], "right": [0,-1,0]}
                sim.plan_move(sim.tcp(arm) + cmd["distance"] * np.array(axes[cmd["direction"]]), arm)
        except MotionError as exc:
            preflight.update(status="failed", reason=str(exc))
        normalized[key] = {"intent": c.intent, "command": cmd,
                           "description": f"{c.intent}: " + json.dumps(cmd, sort_keys=True),
                           "requires": checks, "preflight": preflight,
                           "clearance": c.clearance.model_dump() if c.clearance is not None else None,
                           "eligible": preflight["status"] == "passed" and all(r["status"] == "met" for r in checks),
                           "expected_observation": [x.model_dump() for x in c.expected_observation],
                           "failure_signals": [x.model_dump() for x in c.failure_signals]}
    previous = receipt.get("last_action") or {}
    review = {}
    for group in ("expected_observation", "failure_signals"):
        review[group] = []
        for check in previous.get(group, []):
            value = facts.get(f"{previous.get('target_id')}.{check['relation']}", "unknown")
            review[group].append({**check, "observed": value,
                                  "match": "unknown" if value == "unknown" else "yes" if value == check["value"] else "no"})
    state = {"schema_version": "2.0", "goal": "Put all tabletop objects in the tray",
             "policy_source": "live_conversation_agent_visual_judgment",
             "observation_id": current, "simulation_time": receipt["simulation_time"],
             "coordinates": {"frame": "world", "distance_unit": "meter", "angle_unit": "radian"},
             "working_arm": working_arm(p), "target_id": p.target_id,
             "objects": [o.model_dump() for o in p.objects],
             "visual_facts": [f.model_dump() for f in p.visual_facts],
             "robot_feedback": receipt["robot_feedback"], "last_action": receipt.get("last_action"),
             "previous_action_review": review,
             "candidate_actions": normalized,
             "evidence_limits": "Visual statements are agent assessments, not ground truth. Unknown is not no. Contact is not proof of retention."}
    return {"state": state, "candidates": normalized}


def working_arm(proposal):
    arms = {c.command.arm for c in proposal.candidates.values() if hasattr(c.command, "arm")}
    if arms - {proposal.working_arm}:
        raise ValueError("One V2 proposal must use one working arm")
    return proposal.working_arm
