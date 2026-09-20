"""Offline contracts for human-like pointer, typing and scroll behaviour."""

import math
import random
import statistics
import threading
from unittest.mock import Mock

import pytest

from jev_ultrafast import browser, motion
from jev_ultrafast.agent import Agent
from jev_ultrafast.browser import Paused, StalePage, browser_operation


def seeded(seed=1):
    return random.Random(seed)


# ---------------------------------------------------------------- path shape


def test_fitts_difficulty_rises_with_distance_and_falls_with_target_size():
    assert motion.fitts(100, 100) < motion.fitts(400, 100)
    assert motion.fitts(100, 200) < motion.fitts(100, 50)
    assert motion.fitts(0, 100) == 0


def test_a_path_ends_exactly_on_the_target_and_omits_the_origin():
    start, end = (10.0, 20.0), (400.0, 300.0)
    points = motion.path(start, end, 80.0, seeded())
    assert points[0] != start
    assert points[-1] == pytest.approx(end)
    assert len(points) >= motion.MIN_STEPS


def deviation(points):
    """How far the path strays from the straight line along y."""
    return max(abs(y) for _, y in points)


def test_a_path_almost_never_runs_dead_straight():
    offsets = [deviation(motion.path((0.0, 0.0), (600.0, 0.0), 80.0, seeded(seed))) for seed in range(20)]
    assert all(offset > 1.0 for offset in offsets)


def test_a_long_move_bows_further_than_a_short_one():
    short = motion.path((0.0, 0.0), (30.0, 0.0), 80.0, seeded())
    long = motion.path((0.0, 0.0), (900.0, 0.0), 80.0, seeded())
    assert deviation(short) < deviation(long)


def test_a_very_short_move_collapses_to_the_target():
    assert motion.path((5.0, 5.0), (5.2, 5.2), 80.0, seeded()) == [(5.2, 5.2)]


def test_step_count_stays_within_bounds():
    for distance in (2.0, 50.0, 400.0, 5000.0):
        points = motion.path((0.0, 0.0), (distance, 0.0), 40.0, seeded(int(distance)))
        assert motion.MIN_STEPS <= len(points) <= motion.MAX_STEPS


# ------------------------------------------------------------------- timing


def test_step_delays_sum_to_the_move_duration():
    start, end = (0.0, 0.0), (500.0, 250.0)
    points = [start, *motion.path(start, end, 80.0, seeded())]
    delays = motion.step_delays(points, 0.9)
    assert len(delays) == len(points) - 1
    assert sum(delays) == pytest.approx(0.9, abs=1e-6)


