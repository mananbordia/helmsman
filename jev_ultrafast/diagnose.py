"""Diagnose a stopped run from its evidence, with a vision model.

This is the fallback for the one blocker left: Jev not understanding what to do. It
looks at the screenshot and the page at the moment of the stop and returns one of three
things -- a message for Jev, a request for the human, or an abstain. It never acts:
output cannot become a selector, a coordinate, or an operation, so a wrong diagnosis
costs a wasted step and never a wrong click.

The provider is Gemini, behind one function so it can be swapped. A malformed or
unexpected answer is an abstain rather than an exception: a stopped run must not crash
because a fallback model was verbose.
"""

import json
import os
import time

import httpx

# Same set the decision model retries: temporary capacity, not a bad request.
RETRY_STATUS = {429, 503, 529}

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Named by the provider's own 404 when 2.5-flash was retired for new keys. A wrong model
# here fails loudly with the replacement named, so this default is cheap to keep current.
DEFAULT_MODEL = "gemini-3.6-flash"

LAYERS = ("session", "navigation", "authentication", "action", "evidence", "unknown")
ROUTES = ("jev", "human", "abstain")
MAX_MESSAGE = 600
MAX_NEEDS = 300

PROMPT = """A browser agent stopped while working on a task. You are shown the page as it
looked at that moment, the reason it stopped, and the last steps it took.

Say which layer failed, then either tell the agent what to do differently or tell the
person what you need from them.

Layers:
- session: logged out, session expired, or a challenge is in the way
- navigation: the page did not load, or the agent is on the wrong page
- authentication: a sign-in or credentials are required to continue
- action: the agent tried something the page does not offer, or repeated a dead end
- evidence: the page looks fine but the agent cannot see what it needs
- unknown: you cannot tell

Answer with route "jev" and a message when the agent can continue with better
instructions. Answer with route "human" when something is missing that only the person
can supply, and name it in needs. Answer with route "abstain" when you cannot tell --
an honest abstain is more useful than a guess. Never ask for a password or a one-time
code to be typed into the chat: the person acts in the browser window for those."""

SCHEMA = {
    "type": "object",
    "properties": {
        "layer": {"type": "string", "enum": list(LAYERS)},
        "route": {"type": "string", "enum": list(ROUTES)},
        "message": {"type": "string"},
        "needs": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["layer", "route", "confidence"],
}


class DiagnosisUnavailable(RuntimeError):
    """No vision provider is configured, so there is nothing to ask."""


def configured(env=None):
    """True when a vision model can be reached. Absent, the fallback stays off."""
    return bool((env or os.environ).get("GEMINI_API_KEY"))


def _abstain(reason):
    return {"layer": "unknown", "route": "abstain", "message": "", "needs": "", "confidence": 0.0,
            "reason": reason}


def read_diagnosis(answer):
    """Validate what came back. Anything unexpected becomes an abstain."""
    if not isinstance(answer, dict):
        return _abstain("the diagnosis was not an object")
    route = answer.get("route")
    layer = answer.get("layer")
    if route not in ROUTES:
        return _abstain(f"unsupported route: {route!r}")
    if layer not in LAYERS:
        return _abstain(f"unsupported layer: {layer!r}")
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return _abstain("confidence was missing or out of range")
    message = answer.get("message") or ""
    needs = answer.get("needs") or ""
    if not isinstance(message, str) or not isinstance(needs, str):
        return _abstain("the diagnosis had a non-text message")
    if len(message) > MAX_MESSAGE or len(needs) > MAX_NEEDS:
        return _abstain("the diagnosis was too long to act on")
    if route == "jev" and not message.strip():
        return _abstain("a message was promised but not given")
    if route == "human" and not needs.strip():
        return _abstain("a request was promised but not described")
    return {
        "layer": layer, "route": route, "message": message.strip(),
        "needs": needs.strip(), "confidence": float(confidence), "reason": "",
    }


def request_body(incident, goal, model=DEFAULT_MODEL):
    """The provider payload: what happened, what the page looks like, and the picture."""
    steps = "\n".join(
        f"- step {item.get('step')}: {item.get('operation') or item.get('kind')} "
        f"{item.get('action')} (page {'changed' if item.get('page_changed') else 'unchanged'})"
        for item in incident.get("steps", [])
    ) or "- no steps recorded"
    text = (
        f"Task: {goal}\n"
        f"Stopped because: {json.dumps(incident.get('block_reason'))}\n"
        f"Page: {incident.get('url')} -- {incident.get('title')}\n"
        f"Page text:\n{(incident.get('text') or '')[:4000]}\n"
        f"Last steps:\n{steps}"
    )
    parts = [{"text": text}]
    picture = incident.get("screenshot")
    if picture:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": picture}})
    return {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": PROMPT}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "temperature": 0,
        },
    }


def diagnose(incident, goal, env=None, post=None):
    """Ask the vision model what went wrong. Raises only when nothing is configured."""
    env = env or os.environ
    key = env.get("GEMINI_API_KEY")
    if not key:
        raise DiagnosisUnavailable(
            "Set GEMINI_API_KEY in the app's .env to diagnose a stopped run"
        )
    model = env.get("GEMINI_MODEL", DEFAULT_MODEL)
    post = post or _post
    answer = post(ENDPOINT.format(model=model), key, request_body(incident, goal, model))
    return read_diagnosis(answer)


def _post(url, key, body, attempts=3):
    """One call, retrying a capacity spike, and keeping the provider's words if it refuses.

    A spike is momentary -- the first live call met one -- and a stopped run is exactly
    when a retry is worth the wall clock.
    """
    for attempt in range(attempts):
        try:
            response = httpx.post(url, json=body, headers={"x-goog-api-key": key}, timeout=60)
        except httpx.HTTPError:
            raise RuntimeError("Vision provider connection failed; the run is unchanged") from None
        if response.status_code in RETRY_STATUS and attempt + 1 < attempts:
            time.sleep(1.5 * 2**attempt)
            continue
        if response.is_error:
            detail = " ".join(response.text.split())[:300]
            raise RuntimeError(
                f"Vision provider returned HTTP {response.status_code}"
                + (f". Provider said: {detail}" if detail else "")
            )
        return _answer_from(response.json())
    raise RuntimeError("Vision provider stayed unavailable")


def _answer_from(payload):
    """The model's JSON answer, from the response envelope."""
    candidates = payload.get("candidates") or []
    if not candidates:
        return _abstain(f"no candidate was returned: {json.dumps(payload)[:200]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = next((part.get("text") for part in parts if part.get("text")), "")
    if not text:
        return _abstain("the answer carried no text")
    try:
        return json.loads(text)
    except ValueError:
        return _abstain("the answer was not JSON")
