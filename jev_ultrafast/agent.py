"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, Paused, StalePage, progress_fingerprint
from .loop_guard import action_key, detect_loop, redundant_choice
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


class Agent:
    def complete_observation(self, before_page):
        state = self.state
        before_progress = progress_fingerprint(before_page)
        after_progress = progress_fingerprint(state["page"])
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        action = state["history"][-1]
        action.update(
            page_changed=after_progress != before_progress,
            before_progress=before_progress,
            after_progress=after_progress,
            url=state["page"]["url"],
            elapsed_ms=state["elapsed_ms"],
        )
        if state["record"]:
            (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        if self.stop_for_captcha():
            return self.snapshot()
        state["no_progress_count"] = (
            state.get("no_progress_count", 0) + 1
            if not action["page_changed"] and action["kind"] != "wait"
            else 0
        )
        reason = detect_loop(state["history"][state.get("loop_since", 0) :])
        if reason:
            state["status"] = "blocked"
            state["block_reason"] = reason
        else:
            state["status"] = "ready"
            state["block_reason"] = None
        return self.snapshot()

    def recover_observation(self):
        """Read a navigating page again without replaying the browser action."""
        state = self.state
        before_page = state["page"]
        deadline = time.monotonic() + 5
        while True:
            try:
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                break
            except StalePage:
                if time.monotonic() >= deadline:
                    state["status"] = "blocked"
                    state["block_reason"] = {"code": "page_unavailable"}
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                time.sleep(0.1)
        if state["history"] and state["history"][-1]["page_changed"] is None:
            return self.complete_observation(before_page)
        if self.stop_for_captcha():
            return self.snapshot()
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        return self.snapshot()

    def __init__(self, url, goals, *, record_dir=None, screenshots=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            block_reason=None,
            human_control=False,
            no_progress_count=0,
            loop_since=0,
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def stop_for_captcha(self):
        captcha = self.state["page"].get("captcha")
        if not captcha:
            return False
        self.state["decision"] = None
        self.state["status"] = "blocked"
        self.state["block_reason"] = {"code": "captcha_detected", **captcha}
        if self.state["started_at"] is not None:
            self.state["elapsed_ms"] = round((time.perf_counter() - self.state["started_at"]) * 1000)
        return True

    def hold_if_paused(self):
        """A paused run makes no model call and touches no page.

        Returns ``True`` when the run should stop here. The comparison is written
        as ``is not True`` so a stand-in browser in tests is not mistaken for a
        paused one.
        """
        browser = self.state.get("browser")
        if browser is None or browser.paused() is not True:
            return False
        self.state["decision"] = None
        self.state["status"] = "paused"
        return True

    def human_view(self):
        if self.state["status"] != "blocked" or not self.state["human_control"]:
            raise ValueError("Take control before viewing the live browser")
        live_page = self.state["browser"].observe(screenshot=False)
        return {
            **self.state["browser"].human_view(),
            "ready_to_resume": not live_page.get("captcha")
            and live_page["fingerprint"] != self.state["page"]["fingerprint"],
        }

    def start_live(self):
        """Stream the page so a viewer sees the pointer move, not only where it stopped."""
        browser = self.state.get("browser")
        if browser is not None:
            browser.start_live()

    def stop_live(self):
        """Stop streaming. Only one stream can exist per machine, so a run that can no
        longer change the page must give the shared event queue back."""
        browser = self.state.get("browser")
        if browser is not None:
            browser.stop_live()

    def capture_screen(self):
        """The newest streamed frame, falling back to the last observed screenshot."""
        browser = self.state.get("browser")
        if browser is None:
            raise ValueError("Start a task first")
        page = self.state.get("page") or {}
        return {"screenshot": browser.frame() or page.get("screenshot"), "url": page.get("url")}

    def human_input(self, event):
        if self.state["status"] != "blocked" or not self.state["human_control"]:
            raise ValueError("Take control before using the browser")
        self.state["browser"].human_input(event)

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "pause":
            # Idempotent, and deliberately does not touch the page. A step already
            # in flight stops at its next checkpoint.
            state["browser"].set_paused(True)
            state["decision"] = None
            if state["status"] not in {"done", "blocked"}:
                state["status"] = "paused"
        elif name == "continue":
            state["browser"].set_paused(False)
            if state["status"] == "paused":
                state["status"] = "ready"
        elif name == "handoff":
            if state["status"] != "blocked":
                raise ValueError("Human takeover is available only when the task is blocked")
            state["browser"].show()
            state["decision"] = None
            state["human_control"] = True
        elif name == "resume":
            if state["status"] != "blocked":
                raise ValueError("The task is not waiting for human help")
            if len(state["history"]) >= MAX_STEPS or len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("The run budget is exhausted; start a new task")
            before = state["page"]
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            self.pending_text = None
            if self.stop_for_captcha() or state["page"]["fingerprint"] == before["fingerprint"]:
                return self.snapshot()
            state["status"] = "ready"
            state["block_reason"] = None
            state["human_control"] = False
            state["no_progress_count"] = 0
            state["loop_since"] = len(state["history"])
        elif name in {"tick", "predict", "act"} and state.get("human_control"):
            raise ValueError("Human control is active; resume the task first")
        elif name == "tick":
            try:
                self.command("predict", {})
                if state["status"] in {"blocked", "paused"}:
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                return self.recover_observation()
            except Paused:
                # Nothing reached the page, so no action is recorded and the
                # decision is dropped. Resuming observes and chooses again.
                state["decision"] = None
                state["status"] = "paused"
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if self.hold_if_paused():
                return self.snapshot()
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if self.stop_for_captcha():
                return self.snapshot()
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            # Keep the pointer alive while the model decides, then settle it before acting.
            drift = state["browser"].idle_drift()
            try:
                state["decision"] = choose(
                    state["page"], state["goal"], state["history"], state.get("loop_since", 0)
                )
            finally:
                if drift is not None:
                    drift.join(timeout=1.0)
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if self.hold_if_paused():
                return self.snapshot()
            if self.stop_for_captcha():
                return self.snapshot()
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["block_reason"] = {"code": "model_blocked"} if selected == "BLOCKED" else None
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            repeated = redundant_choice(
                state["history"][state.get("loop_since", 0) :], progress_fingerprint(page), action
            )
            if repeated:
                if not state["browser"].fresh(page, action):
                    raise StalePage("Page changed before loop check. Observe again.")
                state["status"] = "blocked"
                state["block_reason"] = {"code": "no_progress", "actions": repeated}
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            try:
                state["browser"].act(action, page, text=text)
            except Paused:
                # A pause stops the step at the last checkpoint before input, so the
                # page is untouched and no action is recorded.
                self.pending_text = None
                state["status"] = "paused"
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "action_key": action_key(action, text),
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            if (
                action["kind"] != "wait"
                and not state["page"].get("captcha")
                and progress_fingerprint(state["page"]) == progress_fingerprint(page)
            ):
                # A slow page may react after the first read. Inspect once more before
                # spending another model call or treating the action as no progress.
                time.sleep(1.0)
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            return self.complete_observation(page)
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