def test_velocity_peaks_mid_flight_rather_than_staying_constant():
    # Evenly spaced samples along a straight line: the pointer should ease out of
    # the start and into the target, so the ends take longer than the middle.
    points = [(float(index) * 10.0, 0.0) for index in range(12)]
    delays = motion.step_delays(points, 0.8)
    middle = delays[len(delays) // 2]
    assert delays[0] > middle
    assert delays[-1] > middle


def test_move_duration_grows_with_distance_and_is_bounded():
    short = motion.move_duration((0.0, 0.0), (20.0, 0.0), 80.0, seeded())
    far = motion.move_duration((0.0, 0.0), (900.0, 0.0), 80.0, seeded())
    assert short < far
    assert motion.MIN_MOVE_SECONDS <= short <= motion.MAX_MOVE_SECONDS
    assert motion.MIN_MOVE_SECONDS <= far <= motion.MAX_MOVE_SECONDS


def test_click_pauses_are_ordered_and_positive():
    hesitate, hold = motion.click_delays(seeded())
    assert 0.04 <= hesitate <= 0.16
    assert 0.04 <= hold <= 0.11
    assert motion.key_hold(seeded()) > 0


# ----------------------------------------------------------------- overshoot


def test_overshoot_lands_inside_the_configured_radius():
    target = (500.0, 400.0)
    for seed in range(25):
        point = motion.overshoot_point(target, random.Random(seed))
        assert math.hypot(point[0] - target[0], point[1] - target[1]) <= motion.OVERSHOOT_RADIUS


def test_a_long_approach_overshoots_and_a_short_one_does_not():
    target = (900.0, 500.0)
    far_points, _ = motion.approach((0.0, 0.0), target, 60.0, seeded())
    near_points, _ = motion.approach((880.0, 490.0), target, 60.0, seeded())
    # The path lands on the target, then settles with micro-corrections around it.
    for points in (far_points, near_points):
        assert math.hypot(points[-1][0] - target[0], points[-1][1] - target[1]) <= (
            motion.ARRIVAL_RADIUS * math.sqrt(2)
        )
    assert len(far_points) > len(near_points)


def test_overshoot_only_triggers_beyond_the_threshold():
    assert motion.OVERSHOOT_THRESHOLD == 500.0
    just_under, _ = motion.approach((0.0, 0.0), (400.0, 0.0), 60.0, seeded())
    just_over, _ = motion.approach((0.0, 0.0), (600.0, 0.0), 60.0, seeded())
    # The longer approach adds a whole extra leg, not just more samples.
    assert len(just_over) > len(just_under)


def test_arrival_corrections_stay_close_to_the_target():
    corrections = motion.arrival_points((100.0, 100.0), seeded())
    assert len(corrections) == motion.ARRIVAL_MOVES
    for point in corrections:
        assert math.hypot(point[0] - 100.0, point[1] - 100.0) <= motion.ARRIVAL_RADIUS * math.sqrt(2)


# -------------------------------------------------------------------- aim


def test_aim_stays_inside_the_element_and_off_the_exact_centre():
    offsets = [motion.target_offsets(200.0, 40.0, random.Random(seed)) for seed in range(40)]
    assert all(motion.TARGET_INSET <= u <= 1 - motion.TARGET_INSET for u, _ in offsets)
    assert all(motion.TARGET_INSET <= v <= 1 - motion.TARGET_INSET for _, v in offsets)
    # Landing on 0.5 every time is the thing we are removing.
    assert any(abs(u - 0.5) > 1e-9 or abs(v - 0.5) > 1e-9 for u, v in offsets)


def test_aim_error_is_absolute_rather_than_proportional_to_the_element():
    def vertical_deviation(height):
        offsets = [
            (v - 0.5) * height
            for _, v in (motion.target_offsets(400.0, height, random.Random(seed)) for seed in range(200))
        ]
        return statistics.pstdev(offsets)

    # A 20px strip is aimed at far more tightly than a 200px panel, even though
    # both elements are the same width.
    assert vertical_deviation(20.0) < vertical_deviation(200.0) / 3


def test_aim_clusters_around_the_centre_rather_than_spreading_evenly():
    values = [motion.target_offsets(200.0, 200.0, random.Random(seed))[0] for seed in range(400)]
    near_centre = sum(1 for value in values if abs(value - 0.5) < 0.1)
    # A uniform aim across the box would put about a fifth of them there.
    assert near_centre > len(values) * 0.33


# ------------------------------------------------------------------ typing


def test_type_intervals_produce_one_delay_per_character():
    text = "Zurich to London"
    delays = motion.type_intervals(text, seeded())
    assert len(delays) == len(text)
    assert all(delay > 0 for delay in delays)


def test_long_values_are_compressed_to_the_requested_ceiling():
    text = "x" * 400
    assert sum(motion.type_intervals(text, seeded())) > 10.0
    assert sum(motion.type_intervals(text, seeded(), max_total=8.0)) == pytest.approx(8.0)


def test_word_boundaries_take_longer_than_letters():
    # Same character count, so the only difference is the space.
    plain = sum(motion.type_intervals("aaaaaaaaaa", seeded(7)))
    spaced = sum(motion.type_intervals("aaaa aaaaa", seeded(7)))
    assert spaced > plain


def test_sentence_breaks_take_longer_than_spaces():
    spaced = motion.type_intervals("aaaa aaaa", seeded(3))[4]
    stopped = motion.type_intervals("aaaa.aaaa", seeded(3))[4]
    assert stopped > spaced


# ------------------------------------------------------------------ scroll


@pytest.mark.parametrize("total", [-560.0, 560.0, 120.0, -1500.0])
def test_scroll_is_split_into_a_tappering_burst_that_sums_to_the_distance(total):
    deltas = motion.scroll_deltas(total, seeded())
    assert 2 <= len(deltas) <= 9
    assert all(math.copysign(1, delta) == math.copysign(1, total) for delta in deltas)
    assert sum(deltas) == pytest.approx(total, abs=1e-6)
    assert abs(deltas[0]) > abs(deltas[-1])


def test_no_scroll_produces_no_wheel_events():
    assert motion.scroll_deltas(0.0, seeded()) == []


# ------------------------------------------------------------ inter-action


def test_action_delay_is_bounded_and_varies():
    delays = [motion.action_delay(random.Random(seed)) for seed in range(200)]
    assert all(0.35 <= delay <= 6.0 for delay in delays)
    assert len(set(delays)) > 1
    # Around a second, matching what the fixed interval used to approximate.
    assert 0.7 < sum(delays) / len(delays) < 1.6


def test_drift_stays_inside_the_viewport():
    for seed in range(20):
        x, y = motion.drift_point(browser.VIEWPORT_WIDTH, browser.VIEWPORT_HEIGHT, random.Random(seed))
        assert 0 < x < browser.VIEWPORT_WIDTH
        assert 0 < y < browser.VIEWPORT_HEIGHT


# ------------------------------------------------------------- determinism


def test_the_same_seed_reproduces_the_same_gesture():
    first = motion.approach((0.0, 0.0), (700.0, 400.0), 80.0, random.Random(11))
    second = motion.approach((0.0, 0.0), (700.0, 400.0), 80.0, random.Random(11))
    assert first == second
    assert motion.type_intervals("Zurich", random.Random(5)) == motion.type_intervals("Zurich", random.Random(5))
    assert motion.scroll_deltas(560.0, random.Random(9)) == motion.scroll_deltas(560.0, random.Random(9))


# ------------------------------------------------------------------- pause


def test_holding_sleeps_normally_when_no_pause_is_configured(monkeypatch):
    slept = []
    monkeypatch.setattr(browser.time, "sleep", slept.append)
    browser.hold(0.5, None)
    assert slept == [0.5]


def test_holding_returns_immediately_when_a_pause_is_already_requested():
    pause = threading.Event()
    pause.set()
    with pytest.raises(Paused):
        browser.hold(30.0, pause)


def test_holding_wakes_early_when_the_pause_arrives_during_the_wait():
    pause = threading.Event()
    threading.Timer(0.02, pause.set).start()
    with pytest.raises(Paused):
        browser.hold(30.0, pause)


def test_a_checkpoint_does_not_block():
    assert browser.checkpoint(None) is None
    assert browser.checkpoint(threading.Event()) is None
    pause = threading.Event()
    pause.set()
    with pytest.raises(Paused):
        browser.checkpoint(pause)


def test_a_pause_during_the_travel_stops_before_any_press(monkeypatch):
    cdp, calls = cdp_recorder([(300.0, 200.0)] * 4)
    pause = threading.Event()

    def pausing_cdp(method, **params):
        result = cdp(method, **params)
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mouseMoved":
            # The operator hits the switch while the pointer is still travelling.
            pause.set()
        return result

    monkeypatch.setattr(browser, "cdp", pausing_cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    with pytest.raises(Paused):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "click", "node": 1},
            "rng": random.Random(4), "pointer": (10.0, 10.0), "pause": pause,
        })
    kinds = [params["type"] for method, params in calls if method == "Input.dispatchMouseEvent"]
    assert "mouseMoved" in kinds
    # Nothing reached the page.
    assert "mousePressed" not in kinds
    assert "mouseReleased" not in kinds


