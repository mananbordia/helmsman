"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp, drain_events

from . import motion

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
CURSOR = Path(__file__).with_name("cursor.js").read_text()
VIEWPORT_WIDTH = 1120
VIEWPORT_HEIGHT = 780

# Chrome sends the next frame only once the previous one is acknowledged, and the
# acknowledgement cadence drives production rather than merely observing it. With
# a fresh browser for each measurement: 60 fps delivered at a 2 ms cadence, 19 fps
# at 5 ms, 7 fps at 10 ms. The interval is therefore fixed and deliberately tight,
# because the viewer sees a still picture the moment it slips.
PUMP_INTERVAL = 0.002
LIVE_QUALITY = 60

# Headless Chrome advertises itself in the user agent and reports a small virtual
# screen. Both are readable from the page before any input arrives, so the
# advertised browser is aligned with the platform it claims.
DEFAULT_SCREEN = (1920, 1080)

UA_METADATA = """(async () => {
  const data = navigator.userAgentData;
  if (!data) return null;
  const clean = list => (list || []).map(entry => ({
    brand: String(entry.brand).replace(/Headless/g, ''),
    version: String(entry.version)
  }));
  const high = data.getHighEntropyValues
    ? await data.getHighEntropyValues(
        ['platformVersion', 'architecture', 'bitness', 'model', 'fullVersionList', 'wow64'])
    : {};
  return {
    brands: clean(data.brands),
    fullVersionList: clean(high.fullVersionList || data.brands),
    mobile: Boolean(data.mobile),
    platform: data.platform,
    platformVersion: high.platformVersion || '',
    architecture: high.architecture || '',
    model: high.model || '',
    bitness: high.bitness || '',
    wow64: high.wow64 === true
  };
})()"""


def screen_size(env=None):
    """The display size reported to the page, from ``JEV_SCREEN`` as ``WxH``."""
    value = (os.environ if env is None else env).get("JEV_SCREEN", "")
    if not value:
        return DEFAULT_SCREEN
    width, _, height = value.partition("x")
    try:
        size = (int(width), int(height))
    except ValueError:
        raise ValueError("JEV_SCREEN must look like 1920x1080") from None
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("JEV_SCREEN must be positive")
    return size


def sanitized_user_agent(user_agent):
    """Drop the headless marker so the claim matches an ordinary browser."""
    return str(user_agent).replace("Headless", "")

# How many times to re-resolve a target whose geometry moved while the pointer
# travelled towards it. Each attempt is a full approach, so the bound stays small.
APPROACH_ATTEMPTS = 3
RESOLVED_TOLERANCE = 2.0

# Typing is intentionally slow, so long values are compressed to this ceiling.
MAX_TYPE_SECONDS = 10.0

RESOLVE_TARGET = """(action => {
  if (window.__jevFast?.detectCaptcha?.()) return null;
  const e=window.__jevFast?.nodes.get(action.node);
  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
  if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
  if (action.kind==='submit' &&
      (!['INPUT','TEXTAREA'].includes(e.tagName) || !String(e.value||'').trim() ||
       e.closest('form[role="search"]')?.method!=='get')) return null;
  const r=e.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  const u=Number.isFinite(action.u)?action.u:0.5, v=Number.isFinite(action.v)?action.v:0.5;
  const pick=(x,y)=>({x:r.x+r.width*x, y:r.y+r.height*y});
  const candidates=[pick(u,v), pick(0.5, 0.5)];
  for (const point of candidates) {
    if (point.x<0 || point.y<0 || point.x>=innerWidth || point.y>=innerHeight) continue;
    if (!e.contains(document.elementFromPoint(point.x, point.y))) continue;
    return {...point, width:r.width, height:r.height};
  }
  return null;
})("""

