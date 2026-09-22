import json
import httpx
import pytest
from gpt_jev_robot.decision import JevClient, DecisionError, parse_decision, decide_proposal

CANDIDATES = {"lift": "Lift held object", "observe": "Inspect uncertain state"}


def response(choice="lift", confidence=.9):
    return {"model": "jev-test", "answers": {"next_action": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {"lift": .95, "observe": .05}}}}


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(choice="run_shell"),
    lambda a: a.update(confidence=float("nan")),
    lambda a: a.update(probabilities={"lift": 1.5, "observe": -.5}),
    lambda a: a.update(probabilities={"lift": .1, "observe": .1}),
    lambda a: a.update(choice="observe"),
])
def test_malformed_decision_cannot_cross_boundary(mutation):
    body = response(); mutation(body["answers"]["next_action"])
    with pytest.raises(DecisionError): parse_decision(body, CANDIDATES)


def test_official_endpoint_and_payload():
    def handler(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["questions"]["next_action"]["criteria"] == CANDIDATES
        return httpx.Response(200, json=response())
    d = JevClient("test-only", transport=httpx.MockTransport(handler)).choose({"held": True}, CANDIDATES)
    assert d.action == "lift"


def test_low_confidence_records_observation_without_faking_choice(tmp_path):
    client = JevClient("test-only", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(confidence=.1))))
    proposal = {"state": {"held": True}, "candidates": {k:{"description":v} for k,v in CANDIDATES.items()}}
    action, record = decide_proposal(proposal, client, tmp_path / "decisions.jsonl")
    assert action == "observe"
    assert record["decision"]["action"] == "lift"
    assert "test-only" not in (tmp_path / "decisions.jsonl").read_text()


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_http_failure_never_falls_back(status):
    client = JevClient("test-only", transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(DecisionError): client.choose({}, CANDIDATES)