def test_a_pause_while_typing_stops_between_characters(monkeypatch):
    cdp, _calls = cdp_recorder([(100.0, 100.0)] * 4)
    pause = threading.Event()
    typed = []

    def pausing_cdp(method, **params):
        result = cdp(method, **params)
        if method == "Input.dispatchKeyEvent" and params.get("type") == "keyDown":
            if params.get("key") in {"x", "y", "z"}:
                typed.append(params["key"])
                pause.set()
        return result

    monkeypatch.setattr(browser, "cdp", pausing_cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    with pytest.raises(Paused):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "fill", "node": 1},
            "text": "xyz", "rng": random.Random(6), "pointer": (0.0, 0.0), "pause": pause,
        })
    # One character landed, then the pause stopped the rest.
    assert typed == ["x"]


def test_a_pause_stops_a_scroll_before_its_first_wheel_event(monkeypatch):
    cdp, calls = cdp_recorder([])
    pause = threading.Event()
    pause.set()
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    with pytest.raises(Paused):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "scroll_down", "kind": "scroll", "delta": 560},
            "rng": random.Random(8), "pointer": (400.0, 300.0), "pause": pause,
        })
    assert [method for method, _ in calls if method == "Input.dispatchMouseEvent"] == []


def test_a_pause_stops_a_dropdown_before_it_mutates(monkeypatch):
    # A native select is set through the DOM, so no wait protects it.
    cdp, calls = cdp_recorder([])
    pause = threading.Event()
    pause.set()
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(Paused):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "select", "node": 1, "value": "Design"},
            "pause": pause,
        })
    assert calls == []


