"""Offline checks for agent loop safety without provider calls."""

from helmsman import model, questions
from helmsman.browser import progress_fingerprint
from helmsman.loop_guard import action_key, detect_loop, loop_warning, redundant_choice


def test_the_rules_separate_a_quoted_error_from_a_page_outage():
    # A result quoting another page's error is not this page reporting an outage.
    # Page text is flattened, so without this line the two read the same, and a run
    # stopped after three good steps on a search result that quoted a failed preview.
    assert "quoted inside a result" in questions.NEXT_ACTION
    assert "not a reason to stop" in questions.NEXT_ACTION


def row(before, action, after):
    return {
        "before_progress": before,
        "after_progress": after,
        "action_key": action_key({"kind": "click", "label": action}),
        "kind": "click",
        "action": action,
    }


def test_page_and_action_signatures_ignore_transient_node_ids():
    first = {
        "url": "https://example.test/",
        "title": "Billing",
        "text": "Current bill",
        "scroll": {"y": 0},
        "actions": [{"id": "e1", "node": 1, "kind": "click", "label": "EasyPay"}],
    }
    reloaded = {**first, "actions": [{**first["actions"][0], "node": 98, "id": "e7"}]}
    assert progress_fingerprint(first) == progress_fingerprint(reloaded)
    assert action_key(first["actions"][0]) == action_key(reloaded["actions"][0])
    assert progress_fingerprint(first) != progress_fingerprint({**first, "text": "Bill paid"})


def test_warn_then_refuse_repeated_noop_click_on_same_page():
    history = [row("maintenance", "EasyPay", "maintenance")] * 2
    assert loop_warning(history)
    assert redundant_choice(history, "maintenance", {"kind": "click", "label": "EasyPay"}) == 2
    assert redundant_choice(history, "new_page", {"kind": "click", "label": "EasyPay"}) == 0
    assert redundant_choice(history, "maintenance", {"kind": "click", "label": "Help"}) == 0
    assert redundant_choice(history, "maintenance", {"kind": "fill", "label": "Account"}) == 0


def test_nonconsecutive_revisits_are_not_a_loop():
    history = [
        row("A", "Open", "B"),
        row("B", "Next", "C"),
        row("C", "Back", "A"),
        row("A", "Open", "B"),
        row("B", "Other", "D"),
        row("D", "Back", "A"),
        row("A", "Open", "B"),
    ]
    assert detect_loop(history) is None


def test_consecutive_two_page_cycle_stops_after_three_full_repeats():
    cycle = [row("A", "Open", "B"), row("B", "Back", "A")]
    assert detect_loop(cycle * 2) is None
    assert detect_loop(cycle * 3) == {"code": "navigation_loop", "repetitions": 3, "period": 2}


def test_real_progress_allows_repeated_action():
    history = [row("results_1", "Next", "results_2"), row("results_2", "Next", "results_3")]
    assert loop_warning(history) is None
    assert redundant_choice(history, "results_3", {"kind": "click", "label": "Next"}) == 0
    assert detect_loop(history) is None


def test_corrective_warning_reaches_the_decision_model(monkeypatch):
    history = [row("maintenance", "EasyPay", "maintenance")] * 2
    page = {
        "url": "https://example.test/",
        "title": "Unavailable",
        "text": "Service maintenance",
        "actions": [{"id": "e1", "kind": "click", "label": "EasyPay", "node": 1}],
    }
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        criteria = body["questions"]["operation"]["criteria"]
        return {
            "model": "test",
            "answers": {
                "operation": {
                    "choice": "BLOCKED",
                    "confidence": 1.0,
                    "probabilities": {name: float(name == "BLOCKED") for name in criteria},
                }
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(page, "Check bill", history)["choice"] == "BLOCKED"
    assert len(calls) == 1
    rules = calls[0]["questions"]["operation"]["instructions"]["rules"]
    assert "Two identical actions left the visible page unchanged" in rules
    assert model.choose(page, "Check bill", history, loop_since=len(history))["choice"] == "BLOCKED"
    reset_rules = calls[1]["questions"]["operation"]["instructions"]["rules"]
    assert "Two identical actions left the visible page unchanged" not in reset_rules
