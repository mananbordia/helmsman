"""Human-like pointer, typing and scroll behaviour.

Pure geometry and timing. Nothing here talks to a browser, reads a file or uses
unseeded randomness, so every value can be reproduced from a seeded
``random.Random``. ``browser.py`` turns the numbers into CDP events.

The movement model follows the approach popularised by ghost-cursor
(https://github.com/Xetera/ghost-cursor): cubic Bezier paths whose curvature
scales with distance, durations derived from Fitts's law, an overshoot-and-
correct pass on long moves, and a target point placed off-centre inside the
element.

Three deliberate differences from that reference:

* Aim offsets are Gaussian around the centre rather than uniform across the
  whole box. People aim at the middle and miss by a few pixels; they do not
  distribute themselves evenly over a button.
* Distances are sampled with a slow-fast-slow velocity profile, so the pointer
  accelerates and decelerates instead of crossing at constant speed.
* Typing produces per-character intervals with stalls at word boundaries rather
  than a single paste.

This is a behavioural layer. It changes what the page observes as *input*. It
does not change what the page observes as *automation*: ``Runtime.enable``,
``navigator.webdriver``, the remote-debugging port and the automation profile
are all visible to a page before any pointer event occurs.
"""

import math
import random

MIN_SPREAD = 2.0
MAX_SPREAD = 200.0
DEFAULT_TARGET_WIDTH = 100.0
MIN_STEPS = 25
MAX_STEPS = 90

OVERSHOOT_RADIUS = 120.0
OVERSHOOT_THRESHOLD = 500.0
OVERSHOOT_SPREAD = 10.0

TARGET_INSET = 0.10
ARRIVAL_MOVES = 2
ARRIVAL_RADIUS = 3.0

SECONDS_PER_BIT = 0.085
MIN_MOVE_SECONDS = 0.12
MAX_MOVE_SECONDS = 2.4

MEAN_ACTION_SECONDS = 0.95
LONG_PAUSE_CHANCE = 0.07

MEAN_KEY_SECONDS = 0.105


def clamp(value, low, high):
    return max(low, min(high, value))


def fitts(distance, width):
    """Fitts's-law index of difficulty: log2(D/W + 1), in bits."""
    return math.log2(distance / max(width, 1.0) + 1.0)


def _cubic(start, first, second, end, t):
    u = 1.0 - t
    return (
        u * u * u * start[0] + 3 * u * u * t * first[0] + 3 * u * t * t * second[0] + t * t * t * end[0],
        u * u * u * start[1] + 3 * u * u * t * first[1] + 3 * u * t * t * second[1] + t * t * t * end[1],
    )


def _distance(start, end):
    return math.hypot(end[0] - start[0], end[1] - start[1])


def polyline_length(points):
    return sum(_distance(a, b) for a, b in zip(points, points[1:]))