def test_a_pause_leaves_the_press_and_release_pair_complete(monkeypatch):
    cdp, calls = cdp_recorder([(300.0, 200.0)] * 4)
    pause = threading.Event()

    def pausing_cdp(method, **params):
        result = cdp(method, **params)
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed":
            # Worst case: the pause lands inside the hold.
            pause.set()
        return result

    monkeypatch.setattr(browser, "cdp", pausing_cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e1", "kind": "click", "node": 1},
        "rng": random.Random(4), "pointer": (10.0, 10.0), "pause": pause,
    })
    kinds = [params["type"] for method, params in calls if method == "Input.dispatchMouseEvent"]
    # Releasing a button that was pressed matters more than stopping a moment sooner.
    assert kinds.count("mousePressed") == 1
    assert kinds.count("mouseReleased") == 1


# -------------------------------------------------------------- agent pause


def agent_with_stub_browser():
    """An Agent whose browser is a stand-in, so no CDP connection is needed."""
    runner = Agent.__new__(Agent)
    stub = Mock()
    stub.paused.return_value = False
    runner.pending_text = None
    runner.screenshots = False
    runner.record_dir = None
    runner.state = {
        "browser": stub, "goal": "goal", "page": {"url": "u", "actions": [], "fingerprint": "fp"},
        "decision": None, "history": [], "status": "ready", "block_reason": None,
        "human_control": False, "no_progress_count": 0, "loop_since": 0,
        "plan": ["goal"], "plan_index": 0, "decisions": [], "text_calls": [],
        "elapsed_ms": 0, "started_at": None, "record": False,
    }
    return runner, stub


def test_pause_sets_the_flag_and_reports_the_status():
    runner, stub = agent_with_stub_browser()
    assert runner.command("pause")["status"] == "paused"
    stub.set_paused.assert_called_once_with(True)


def test_pause_is_idempotent_and_leaves_the_page_alone():
    runner, stub = agent_with_stub_browser()
    runner.command("pause")
    runner.command("pause")
    assert stub.set_paused.call_count == 2
    stub.act.assert_not_called()
    stub.observe.assert_not_called()


def test_continue_clears_the_flag_and_returns_to_ready():
    runner, stub = agent_with_stub_browser()
    runner.state["status"] = "paused"
    assert runner.command("continue")["status"] == "ready"
    stub.set_paused.assert_called_once_with(False)


