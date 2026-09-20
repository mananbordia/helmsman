# Human-like input

The agent used to click the exact centre of an element with a single synthetic
press, type a value by pasting it, and wait a fixed one second between actions.
All three are shapes a behavioural detector looks for. This document records what
changed, what was measured, and what is deliberately still open.

## What this layer does and does not do

It changes what a page observes as **input**: pointer motion, aim, key events,
wheel events and the timing between them.

It does **not** change what a page observes as **automation**. The debugging
connection, the browser's own flags and the page-visible consequences of the
driver are a separate problem, measured separately below.

Keeping those apart matters, because a page can inspect the second before it
ever sees the first.

## The model

Logic adapted from [ghost-cursor](https://github.com/Xetera/ghost-cursor), which
is the reference implementation most other projects copy. Nothing was ported as
code; `motion.py` reimplements the geometry and timing in pure Python with no new
dependencies.

| Concern | Behaviour |
| --- | --- |
| Path | Cubic Bezier. Curvature `clamp(distance, 2, 200)`, so longer moves bow further and short hops stay near-straight. Control points sit at random points along the line, offset perpendicular on one random side. |
| Sampling | `ceil((fitts(distance, width) + 25) * 3)` points, clamped to 25–90. |
| Timing | `fitts(d, w) = 2·log2(d/w + 1)`, scaled by 85 ms/bit and jittered ±20%. |
| Velocity | Slow-fast-slow, distributed by `acos(1 − 2d)/π` so the pointer eases out of the start and into the target. |
| Overshoot | Only beyond 500 px: travel to a uniformly sampled point in a 120 px disc past the target, then a tighter second pass (`spread = 10`). |
| Aim | Gaussian around the centre with `σ = min(w, h)/6` in absolute pixels, clamped to the inner 10% so it never lands on an edge. |
| Arrival | Two micro-corrections within 3 px, then the press. |
| Press | 40–160 ms hesitate, then press, 40–110 ms hold, then release. |
| Re-resolve | The element is resolved again after the travel. If it moved, the whole approach repeats, up to three times, then the action is reported stale. |
| Typing | One key-down/key-up pair per printable character, lognormal intervals around 105 ms, longer at word and sentence breaks, longer again for shifted characters. Compressed to a 10 s ceiling for long values. |
| Scroll | One distance becomes 2–9 wheel events that decay by 0.72 per step, sent from the pointer's current position. |
| Pacing | Inter-action gaps are lognormal around 0.95 s, with a 7% chance of an extra 1.4–4 s reading pause. |
| Idle | While the model decides, the pointer drifts to a plausible resting place, then the move is joined before anything is dispatched. |

Three deliberate departures from the reference: aim is clustered rather than
uniform, velocity is eased rather than proportional, and typing is per-character
rather than a paste.

Non-ASCII characters have no invented key code. They fall back to a direct
insert, so a value is never silently truncated.

## Measured baseline

`scripts/check_detection.py` was run against `about:blank` on this machine
(Chrome 153). Recorded in [detection-baseline.json](detection-baseline.json).

**Failing:**

- **Headless user agent.** `HeadlessChrome/153.0.0.0` appears verbatim in
  `navigator.userAgent`. This is the bluntest possible signal and needs no
  heuristic to read.
- **Viewport larger than the screen.** The viewport is `1120x780` while the
  screen reports `800x600` — a window bigger than its own display. A default
  headless Chrome reports a small virtual screen; a real one cannot.

**Passing:**

- `navigator.webdriver` is `false`.
- No driver globals were present, including this project's own `__jevFast`.
- `navigator.plugins` is non-empty, `window.chrome` exists, and a known native
  function still reports `[native code]`.
- The `Runtime.enable` leak probe did not fire.

Two caveats on that last one. The probe is the documented heuristic — a page
defines a `stack` getter and watches whether `console.debug` reads it — and it is
indirect, not a direct reading of the domain's state. And the probe itself runs
over `Runtime.evaluate`, so this result should be re-checked rather than treated
as settled.

The practical reading: the browser's *behaviour* was the weak part, and the
*environment* leaks were two cheap fixes.

## The two leaks, closed

`Browser.align_fingerprint` and the screen metrics in `Browser.__init__` close
both. Re-measured in [detection-aligned.json](detection-aligned.json).

| | Before | After |
| --- | --- | --- |
| User agent | `HeadlessChrome/153.0.0.0` | `Chrome/153.0.0.0` |
| Screen against a `1120x780` viewport | `800x600` | `1920x1080` |
| Failing checks | 2 of 8 | 0 of 8 |

Two decisions worth recording:

- The override is built from the browser's own values with the headless marker
  removed, not invented ones. The user agent string and the client hints are
  sanitised together so they keep agreeing with each other — a string that
  disagrees with its own hints is a louder signal than `HeadlessChrome` alone.
  It applies only when the browser actually reports a headless marker, so a
  headed browser is never touched.
- The screen is configurable (`JEV_SCREEN=WxH`, default `1920x1080`) and can
  never be set below the viewport, because that relation is the invariant being
  preserved.

One honest limit: `1920x1080` at a device pixel ratio of 1 describes an ordinary
external display, which is coherent, but it is plausible rather than true. Set
`JEV_SCREEN` to the machine's real display. Running a headful browser on a real
display is better still.

## What remains

- **The `Runtime.enable` leak.** The probe does not detect it, but the probe is
  indirect. Adopting [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
  or [rebrowser-patches](https://github.com/rebrowser/rebrowser-patches) means
  changing the connection layer, because this project drives CDP through Browser
  Harness rather than Playwright. Re-check the probe before assuming the leak is
  absent.
- **Everything the probe cannot see.** It reads what a page can read. Rate
  shaping, request cadence, IP reputation and account history are outside it, and
  the last two usually matter more than anything listed here.

## Visible pointer

`cursor.js` is injected through `Page.addScriptToEvaluateOnNewDocument`, so the
pointer survives navigation and appears in every screenshot the task app shows.
It is always on and not configurable. Two details matter:

- **`Page.enable` is required.** Without it the injection call is accepted and
  returns an identifier, but the script never runs. The domain is therefore
  always enabled.
- **The node starts hidden**, appearing on the first pointer move so it never
  shows as a stray dot in the corner. It hides again only when the pointer leaves
  the window — not when it merely leaves an element, which an earlier
  capture-phase `mouseleave` listener mistook for the pointer leaving the page.

The node and its stylesheet are ordinary page elements, so a page that looks for
them will find them. That is the price of being able to see where the agent aims.
It creates no global, though: a window property named after this project is the
shape of a driver leftover, and the detection probe checks for exactly that.

## Verification

`tests/test_motion.py` and `tests/test_detection.py` cover the geometry, the
timing distributions, keyboard mapping, the dispatched event sequences, target
re-resolution, and the classification of probe findings. Every generator is a
function of a seeded `random.Random`, so a gesture is reproducible and the tests
need no browser.

`scripts/check_guards.py` exercises the real input path in a local browser
without model calls. It is the check that would catch the CDP level disagreeing
with the offline tests.