SELECT_VALUE = """(action => {
  if (window.__jevFast?.detectCaptcha?.()) return null;
  const e=window.__jevFast?.nodes.get(action.node);
  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
  const r=e.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
      !o.disabled && !o.closest('optgroup[disabled]'))) return null;
  e.value=action.value;
  e.dispatchEvent(new Event('input',{bubbles:true}));
  e.dispatchEvent(new Event('change',{bubbles:true}));
  return {x:r.x+r.width/2, y:r.y+r.height/2, width:r.width, height:r.height};
})("""

# CDP modifier bits are platform-independent: Alt 1, Ctrl 2, Meta 4, Shift 8.
VIRTUAL_KEYS = {
    "Backspace": 8, "Tab": 9, "Enter": 13, "Escape": 27, " ": 32,
    "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
    "Delete": 46,
}
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
    ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}
PUNCTUATION = {
    "-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight",
    "\\": "Backslash", ";": "Semicolon", "'": "Quote", ",": "Comma",
    ".": "Period", "/": "Slash", "`": "Backquote",
}


def key_identity(character):
    """``(key, code, modifiers)`` for one printable US-layout character, or ``None``.

    Anything outside that layout returns ``None`` so the caller can insert it
    directly instead of inventing a key code.
    """
    if character == " ":
        return " ", "Space", 0
    if not character.isascii() or len(character) != 1:
        return None
    if character.isalpha():
        return character, "Key" + character.upper(), 8 if character.isupper() else 0
    base = SHIFTED.get(character, character)
    shifted = 8 if character in SHIFTED else 0
    if base.isdigit():
        return character, "Digit" + base, shifted
    code = PUNCTUATION.get(base)
    return (character, code, shifted) if code else None


def press_character(call, character, rng):
    """Type one character as a real key-down/key-up pair."""
    identity = key_identity(character)
    if identity is None:
        # Anything outside the US layout is inserted directly rather than dropped.
        call("Input.insertText", text=character)
        return
    key, code, modifiers = identity
    virtual = VIRTUAL_KEYS.get(key, ord(key.upper()) if len(key) == 1 and key.isascii() else 0)
    # Meta and Ctrl change meaning; Shift keeps the character literal.
    text = key if not (modifiers & 7) else ""
    body = dict(
        key=key, code=code, modifiers=modifiers,
        windowsVirtualKeyCode=virtual, nativeVirtualKeyCode=virtual,
    )
    call("Input.dispatchKeyEvent", type="keyDown", text=text, unmodifiedText=text, **body)
    # Deliberately not interruptible: the key is already down, so it must be released.
    time.sleep(motion.key_hold(rng))
    call("Input.dispatchKeyEvent", type="keyUp", **body)