def test_pausing_does_not_reopen_a_finished_run():
    runner, _stub = agent_with_stub_browser()
    runner.state["status"] = "done"
    assert runner.command("pause")["status"] == "done"


def test_a_paused_run_makes_no_model_call(monkeypatch):
    from jev_ultrafast import agent as agent_module

    runner, stub = agent_with_stub_browser()
    stub.paused.return_value = True
    choose = Mock()
    monkeypatch.setattr(agent_module, "choose", choose)
    assert runner.command("predict")["status"] == "paused"
    choose.assert_not_called()


def test_a_paused_run_does_not_touch_the_page():
    runner, stub = agent_with_stub_browser()
    stub.paused.return_value = True
    runner.state["page"]["fingerprint"] = "fp"
    runner.state["decision"] = {"choice": "e1"}
    assert runner.command("act", {"fingerprint": "fp"})["status"] == "paused"
    stub.act.assert_not_called()


def test_resuming_after_a_pause_observes_and_chooses_again(monkeypatch):
    from jev_ultrafast import agent as agent_module

    runner, stub = agent_with_stub_browser()
    runner.state["status"] = "paused"
    stub.paused.return_value = False
    choose = Mock(return_value={"choice": "wait", "operation": "WAIT", "target": None,
                                "confidence": 1.0, "probabilities": {"wait": 1.0},
                                "latency_ms": 1, "usage": {}})
    monkeypatch.setattr(agent_module, "choose", choose)
    runner.command("continue")
    runner.command("predict")
    # The dropped decision is not reused; a fresh one is requested.
    assert choose.call_count == 1


# -------------------------------------------------------- browser identity


def test_the_screen_reader_prefers_the_streamed_frame():
    runner, stub = agent_with_stub_browser()
    stub.frame.return_value = "streamed"
    assert runner.capture_screen() == {"screenshot": "streamed", "url": "u"}
    stub.observe.assert_not_called()
    stub.act.assert_not_called()
    # Before the page has changed there is nothing streamed, so the last observed
    # screenshot stands in rather than showing a blank panel.
    stub.frame.return_value = None
    runner.state["page"]["screenshot"] = "held"
    assert runner.capture_screen() == {"screenshot": "held", "url": "u"}


def test_starting_the_stream_uses_the_browser():
    runner, stub = agent_with_stub_browser()
    runner.start_live()
    stub.start_live.assert_called_once_with()


def test_the_default_screen_is_larger_than_the_viewport():
    # A window bigger than its own display is impossible, and visible from the page.
    assert browser.screen_size({}) == browser.DEFAULT_SCREEN
    assert browser.DEFAULT_SCREEN[0] > browser.VIEWPORT_WIDTH
    assert browser.DEFAULT_SCREEN[1] > browser.VIEWPORT_HEIGHT


def test_the_configured_screen_is_used_when_set():
    assert browser.screen_size({"JEV_SCREEN": "2560x1440"}) == (2560, 1440)


def test_a_malformed_screen_setting_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError, match="1920x1080"):
        browser.screen_size({"JEV_SCREEN": "wide"})
    with pytest.raises(ValueError, match="positive"):
        browser.screen_size({"JEV_SCREEN": "0x1080"})


def test_the_headless_marker_is_removed_from_the_user_agent():
    headless = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36"
    )
    cleaned = browser.sanitized_user_agent(headless)
    assert "Headless" not in cleaned
    assert "Chrome/153.0.0.0" in cleaned
    # Re-running must not keep changing it.
    assert browser.sanitized_user_agent(cleaned) == cleaned


def test_an_ordinary_user_agent_is_left_alone():
    ordinary = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    )
    assert browser.sanitized_user_agent(ordinary) == ordinary


def stub_browser(user_agent, metadata):
    instance = browser.Browser.__new__(browser.Browser)
    calls = []
    instance.call = lambda method, **params: calls.append((method, params)) or {}
    instance.evaluate = lambda _expression: user_agent
    instance.evaluate_async = lambda _expression: metadata
    return instance, calls