def _anchors(start, end, spread, rng):
    """Two control points offset perpendicular to the direct line."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    span = math.hypot(dx, dy) or 1.0
    normal = (dy / span, -dx / span)
    side = rng.choice((-1.0, 1.0))
    control = []
    for _ in range(2):
        along = rng.random()
        offset = spread * rng.random() * side
        control.append((start[0] + dx * along + normal[0] * offset, start[1] + dy * along + normal[1] * offset))
    return sorted(control)


def path(start, end, width=DEFAULT_TARGET_WIDTH, rng=None, spread=None, steps=None):
    """Sample the points to dispatch, excluding ``start``. The last point is ``end``."""
    rng = rng or random.Random()
    span = _distance(start, end)
    if span < 1.0:
        return [end]
    if spread is None:
        # Longer moves bow out further. A short hop is nearly straight.
        spread = clamp(span, MIN_SPREAD, MAX_SPREAD)
    first, second = _anchors(start, end, spread, rng)
    if steps is None:
        steps = int(clamp(math.ceil((fitts(span, width) + 25.0) * 3), MIN_STEPS, MAX_STEPS))
    return [_cubic(start, first, second, end, index / steps) for index in range(1, steps + 1)]


def move_duration(start, end, width=DEFAULT_TARGET_WIDTH, rng=None):
    """Seconds for one uninterrupted move, from distance and target size."""
    rng = rng or random.Random()
    seconds = fitts(_distance(start, end), width) * SECONDS_PER_BIT * rng.uniform(0.85, 1.25)
    return clamp(seconds, MIN_MOVE_SECONDS, MAX_MOVE_SECONDS)


def step_delays(points, duration):
    """Split ``duration`` across a path with a slow-fast-slow velocity profile.

    ``points`` must include the starting position. Velocity peaks mid-flight, so
    the pointer eases out of the start and eases into the target instead of
    moving at a constant rate.
    """
    total = polyline_length(points) or 1.0
    delays, travelled, previous = [], 0.0, 0.0
    for current, following in zip(points, points[1:]):
        travelled += _distance(current, following)
        reached = duration * math.acos(1.0 - 2.0 * min(1.0, travelled / total)) / math.pi
        delays.append(max(0.0, reached - previous))
        previous = reached
    return delays


def overshoot_point(target, rng, radius=OVERSHOOT_RADIUS):
    """A uniformly sampled point in a disc around the target."""
    angle = rng.random() * 2.0 * math.pi
    reach = radius * math.sqrt(rng.random())
    return (target[0] + reach * math.cos(angle), target[1] + reach * math.sin(angle))


def arrival_points(target, rng, count=ARRIVAL_MOVES, radius=ARRIVAL_RADIUS):
    """Small settling corrections after the pointer reaches the target."""
    return [
        (target[0] + rng.uniform(-radius, radius), target[1] + rng.uniform(-radius, radius))
        for _ in range(count)
    ]


def approach(start, target, width=DEFAULT_TARGET_WIDTH, rng=None):
    """Return ``(points, delays)`` for one approach, overshooting when far away.

    ``points`` excludes ``start`` and ends on ``target``.
    """
    rng = rng or random.Random()
    points, delays = [], []
    arrive = target
    far = _distance(start, target) > OVERSHOOT_THRESHOLD
    if far:
        overshoot = overshoot_point(target, rng)
        leg = path(start, overshoot, width, rng)
        span = [start, *leg]
        points += leg
        delays += step_delays(span, move_duration(start, overshoot, width, rng))
        start = overshoot
        width = DEFAULT_TARGET_WIDTH
    leg = path(start, arrive, width, rng, spread=OVERSHOOT_SPREAD if far else None)
    points += leg
    delays += step_delays([start, *leg], move_duration(start, arrive, width, rng))
    for settle in arrival_points(arrive, rng):
        points.append(settle)
        delays.append(rng.uniform(0.012, 0.045))
    return points, delays


def target_offsets(width, height, rng, inset=TARGET_INSET):
    """Fractions ``(u, v)`` of an element box, clustered near its centre.

    Aim error is roughly absolute rather than proportional, so the spread comes
    from the shorter side of the element and is converted back into a fraction
    of each axis. The result stays inside the element's inset region.
    """
    side = max(min(width or 1.0, height or 1.0), 1.0)
    sigma = side / 6.0
    u = 0.5 + rng.gauss(0.0, sigma / max(width or 1.0, 1.0))
    v = 0.5 + rng.gauss(0.0, sigma / max(height or 1.0, 1.0))
    return (clamp(u, inset, 1.0 - inset), clamp(v, inset, 1.0 - inset))


def click_delays(rng):
    """``(hesitate, hold)``: pause before pressing, then press duration."""
    return rng.uniform(0.04, 0.16), rng.uniform(0.04, 0.11)


def key_hold(rng):
    return rng.uniform(0.02, 0.06)


def type_intervals(text, rng, max_total=None):
    """Per-character delays. Fast bursts, stalled at word and sentence breaks.

    ``max_total`` rescales the whole sequence when a caller needs an upper bound
    on wall time, which keeps unusually long values from stalling a run.
    """
    delays = []
    for character in text:
        delay = clamp(rng.lognormvariate(math.log(MEAN_KEY_SECONDS), 0.34), 0.04, 0.85)
        if character == " ":
            delay += rng.uniform(0.02, 0.10)
        elif character in ".!?,;:":
            delay += rng.uniform(0.06, 0.26)
        elif character.isupper() or character in '!@#$%^&*()_+{}|:"<>?~':
            delay += rng.uniform(0.01, 0.05)
        delays.append(delay)
    total = sum(delays)
    if max_total and total > max_total:
        scale = max_total / total
        delays = [delay * scale for delay in delays]
    return delays


def scroll_deltas(total, rng, notches=None):
    """Split one scroll distance into wheel events that taper like a real flick.

    The decay is steep enough that jitter cannot make a later notch larger than
    an earlier one, so the burst always reads as a decaying flick.
    """
    distance = abs(total)
    if distance < 1.0:
        return []
    direction = 1.0 if total > 0 else -1.0
    count = notches or int(clamp(round(distance / rng.uniform(95.0, 150.0)), 2, 9))
    weights = [rng.uniform(0.9, 1.1) * (0.72**index) for index in range(count)]
    scale = distance / sum(weights)
    return [direction * weight * scale for weight in weights]


def action_delay(rng):
    """Gap between two distinct actions, occasionally including a reading pause."""
    delay = rng.lognormvariate(math.log(MEAN_ACTION_SECONDS), 0.42)
    if rng.random() < LONG_PAUSE_CHANCE:
        delay += rng.uniform(1.4, 4.0)
    return clamp(delay, 0.35, 6.0)


def drift_point(width, height, rng):
    """A plausible resting place for the pointer while the model is deciding."""
    return (rng.uniform(0.15, 0.85) * width, rng.uniform(0.15, 0.85) * height)
