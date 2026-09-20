"""Bounded action/observation loop checks for the browser agent.

Inspired by browser-use's action/page fingerprints and OpenHands' consecutive
action-observation pattern checks. Only executed actions enter this history.
"""

import hashlib
import json


def action_key(action, text=None):
    """Identify an operation by its visible meaning, never its transient DOM node."""
    content = {
        "kind": action["kind"],
        "role": action.get("role"),
        "label": action["label"],
        "value": action.get("value") if action["kind"] == "select" else None,
        "text": text if action["kind"] == "fill" else None,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _same_stalled_pair(history, count, page_key=None, candidate=None):
    if len(history) < count:
        return False
    tail = history[-count:]
    first = tail[0]
    key = first.get("action_key")
    page = first.get("before_progress")
    if not key or not page or (page_key is not None and page != page_key):
        return False
    if candidate is not None and key != candidate:
        return False
    return all(
        item.get("action_key") == key
        and item.get("before_progress") == page
        and item.get("after_progress") == page
        for item in tail
    )


def redundant_choice(history, page_key, action):
    """Refuse another identical no-op before dispatching browser input."""
    if action["kind"] == "fill":
        return 0  # Text is generated later and may differ from previous attempts.
    prior = 4 if action["kind"] == "wait" else 2
    return prior if _same_stalled_pair(history, prior, page_key, action_key(action)) else 0


def loop_warning(history):
    """Give the model one chance to choose a different action or BLOCKED."""
    if _same_stalled_pair(history, 4) and history[-1].get("kind") == "wait":
        return "Four waits left the visible page unchanged. Choose another action or BLOCKED."
    if _same_stalled_pair(history, 2) and history[-1].get("kind") != "wait":
        return "Two identical actions left the visible page unchanged. Choose a different action or BLOCKED."
    if len(history) >= 4:
        tail = history[-4:]
        if all(item.get("action_key") and item.get("after_progress") for item in tail):
            pairs = [(item["action_key"], item["after_progress"]) for item in tail]
            if pairs[:2] == pairs[2:]:
                return "The last four actions repeated a two-step page cycle. Change approach or choose BLOCKED."
    return None


def detect_loop(history):
    """Return a block reason only for repeated, recently completed patterns."""
    if _same_stalled_pair(history, 3) and history[-1].get("kind") != "wait":
        return {"code": "no_progress", "actions": 3}
    if len(history) >= 5:
        tail = history[-5:]
        if all(item.get("kind") == "wait" for item in tail) and _same_stalled_pair(tail, 5):
            return {"code": "no_progress", "actions": 5}
        if all(
            item.get("before_progress")
            and item.get("before_progress") == item.get("after_progress")
            for item in tail
        ):
            return {"code": "no_progress", "actions": 5}
    for period in (2, 3):
        length = period * 3
        if len(history) < length:
            continue
        tail = history[-length:]
        if not all(
            item.get("before_progress") and item.get("after_progress") and item.get("action_key")
            for item in tail
        ):
            continue
        pattern = [
            (item["before_progress"], item["action_key"], item["after_progress"])
            for item in tail
        ]
        if pattern[:period] == pattern[period : 2 * period] == pattern[2 * period :]:
            return {"code": "navigation_loop", "repetitions": 3, "period": period}
    return None