def test_alignment_leaves_an_ordinary_user_agent_untouched():
    instance, calls = stub_browser("Mozilla/5.0 Chrome/153.0.0.0", {"brands": []})
    instance.align_fingerprint()
    assert calls == []


def test_alignment_replaces_a_headless_user_agent_and_its_client_hints():
    hinted = {"brands": [{"brand": "Chromium", "version": "153"}]}
    instance, calls = stub_browser("HeadlessChrome/153.0.0.0", hinted)
    instance.align_fingerprint()
    assert [method for method, _ in calls] == ["Emulation.setUserAgentOverride"]
    _, params = calls[0]
    assert params["userAgent"] == "Chrome/153.0.0.0"
    # The string and the hints must agree, or the pair is a louder signal than either.
    assert params["userAgentMetadata"] == hinted


def test_alignment_still_overrides_when_client_hints_are_unavailable():
    instance, calls = stub_browser("HeadlessChrome/153.0.0.0", None)
    instance.align_fingerprint()
    _, params = calls[0]
    assert params == {"userAgent": "Chrome/153.0.0.0"}


def test_alignment_does_nothing_when_the_page_cannot_be_read():
    instance, calls = stub_browser("HeadlessChrome/153.0.0.0", None)

    def explode(_expression):
        raise StalePage("navigating")

    instance.evaluate = explode
    instance.align_fingerprint()
    assert calls == []


# ------------------------------------------------------- keyboard mapping


def test_printable_characters_map_to_real_key_identities():
    assert browser.key_identity("a") == ("a", "KeyA", 0)
    assert browser.key_identity("A") == ("A", "KeyA", 8)
    assert browser.key_identity("7") == ("7", "Digit7", 0)
    assert browser.key_identity("!") == ("!", "Digit1", 8)
    assert browser.key_identity(" ") == (" ", "Space", 0)
    assert browser.key_identity("-") == ("-", "Minus", 0)
    assert browser.key_identity("_") == ("_", "Minus", 8)
    assert browser.key_identity("€") is None


def test_press_character_sends_a_key_pair_and_falls_back_for_unmapped_text(monkeypatch):
    calls = []
    monkeypatch.setattr(browser, "cdp", lambda method, **params: calls.append((method, params)) or {})
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    browser.press_character(browser.cdp, "B", seeded())
    assert [params["type"] for _, params in calls] == ["keyDown", "keyUp"]
    assert calls[0][1]["key"] == "B"
    assert calls[0][1]["modifiers"] == 8
    assert calls[0][1]["text"] == "B"
    calls.clear()
    browser.press_character(browser.cdp, "€", seeded())
    assert calls == [("Input.insertText", {"text": "€"})]


# ---------------------------------------------------- dispatched gestures


def cdp_recorder(points):
    """A fake CDP endpoint that resolves targets from ``points`` in order."""
    calls = []
    remaining = list(points)

    def cdp(method, **params):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            if not remaining:
                return {"result": {"value": None}}
            entry = remaining.pop(0)
            if entry is None:
                return {"result": {"value": None}}
            x, y = entry
            return {"result": {"value": {"x": x, "y": y, "width": 80.0, "height": 30.0}}}
        return {}

    return cdp, calls


def test_a_click_travels_before_it_presses(monkeypatch):
    cdp, calls = cdp_recorder([(300.0, 200.0)] * 4)
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    result = browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e1", "kind": "click", "node": 1},
        "rng": random.Random(4), "pointer": (10.0, 10.0),
    })
    dispatched = [params for method, params in calls if method == "Input.dispatchMouseEvent"]
    assert len(dispatched) > 5
    assert all(params["type"] == "mouseMoved" for params in dispatched[:-2])
    assert dispatched[-2]["type"] == "mousePressed"
    assert dispatched[-1]["type"] == "mouseReleased"
    assert (dispatched[-2]["x"], dispatched[-2]["y"]) == (300.0, 200.0)
    assert result["pointer"] == (300.0, 200.0)


