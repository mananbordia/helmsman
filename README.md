<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Helmsman ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=helmsman)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Measured run: Zürich → London on Google Flights in 7.1 seconds.** It includes a natural-language goal, text generation, and loading waits. That run predates the current action pacing, so new runs take longer.

[Measurements](docs/performance.md) · [Read the loop](helmsman/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/helmsman.git
cd helmsman
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
```

Then run any of the commands under [Use the library](#use-the-library) below.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The example uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from helmsman import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. Change the URL and the goal to run any other task.

`tests/flights_task.py` defines the measured flight task and an independent check on the page it ends on: the route, date, and visible results are read from the page rather than trusted from the model's own answer. The test suite and the measurement both use it.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. Screenshots are opt-in through `screenshots=True` or `record_dir=...`.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Stop at CAPTCHA.** A visible provider widget or challenge page produces a `captcha_detected` block reason before the next model call or browser action. Merely loading a provider script does not stop the run.
- **Stop repeated dead ends.** Stable visible-page signatures ignore recreated DOM node IDs. Repeated no-op actions trigger a corrective model warning and then a block before another identical browser action; unchanged waits and short page cycles are bounded too.
- **Hand off blocked tabs.** A caller can bring the owned tab forward for a human, then explicitly resume after a fresh observation confirms the challenge is gone or the page has changed. No model call runs during human control.
- **Submit search fields.** A populated GET search form offers an Enter action even when its submit button has no visible hit target. The field's ordinary open/focus action is omitted in that state.
- **Move the pointer like a hand.** A click travels along a curved path, decelerates into the target, overshoots and corrects on long moves, and lands off-centre inside the element rather than on its exact middle. The element is resolved again after the travel, because its geometry can change while the pointer is in flight.
- **Type instead of pasting.** A filled field receives one key-down/key-up pair per character with uneven timing, stalled at word and sentence breaks. Inserting a whole value at once is the clearest automation signal available.
- **Scroll in a flick.** One requested scroll becomes several wheel events that decay, sent from the pointer's own position.
- **Keep the pointer alive.** While the model is deciding, the pointer drifts to a plausible resting place instead of freezing for the length of the call.
- **Measure, don't assume.** `scripts/check_detection.py` reports what a page can learn about the browser beyond ordinary use, so a change to the stack can be judged against a recorded baseline. The behavioural work above changes what a page sees as *input*; it does not change what a page sees as *automation*.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** Sample the gap between browser actions around a second rather than fixing it, and recheck freshness after that pause. After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other first reads get at most two animation frames or 50 ms; if the page still looks unchanged, wait one second and read once more before asking the model again. These reads happen after execution is logged. An explicit `WAIT` lasts one second.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](helmsman/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](helmsman/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](helmsman/browser.py) | Browser connection, current geometry, execution |
| [motion.py](helmsman/motion.py) | Pointer paths, aim, typing and scroll timing |
| [detection.py](helmsman/detection.py) | What a page can learn about this browser |
| [loop_guard.py](helmsman/loop_guard.py) | Action/page repetition checks and corrective warning |
| [model.py](helmsman/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](helmsman/questions.py) | Model instructions |

## Evidence and limits

The video is a **7,073 ms** Google Flights run recorded before action pacing was added. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verified the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold. New runs take longer because actions now have a minimum one-second gap.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

CAPTCHA detection is intentionally read-only. It recognizes visible reCAPTCHA, hCaptcha, Turnstile, Arkose, GeeTest, and Friendly Captcha widgets by their frames or containers, Cloudflare challenge-page forms, and visible human-verification challenge overlays. On detection, `status` is `blocked` and `block_reason.code` is `captcha_detected`; no solver or image challenge action runs. This is a bounded heuristic, not proof that every site's challenge is recognized. If using a standalone Browser Use Cloud browser, disable its default automatic CAPTCHA solver with `solveCaptchas: false` to preserve this behavior ([Browser Use documentation](https://docs.browser-use.com/cloud/browser/captcha-handling)).

## Development

```bash
uv run ruff check .
uv run pytest
node --check helmsman/snapshot.js
node --check helmsman/cursor.js
node --check helmsman/detection.js
uv build
```

A visible pointer is drawn inside the page, so it appears in every screenshot and in the live view and you can always see where the agent aims. It is always on and not configurable. The pointer is an ordinary DOM element, and the Page domain is enabled for it, so both are part of what a page can see.

`uv run python scripts/check_detection.py <url>` reports the automation signals a page can see. Run it against the same URL before and after a change to the browser stack, and compare against [the recorded baseline](docs/detection-baseline.json).

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. `uv run python scripts/measure_display.py` measures how many streamed frames a viewer browser actually paints, and compares the polling path against a WebSocket push, which it found to be no faster. `scripts/measure_flights.py` and `scripts/smoke.py` make paid API calls. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