def capture_viewport(call, quality=80):
    return call(
        "Page.captureScreenshot", format="jpeg", quality=quality,
        captureBeyondViewport=True,
        clip={"x": 0, "y": 0, "width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT, "scale": 1},
    )["data"]

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Paused(Exception):
    """A pause was requested before the next input reached the page."""


def hold(seconds, pause):
    """Sleep, stopping early when a pause is requested.

    ``Event.wait`` returns as soon as the flag is set, so this makes every pause
    in the action path interruptible without restructuring the loop.
    """
    if pause is None:
        time.sleep(seconds)
    elif pause.wait(seconds):
        raise Paused("Paused before the next input")


def checkpoint(pause):
    """Refuse to touch the page when a pause has already been requested."""
    if pause is not None and pause.is_set():
        raise Paused("Paused before the next input")


class Browser:
    def __init__(self, url):
        ensure_daemon()
        self.rng = random.Random()
        self.pointer = (VIEWPORT_WIDTH / 2, VIEWPORT_HEIGHT / 2)
        self.acting = False
        # Created only when a pause is first requested, so the ordinary path
        # stays a plain sleep.
        self.pause = None
        self.page_enabled = False
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        # A window can never be larger than the display it sits on, so the screen
        # is never allowed to fall below the viewport.
        screen = screen_size()
        self.call(
            "Emulation.setDeviceMetricsOverride",
            width=VIEWPORT_WIDTH, height=VIEWPORT_HEIGHT, deviceScaleFactor=1, mobile=False,
            screenWidth=max(screen[0], VIEWPORT_WIDTH), screenHeight=max(screen[1], VIEWPORT_HEIGHT),
        )
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        # Draw the pointer inside the page, so it appears in every screenshot and
        # in the live view. Without it a viewer cannot see where the agent aims.
        # A new-document script is accepted without Page.enable but never runs, so
        # enabling the domain is required rather than optional.
        self.enable_page()
        # Registered before navigation so the pointer reappears after every page load.
        self.call("Page.addScriptToEvaluateOnNewDocument", source=CURSOR)
        # Before navigation, so the first request the page makes is already aligned.
        # A browser that is not headless is left untouched: the check inside returns
        # early when the user agent carries no headless marker.
        self.align_fingerprint()
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)
        self.next_action_at = time.monotonic() + motion.action_delay(self.rng)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def _random(self):
        """Created on demand so a partially constructed Browser still works."""
        rng = getattr(self, "rng", None)
        if rng is None:
            rng = random.Random()
            self.rng = rng
        return rng

    def evaluate_async(self, expression):
        """Evaluate a promise-returning expression. Best effort: ``None`` on failure."""
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if response.get("exceptionDetails"):
            return None
        return response.get("result", {}).get("value")

    def align_fingerprint(self):
        """Make the advertised browser match the platform it claims.

        Headless Chrome names itself in the user agent and reports a small
        virtual screen. Both are readable from the page before any input arrives,
        and both are cheaper to correct than any amount of pointer realism.

        The override is built from the browser's own values with the headless
        marker removed, rather than invented ones, so the user agent string and
        the client hints stay consistent with each other. A string that
        disagrees with its own client hints is a louder signal than either alone.
        """
        try:
            user_agent = self.evaluate("navigator.userAgent")
            metadata = self.evaluate_async(UA_METADATA)
        except StalePage:
            return
        if not isinstance(user_agent, str) or "Headless" not in user_agent:
            return
        override = {"userAgent": sanitized_user_agent(user_agent)}
        if isinstance(metadata, dict):
            override["userAgentMetadata"] = metadata
        self.call("Emulation.setUserAgentOverride", **override)

    def enable_page(self):
        """Turn on the Page domain once, for the callers that need its events."""
        if not getattr(self, "page_enabled", False):
            self.call("Page.enable")
            self.page_enabled = True

    def _lock(self):
        lock = getattr(self, "_dispatch_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._dispatch_lock = lock
        return lock

    def _pause(self):
        """The pause flag, or ``None`` when no pause has been requested."""
        return getattr(self, "pause", None)

    def paused(self):
        pause = self._pause()
        return pause is not None and pause.is_set()

    def set_paused(self, paused):
        if paused:
            if self._pause() is None:
                self.pause = threading.Event()
            self.pause.set()
        elif self._pause() is not None:
            self.pause.clear()

    def _offsets(self, action):
        rect = action.get("rect") or {}
        return motion.target_offsets(rect.get("w"), rect.get("h"), self._random())

    def move_pointer(self, target):
        """Send a curved pointer move and return the arrival position."""
        rng = self._random()
        start = getattr(self, "pointer", None) or (VIEWPORT_WIDTH / 2, VIEWPORT_HEIGHT / 2)
        points, delays = motion.approach(start, target, motion.DEFAULT_TARGET_WIDTH, rng)
        pause = self._pause()
        for point, delay in zip(points, delays):
            self.call("Input.dispatchMouseEvent", type="mouseMoved", x=point[0], y=point[1])
            if delay > 0:
                hold(delay, pause)
        self.pointer = target
        return target

    def idle_drift(self):
        """Move the pointer once, on a short delay, while the model is thinking.

        A pointer that freezes for the length of a model call is a signal in
        itself. The move is joined by the caller before it dispatches anything,
        and it skips when an action already holds the lock.
        """
        rng = self._random()

        def run():
            time.sleep(rng.uniform(0.12, 0.45))
            try:
                with self._lock():
                    if getattr(self, "acting", False) or self.paused():
                        return
                    self.acting = True
                    try:
                        self.move_pointer(motion.drift_point(VIEWPORT_WIDTH, VIEWPORT_HEIGHT, rng))
                    finally:
                        self.acting = False
            except Exception:
                # Drift is cosmetic. It must never disturb a run.
                pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if page.get("captcha"):
            raise StalePage("CAPTCHA detected; no browser action executed.")
        delay = getattr(self, "next_action_at", 0) - time.monotonic()
        if delay > 0:
            # Interruptible: a pause during pacing must not wait out the gap.
            hold(delay, self._pause())
        else:
            # Pacing already elapsed, so there was no wait above to stop at.
            checkpoint(self._pause())
        # Recheck after pacing: the page or target may have changed while waiting.
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            hold(1.0, self._pause())
        rng = self._random()
        with self._lock():
            self.acting = True
            try:
                result = browser_operation(
                    {
                        "operation": "act",
                        "session": self.session,
                        "action": action,
                        "text": text,
                        "pointer": getattr(self, "pointer", None),
                        "offsets": self._offsets(action),
                        "rng": rng,
                        "pause": self._pause(),
                    }
                )
            finally:
                self.acting = False
        self.pointer = tuple(result.get("pointer") or self.pointer)
        self.next_action_at = time.monotonic() + motion.action_delay(rng)
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def show(self):
        """Bring the owned tab forward for a human without changing its page."""
        if not self.target:
            raise ValueError("The browser tab is no longer available")
        cdp("Target.activateTarget", targetId=self.target)
        self.call("Page.bringToFront")

    def screenshot(self):
        """A JPEG of the current viewport.

        Evidence, not model input: this exists so a stopped run can be looked at
        afterwards, by a person or by a fallback model.
        """
        return capture_viewport(self.call)

    def human_view(self):
        """Read the current screen without asking the model or changing the page."""
        return {
            "screenshot": capture_viewport(self.call),
            "url": self.evaluate("location.href"),
            "width": VIEWPORT_WIDTH,
            "height": VIEWPORT_HEIGHT,
        }

    def human_input(self, event):
        """Forward a human's bounded input to this run's existing CDP tab."""
        if not isinstance(event, dict):
            raise ValueError("Invalid human input")
        kind = event.get("kind")
        if kind in {"pointer_down", "pointer_move", "pointer_up", "wheel"}:
            x, y = event.get("x"), event.get("y")
            if not all(type(value) in {int, float} for value in (x, y)) or not (
                0 <= x < VIEWPORT_WIDTH and 0 <= y < VIEWPORT_HEIGHT
            ):
                raise ValueError("Pointer is outside the browser screen")
            if kind == "wheel":
                delta = event.get("delta")
                if type(delta) not in {int, float} or not -600 <= delta <= 600:
                    raise ValueError("Invalid scroll distance")
                self.call("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=delta)
            else:
                button_down = kind != "pointer_up"
                self.call(
                    "Input.dispatchMouseEvent",
                    type={"pointer_down": "mousePressed", "pointer_move": "mouseMoved",
                          "pointer_up": "mouseReleased"}[kind],
                    x=x, y=y, button="left" if kind != "pointer_move" else "none",
                    buttons=int(button_down), clickCount=int(kind != "pointer_move"),
                )
            return
        if kind == "key":
            key, code, modifiers = event.get("key"), event.get("code"), event.get("modifiers", 0)
            if not isinstance(key, str) or not isinstance(code, str) or not (
                1 <= len(key) <= 24 and 1 <= len(code) <= 32
            ):
                raise ValueError("Invalid keyboard input")
            if type(modifiers) is not int or not 0 <= modifiers <= 15:
                raise ValueError("Invalid keyboard modifiers")
            virtual = {
                "Backspace": 8, "Tab": 9, "Enter": 13, "Escape": 27, " ": 32,
                "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36,
                "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
                "Delete": 46,
            }.get(key, ord(key.upper()) if len(key) == 1 and key.isascii() else 0)
            text = key if len(key) == 1 and not (modifiers & 7) else ""
            params = dict(key=key, code=code, modifiers=modifiers,
                          windowsVirtualKeyCode=virtual, nativeVirtualKeyCode=virtual)
            self.call("Input.dispatchKeyEvent", type="keyDown", text=text, unmodifiedText=text, **params)
            self.call("Input.dispatchKeyEvent", type="keyUp", **params)
            return
        if kind == "text":
            value = event.get("text")
            if not isinstance(value, str) or not 0 < len(value) <= 2000:
                raise ValueError("Invalid pasted text")
            self.call("Input.insertText", text=value)
            return
        raise ValueError("Unsupported human input")

    def start_live(self):
        """Stream the page so a viewer can watch the pointer, not just where it stopped.

        Chrome pushes a frame whenever the page changes. That is the only way to
        see motion: a screenshot requested from outside an action can only ever
        show where that action finished, because the whole action holds the
        dispatch lock. Frames are produced on change only, so an idle page is free.
        """
        if getattr(self, "_live", False):
            return
        self.enable_page()
        self._frame = None
        self._live = True
        self.call(
            "Page.startScreencast", format="jpeg", quality=LIVE_QUALITY,
            maxWidth=VIEWPORT_WIDTH, maxHeight=VIEWPORT_HEIGHT, everyNthFrame=1,
        )
        self._live_thread = threading.Thread(target=self._pump_frames, daemon=True)
        self._live_thread.start()

    def _pump_frames(self):
        """Acknowledge streamed frames, keeping only the newest.

        Acknowledging promptly is what makes Chrome produce frames at all, so the
        interval is fixed and deliberately tight. The harness opens a connection
        per call, so this thread does not contend with the action thread.
        """
        while getattr(self, "_live", False):
            try:
                for event in drain_events():
                    if event.get("session_id") != self.session:
                        continue
                    if event.get("method") != "Page.screencastFrame":
                        continue
                    params = event.get("params") or {}
                    self._frame = params.get("data")
                    self.call("Page.screencastFrameAck", sessionId=params.get("sessionId"))
            except Exception:
                # A dropped frame is cosmetic and must never disturb a run.
                pass
            time.sleep(PUMP_INTERVAL)

    def frame(self):
        """The newest streamed frame, or ``None`` if the page has not changed yet."""
        return getattr(self, "_frame", None)

    def stop_live(self):
        if not getattr(self, "_live", False):
            return
        self._live = False
        thread = getattr(self, "_live_thread", None)
        if thread is not None:
            thread.join(timeout=1.0)
        try:
            self.call("Page.stopScreencast")
        except Exception:
            # The target may already be gone; nothing left to stop.
            pass

    def close(self):
        self.stop_live()
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state.get(k) for k in ("url", "text", "actions", "scroll", "captcha")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def progress_fingerprint(state):
    """Visible page state, excluding per-document node IDs and geometry."""
    content = {
        "url": state.get("url"),
        "title": state.get("title"),
        "text": state.get("text"),
        "scroll": state.get("scroll"),
        "captcha": state.get("captcha"),
        "actions": [
            {key: action.get(key) for key in ("kind", "label", "value", "checked", "selected", "expanded")}
            for action in state.get("actions", [])
        ],
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = dict(request["action"])
        offsets = request.get("offsets")
        if offsets:
            # Where inside the element the pointer aims. Resolved against the
            # element's geometry at the moment of input, not at observation time.
            action["u"], action["v"] = offsets
        kind = action["kind"]
        rng = request.get("rng") or random.Random()
        pointer = request.get("pointer")
        pause = request.get("pause")
        # Checked before any action kind can reach the page. A dropdown is set
        # through the DOM and a scroll dispatches its first wheel event without
        # waiting, so neither is covered by a wait-based check alone.
        checkpoint(pause)
        if kind == "scroll":
            # One burst of wheel events that tapers, at the pointer's own position.
            point = pointer or (VIEWPORT_WIDTH / 2, VIEWPORT_HEIGHT / 2)
            for delta in motion.scroll_deltas(action["delta"], rng):
                call("Input.dispatchMouseEvent", type="mouseWheel", x=point[0], y=point[1], deltaX=0, deltaY=delta)
                hold(rng.uniform(0.02, 0.06), pause)
            return {"executed": action["id"], "pointer": point}
        if kind == "wait":
            return {"executed": action["id"], "pointer": pointer}
        if type(action["node"]) is not int:
            raise ValueError("Invalid observed node")
        # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
        if kind == "select":
            # A dropdown is set through the DOM. There is no pointer gesture to imitate.
            target = evaluate(SELECT_VALUE + json.dumps(action) + ")")
            if target is None:
                raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
            return {"executed": action["id"], "pointer": pointer}
        call_js = RESOLVE_TARGET + json.dumps(action) + ")"
        point = None
        for _attempt in range(APPROACH_ATTEMPTS):
            target = evaluate(call_js)
            if target is None:
                raise StalePage("Target changed or is covered. Observe again.")
            point = (target["x"], target["y"])
            start = pointer or point
            points, delays = motion.approach(start, point, target.get("width") or motion.DEFAULT_TARGET_WIDTH, rng)
            for step, delay in zip(points, delays):
                call("Input.dispatchMouseEvent", type="mouseMoved", x=step[0], y=step[1])
                if delay > 0:
                    hold(delay, pause)
            pointer = point
            # The element can move while the pointer travels. Confirm before pressing.
            settled = evaluate(call_js)
            if settled is None:
                raise StalePage("Target changed while the pointer moved. Observe again.")
            if math.hypot(settled["x"] - point[0], settled["y"] - point[1]) <= RESOLVED_TOLERANCE:
                point = (settled["x"], settled["y"])
                break
        else:
            raise StalePage("Target kept moving under the pointer. Observe again.")
        hesitate, press_seconds = motion.click_delays(rng)
        # Last point at which the page is still untouched.
        hold(hesitate, pause)
        checkpoint(pause)
        call("Input.dispatchMouseEvent", type="mousePressed", x=point[0], y=point[1], button="left", clickCount=1)
        # Deliberately not interruptible: releasing a button that was never pressed
        # is worse than waiting out the hold.
        time.sleep(press_seconds)
        call("Input.dispatchMouseEvent", type="mouseReleased", x=point[0], y=point[1], button="left", clickCount=1)
        if kind == "fill":
            modifier = 4 if sys.platform == "darwin" else 2
            call(
                "Input.dispatchKeyEvent",
                type="keyDown",
                key="a",
                code="KeyA",
                modifiers=modifier,
                commands=["selectAll"],
            )
            call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=modifier)
            # Type the value one key at a time; a paste is the clearest automation signal.
            typed = request.get("text") or ""
            for character, delay in zip(typed, motion.type_intervals(typed, rng, MAX_TYPE_SECONDS)):
                # Checked between characters. Stopping mid-character would leave
                # the key down.
                checkpoint(pause)
                press_character(call, character, rng)
                hold(delay, pause)
        elif kind == "submit":
            for event in ("keyDown", "keyUp"):
                call(
                    "Input.dispatchKeyEvent",
                    type=event,
                    key="Enter",
                    code="Enter",
                    windowsVirtualKeyCode=13,
                    nativeVirtualKeyCode=13,
                )
        return {"executed": action["id"], "pointer": point}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = capture_viewport(call, quality=72)
    return info
