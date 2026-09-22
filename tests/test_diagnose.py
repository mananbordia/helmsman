"""Contracts for the vision fallback; no provider calls."""

import json

import pytest

from helmsman import diagnose


def incident():
    return {
        "block_reason": {"code": "model_blocked"},
        "url": "https://example.com/pay",
        "title": "Pay",
        "text": "Sign in to continue",
        "screenshot": "anBlZy1ieXRlcw==",
        "elapsed_ms": 4200,
        "steps": [
            {"step": 1, "action": "Open result", "kind": "click", "operation": "CLICK", "page_changed": True},
            {"step": 2, "action": "EasyPay Number", "kind": "click", "operation": "CLICK", "page_changed": False},
        ],
    }


def answering(answer):
    def post(_url, _key, _body):
        return answer
    return post


# ------------------------------------------------------------------ the answer


def test_a_valid_diagnosis_is_kept():
    result = diagnose.diagnose(
        incident(), "Find the bill",
        env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "authentication", "route": "jev",
                        "message": "Sign in first, then reopen EasyPay", "confidence": 0.8}),
    )

    assert result["route"] == "jev"
    assert result["layer"] == "authentication"
    assert result["message"] == "Sign in first, then reopen EasyPay"
    assert result["confidence"] == 0.8


def test_an_unknown_route_is_an_abstain():
    # The fallback must not be able to invent a third behaviour.
    result = diagnose.diagnose(
        incident(), "goal", env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "action", "route": "act_directly", "confidence": 0.9}),
    )

    assert result["route"] == "abstain"
    assert "unsupported route" in result["reason"]


def test_a_message_that_was_promised_but_not_given_is_an_abstain():
    result = diagnose.diagnose(
        incident(), "goal", env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "action", "route": "jev", "message": "   ", "confidence": 0.5}),
    )

    assert result["route"] == "abstain"


def test_a_request_for_the_human_without_a_description_is_an_abstain():
    result = diagnose.diagnose(
        incident(), "goal", env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "authentication", "route": "human", "needs": "", "confidence": 0.6}),
    )

    assert result["route"] == "abstain"


@pytest.mark.parametrize("confidence", [None, "high", 1.5, -0.2])
def test_a_bad_confidence_is_an_abstain(confidence):
    result = diagnose.diagnose(
        incident(), "goal", env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "action", "route": "jev", "message": "try again",
                        "confidence": confidence}),
    )

    assert result["route"] == "abstain"


def test_an_overlong_message_is_an_abstain():
    result = diagnose.diagnose(
        incident(), "goal", env={"GEMINI_API_KEY": "test"},
        post=answering({"layer": "action", "route": "jev", "message": "x" * 601, "confidence": 0.7}),
    )

    assert result["route"] == "abstain"


# ----------------------------------------------------------------- the request


def test_the_request_carries_the_picture_and_the_evidence():
    seen = {}

    def post(_url, _key, body):
        seen.update(body)
        return {"layer": "action", "route": "abstain", "confidence": 0.1}

    diagnose.diagnose(incident(), "Find the bill", env={"GEMINI_API_KEY": "test"}, post=post)

    parts = seen["contents"][0]["parts"]
    assert parts[1]["inline_data"] == {"mime_type": "image/jpeg", "data": "anBlZy1ieXRlcw=="}
    prompt = parts[0]["text"]
    assert "Find the bill" in prompt
    assert "model_blocked" in prompt
    assert "https://example.com/pay" in prompt
    assert "Sign in to continue" in prompt
    assert "step 2" in prompt and "page unchanged" in prompt
    assert seen["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["generationConfig"]["responseSchema"] is diagnose.SCHEMA


def test_a_page_without_a_picture_still_asks():
    seen = {}
    broken = {**incident(), "screenshot": None}

    def post(_url, _key, body):
        seen.update(body)
        return {"layer": "unknown", "route": "abstain", "confidence": 0.0}

    diagnose.diagnose(broken, "goal", env={"GEMINI_API_KEY": "test"}, post=post)

    assert len(seen["contents"][0]["parts"]) == 1


# ---------------------------------------------------------------- the provider


def test_no_configured_provider_refuses_with_instructions():
    with pytest.raises(diagnose.DiagnosisUnavailable, match="GEMINI_API_KEY"):
        diagnose.diagnose(incident(), "goal", env={})
    assert diagnose.configured({}) is False
    assert diagnose.configured({"GEMINI_API_KEY": "k"}) is True


def test_the_answer_is_read_from_the_provider_envelope():
    payload = {"candidates": [{"content": {"parts": [
        {"text": json.dumps({"layer": "session", "route": "human", "needs": "the account email",
                             "confidence": 0.7})}
    ]}}]}

    result = diagnose._answer_from(payload)

    assert result["route"] == "human"
    assert result["needs"] == "the account email"


def test_an_envelope_without_an_answer_is_an_abstain():
    assert diagnose._answer_from({"candidates": []})["route"] == "abstain"
    assert diagnose._answer_from({"candidates": [{"content": {"parts": []}}]})["route"] == "abstain"


def test_an_answer_that_is_not_json_is_an_abstain():
    payload = {"candidates": [{"content": {"parts": [{"text": "I think the page is broken"}]}}]}

    assert diagnose._answer_from(payload)["route"] == "abstain"


def test_a_provider_refusal_keeps_what_the_provider_said(monkeypatch):
    class Refused:
        status_code = 400
        is_error = True
        text = '{"error": {"message": "API key not valid."}}'

    monkeypatch.setattr(diagnose.httpx, "post", lambda *a, **k: Refused())

    with pytest.raises(RuntimeError) as failure:
        diagnose.diagnose(incident(), "goal", env={"GEMINI_API_KEY": "bad"})

    assert "HTTP 400" in str(failure.value)
    assert "API key not valid" in str(failure.value)


class Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


ANSWER = {"candidates": [{"content": {"parts": [
    {"text": json.dumps({"layer": "action", "route": "abstain", "confidence": 0.2})}
]}}]}


def test_a_capacity_spike_is_retried(monkeypatch):
    # The first live call met one of these; a stopped run is the wrong moment to give up.
    calls = []
    responses = [Response(503, text="high demand"), Response(200, ANSWER)]
    monkeypatch.setattr(diagnose.httpx, "post", lambda *a, **k: calls.append(1) or responses.pop(0))
    monkeypatch.setattr(diagnose.time, "sleep", lambda _seconds: None)

    result = diagnose.diagnose(incident(), "goal", env={"GEMINI_API_KEY": "test"})

    assert len(calls) == 2
    assert result["route"] == "abstain"


def test_a_spike_that_does_not_clear_still_reports_the_provider(monkeypatch):
    monkeypatch.setattr(diagnose.httpx, "post",
                        lambda *a, **k: Response(503, text="high demand"))
    monkeypatch.setattr(diagnose.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError) as failure:
        diagnose.diagnose(incident(), "goal", env={"GEMINI_API_KEY": "test"})

    assert "HTTP 503" in str(failure.value) and "high demand" in str(failure.value)


def test_a_dropped_connection_is_reported_rather_than_raised_raw(monkeypatch):
    def explode(*_args, **_kwargs):
        raise diagnose.httpx.ConnectError("no route to host")

    monkeypatch.setattr(diagnose.httpx, "post", explode)

    with pytest.raises(RuntimeError, match="connection failed"):
        diagnose.diagnose(incident(), "goal", env={"GEMINI_API_KEY": "test"})