def test_typing_sends_one_key_pair_per_character_instead_of_pasting(monkeypatch):
    cdp, calls = cdp_recorder([(100.0, 100.0)] * 4)
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e1", "kind": "fill", "node": 1},
        "text": "Hi 5", "rng": random.Random(6), "pointer": (0.0, 0.0),
    })
    methods = [method for method, _ in calls]
    assert "Input.insertText" not in methods
    typed = [params["key"] for method, params in calls
             if method == "Input.dispatchKeyEvent" and params["type"] == "keyDown"]
    # Select-all first, then every character of the value.
    assert typed == ["a", "H", "i", " ", "5"]
    assert typed.count(" ") == 1


def test_scroll_becomes_a_burst_of_wheel_events_at_the_pointer(monkeypatch):
    cdp, calls = cdp_recorder([])
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    result = browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "scroll_down", "kind": "scroll", "delta": 560},
        "rng": random.Random(8), "pointer": (400.0, 300.0),
    })
    wheels = [params for method, params in calls if method == "Input.dispatchMouseEvent"]
    assert len(wheels) >= 2
    assert all(params["type"] == "mouseWheel" for params in wheels)
    assert all((params["x"], params["y"]) == (400.0, 300.0) for params in wheels)
    assert sum(params["deltaY"] for params in wheels) == pytest.approx(560.0, abs=1e-6)
    assert result["pointer"] == (400.0, 300.0)


def test_select_still_executes_through_the_dom_without_pointer_events(monkeypatch):
    cdp, calls = cdp_recorder([(1.0, 2.0)])
    monkeypatch.setattr(browser, "cdp", cdp)
    result = browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e1", "kind": "select", "node": 1, "value": "Design"},
    })
    assert [method for method, _ in calls] == ["Runtime.evaluate"]
    assert result["executed"] == "e1"


def test_wait_does_not_touch_the_page(monkeypatch):
    cdp, calls = cdp_recorder([])
    monkeypatch.setattr(browser, "cdp", cdp)
    result = browser_operation({
        "operation": "act", "session": "test", "action": {"id": "wait", "kind": "wait"},
    })
    assert calls == []
    assert result == {"executed": "wait", "pointer": None}


def test_a_target_that_keeps_moving_under_the_pointer_stops_the_run(monkeypatch):
    # Alternating positions further apart than the tolerance, for every attempt.
    points = [(300.0, 100.0), (400.0, 100.0)] * 6
    cdp, _ = cdp_recorder(points)
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    with pytest.raises(StalePage, match="kept moving"):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "click", "node": 1},
            "rng": random.Random(2), "pointer": (0.0, 0.0),
        })


def test_a_target_that_vanishes_before_input_is_reported_as_stale(monkeypatch):
    cdp, _ = cdp_recorder([None])
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    with pytest.raises(StalePage, match="covered"):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "click", "node": 1},
            "rng": random.Random(2), "pointer": (0.0, 0.0),
        })


def test_a_model_generated_node_id_is_refused_before_any_input(monkeypatch):
    cdp, calls = cdp_recorder([])
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(ValueError, match="Invalid observed node"):
        browser_operation({
            "operation": "act", "session": "test",
            "action": {"id": "e1", "kind": "click", "node": "body"},
        })
    assert calls == []


def test_pointer_position_carries_between_actions(monkeypatch):
    cdp, calls = cdp_recorder([(250.0, 150.0), (250.0, 150.0), (260.0, 160.0), (260.0, 160.0)])
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _seconds: None)
    first = browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e1", "kind": "click", "node": 1},
        "rng": random.Random(1), "pointer": (10.0, 10.0),
    })
    second = browser_operation({
        "operation": "act", "session": "test",
        "action": {"id": "e2", "kind": "click", "node": 2},
        "rng": random.Random(1), "pointer": first["pointer"],
    })
    # The second leg starts where the first one finished.
    assert first["pointer"] == (250.0, 150.0)
    assert second["pointer"] == (260.0, 160.0)
