"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from helmsman import agent as loop
from helmsman import model
from helmsman.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
        "escalations": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_with_changing_page_do_not_trigger_no_progress_stop(runner):
    for index in range(5):
        updated = page()
        updated["text"] = f"Loading result {index}"
        updated["fingerprint"] = fingerprint(updated)
        runner.state["browser"].observe.return_value = updated
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stalled_waits_stop_before_another_wait(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert result["status"] == "blocked"
    assert result["block_reason"] == {"code": "no_progress", "actions": 4}
    assert len(result["history"]) == 4
    assert runner.state["browser"].act.call_count == 4


def test_model_block_is_reported_without_browser_mutation(runner):
    runner.state["decision"] = decision("BLOCKED")
    result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert result["status"] == "blocked"
    assert result["block_reason"] == {"code": "model_blocked"}
    runner.state["browser"].act.assert_not_called()


def test_repeated_actions_without_page_change_report_why_the_run_stopped(runner):
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert result["status"] == "blocked"
    assert result["block_reason"] == {"code": "no_progress", "actions": 2}
    assert len(result["history"]) == 2
    assert runner.state["browser"].act.call_count == 2


def test_reloaded_same_page_is_not_counted_as_progress(runner, monkeypatch):
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    for index in range(3):
        reloaded = page()
        for action in reloaded["actions"]:
            if "node" in action:
                action["node"] += (index + 1) * 100
        reloaded["fingerprint"] = fingerprint(reloaded)
        runner.state["browser"].observe.return_value = reloaded
        runner.state["decision"] = decision("e3")
        result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
        assert result["history"][-1]["page_changed"] is False
    assert result["status"] == "blocked"
    assert result["block_reason"] == {"code": "no_progress", "actions": 2}
    assert runner.state["browser"].act.call_count == 2


def test_repeated_page_cycle_stops_before_action_budget(runner):
    first = page()
    second = page()
    second["text"] = "Other page"
    second["fingerprint"] = fingerprint(second)
    for destination in [second, first] * 3:
        runner.state["browser"].observe.return_value = destination
        runner.state["decision"] = decision("e3")
        result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert result["status"] == "blocked"
    assert result["block_reason"] == {"code": "navigation_loop", "repetitions": 3, "period": 2}
    assert len(result["history"]) == 6


def test_delayed_page_update_is_observed_before_another_decision(runner, monkeypatch):
    changed = page()
    changed["text"] = "Results loaded"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.side_effect = [page(), changed]
    sleep = Mock()
    monkeypatch.setattr(loop.time, "sleep", sleep)
    runner.state["decision"] = decision("e3")

    result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    runner.state["browser"].act.assert_called_once()
    assert runner.state["browser"].observe.call_count == 2
    sleep.assert_called_once_with(1.0)
    assert result["page"]["text"] == "Results loaded"
    assert result["history"][-1]["page_changed"] is True
    assert result["no_progress_count"] == 0
    assert result["status"] == "ready"


def test_visible_captcha_blocks_without_model_call_or_browser_action(runner, monkeypatch):
    # A challenge page is a wall: nothing for the agent to do, so it must not reach the
    # model. A provider widget beside usable controls is not a wall and is covered in
    # test_motion.
    runner.state["page"]["captcha"] = {"provider": "recaptcha", "surface": "challenge_page"}
    runner.state["decision"] = None
    choose = Mock(side_effect=AssertionError("CAPTCHA must not reach the model"))
    monkeypatch.setattr(loop, "choose", choose)
    result = runner.command("tick")
    assert result["status"] == "blocked"
    assert result["block_reason"] == {
        "code": "captcha_detected", "provider": "recaptcha", "surface": "challenge_page"
    }
    assert result["decisions"] == []
    choose.assert_not_called()
    runner.state["browser"].act.assert_not_called()


def test_human_input_is_available_only_during_blocked_handoff(runner):
    event = {"kind": "pointer_down", "x": 10, "y": 10}
    runner.state["human_control"] = False
    with pytest.raises(ValueError, match="Take control"):
        runner.human_input(event)
    runner.state["status"] = "blocked"
    with pytest.raises(ValueError, match="Take control"):
        runner.human_view()
    runner.state["human_control"] = True
    runner.state["browser"].human_view.return_value = {"screenshot": "live"}
    assert runner.human_view() == {"screenshot": "live", "ready_to_resume": False}
    changed = page()
    changed["text"] = "The challenge is gone"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.return_value = changed
    assert runner.human_view()["ready_to_resume"] is True
    runner.human_input(event)
    runner.state["browser"].human_input.assert_called_once_with(event)


def test_captcha_appearing_after_action_stops_next_decision(runner):
    blocked = page()
    blocked["captcha"] = {"provider": "hcaptcha", "surface": "challenge_page"}
    blocked["fingerprint"] = fingerprint(blocked)
    runner.state["browser"].observe.return_value = blocked
    runner.state["decision"] = decision("e3")
    result = runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert result["status"] == "blocked"
    assert result["block_reason"]["code"] == "captcha_detected"
    assert len(result["history"]) == 1
    runner.state["browser"].act.assert_called_once()


def test_human_handoff_waits_for_challenge_to_clear_before_resuming(runner, monkeypatch):
    blocked = page()
    blocked["captcha"] = {"provider": "unknown", "surface": "challenge_page"}
    blocked["fingerprint"] = fingerprint(blocked)
    runner.state["page"] = blocked
    choose = Mock(side_effect=AssertionError("No model call during handoff"))
    monkeypatch.setattr(loop, "choose", choose)

    assert runner.command("tick")["status"] == "blocked"
    taken = runner.command("handoff")
    assert taken["human_control"] is True
    runner.state["browser"].show.assert_called_once()
    with pytest.raises(ValueError, match="Human control"):
        runner.command("tick")
    runner.state["browser"].act.assert_not_called()
    choose.assert_not_called()

    runner.state["browser"].observe.return_value = blocked
    still_blocked = runner.command("resume")
    assert still_blocked["status"] == "blocked"
    assert still_blocked["block_reason"]["code"] == "captcha_detected"
    assert still_blocked["human_control"] is True

    cleared = page()
    cleared["text"] = "Search results are now visible"
    cleared["fingerprint"] = fingerprint(cleared)
    runner.state["browser"].observe.return_value = cleared
    resumed = runner.command("resume")
    assert resumed["status"] == "ready"
    assert resumed["block_reason"] is None
    assert resumed["human_control"] is False
    assert len(resumed["history"]) == 0


def test_no_progress_handoff_needs_a_changed_page(runner):
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.command("handoff")
    unchanged = runner.command("resume")
    assert unchanged["status"] == "blocked"
    assert unchanged["block_reason"]["code"] == "no_progress"

    changed = page()
    changed["text"] = "The human opened a different result"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.return_value = changed
    assert runner.command("resume")["status"] == "ready"
    runner.state["decision"] = decision("e3")
    assert runner.command("act", {"fingerprint": changed["fingerprint"]})["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import helmsman.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import helmsman.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


def test_action_pacing_rechecks_freshness_before_input(monkeypatch):
    import helmsman.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.next_action_at = 11.0
    b.session = "test"
    clock = {"now": 10.0}
    events = []

    def sleep(seconds):
        events.append(("sleep", seconds))
        clock["now"] += seconds

    def fresh(*_args):
        events.append(("fresh", clock["now"]))
        return False  # The page changed during the pause.

    b.fresh = fresh
    operation = Mock()
    monkeypatch.setattr(browser.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(browser.time, "sleep", sleep)
    monkeypatch.setattr(browser, "browser_operation", operation)

    with pytest.raises(StalePage, match="Page changed"):
        b.act(page()["actions"][2], page())

    assert events == [("sleep", 1.0), ("fresh", 11.0)]
    operation.assert_not_called()


def test_executor_refuses_a_detected_captcha_before_browser_input(monkeypatch):
    import helmsman.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=True)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    p = page()
    p["captcha"] = {"provider": "recaptcha", "surface": "widget"}
    with pytest.raises(StalePage, match="CAPTCHA"):
        b.act(p["actions"][2], p)
    b.fresh.assert_not_called()
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import helmsman.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)
    other = deepcopy(p)
    other["captcha"] = {"provider": "recaptcha", "surface": "widget"}
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from tests.flights_task import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_navigation_after_click_retries_only_observation(runner, monkeypatch):
    monkeypatch.setattr(loop.time, "sleep", lambda _seconds: None)
    changed = page()
    changed["url"] = "https://example.test/next"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = [
        StalePage("Document navigating"),
        StalePage("Document navigating"),
        changed,
    ]
    monkeypatch.setattr(loop, "choose", lambda *_args: decision("e3"))
    result = runner.command("tick")
    assert result["status"] == "ready"
    assert result["page"]["url"] == changed["url"]
    assert result["history"][-1]["page_changed"] is True
    runner.state["browser"].act.assert_called_once()


def test_navigation_timeout_blocks_without_replaying_click(runner, monkeypatch):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("Document navigating")
    monkeypatch.setattr(loop, "choose", lambda *_args: decision("e3"))
    now = iter([0, 6])
    monkeypatch.setattr(loop.time, "monotonic", lambda: next(now))
    result = runner.command("tick")
    assert result["status"] == "blocked"
    assert result["block_reason"]["code"] == "page_unavailable"
    runner.state["browser"].act.assert_called_once()
