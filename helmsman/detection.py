"""What a page can learn about this browser beyond ordinary use.

The behavioural layer in ``motion.py`` changes what a page observes as *input*.
This module measures the separate question: what a page observes as
*automation*. Run it before and after any change to the browser stack, against
the same URL, so the effect is measured rather than assumed.

``summarize`` is pure, so the classification can be tested without a browser.
"""

from pathlib import Path

PROBE = Path(__file__).with_name("detection.js").read_text()

LEVELS = {"pass": 0, "info": 1, "warn": 2, "fail": 3}


def _size(value):
    """Parse a ``"1120x780"`` reading, or ``None`` when it is unusable."""
    try:
        width, _, height = str(value).partition("x")
        return int(width), int(height)
    except (TypeError, ValueError):
        return None


def summarize(findings):
    """Classify probe findings, worst first. Pure function over a findings dict."""
    present = findings.get("driverGlobals") or []
    viewport = _size(findings.get("viewport"))
    screen = _size(findings.get("screen"))
    # A window cannot be larger than the display it sits on. A default headless
    # Chrome reports a small virtual screen, which is visible from the page.
    oversized = bool(viewport and screen and (viewport[0] > screen[0] or viewport[1] > screen[1]))
    checks = [
        (
            "fail",
            "CDP Runtime.enable leak",
            findings.get("runtimeLeak") is True,
            "A page-defined stack getter was read after console.debug.",
            "Adopt a client that avoids Runtime.enable, or evaluate in isolated execution contexts.",
        ),
        (
            "fail",
            "navigator.webdriver",
            findings.get("webdriver") is True,
            "The standard automation flag is set.",
            "It is set by the debugging connection itself, so it needs a different client.",
        ),
        (
            "fail",
            "Driver globals",
            bool(present),
            "Present: " + ", ".join(present),
            "Remove injected page objects, or accept that a page can see them.",
        ),
        (
            "fail",
            "Headless user agent",
            findings.get("headlessUserAgent") is True,
            str(findings.get("userAgent")),
            "Run non-headless, or make the user agent match the platform it claims.",
        ),
        (
            "fail",
            "Viewport larger than the screen",
            oversized,
            f"viewport {findings.get('viewport')} exceeds screen {findings.get('screen')}",
            "Set a window size and screen that agree, as a real display would.",
        ),
        (
            "warn",
            "Plugin count",
            findings.get("plugins") == 0,
            "navigator.plugins is empty.",
            "Typical of bundled or headless Chrome.",
        ),
        (
            "warn",
            "window.chrome",
            findings.get("chromeObject") not in (None, "object"),
            "window.chrome is " + str(findings.get("chromeObject")),
            "Expected in Chrome; its absence points at a non-browser runtime.",
        ),
        (
            "warn",
            "Native function integrity",
            findings.get("nativeToString") is False,
            "A known native function no longer reports [native code].",
            "Something patched a built-in; prefer configuration over monkey-patching.",
        ),
    ]
    return [
        {
            "level": level if triggered else "pass",
            "name": name,
            "detail": detail if triggered else "",
            "advice": advice if triggered else "",
        }
        for level, name, triggered, detail, advice in checks
    ]


def worst(results):
    """The highest severity present, so a caller can set an exit status."""
    if not results:
        return "pass"
    return max((result["level"] for result in results), key=lambda level: LEVELS[level])


def inventory(findings):
    """The raw observed values worth recording between runs."""
    keys = (
        "userAgent", "platform", "languages", "hardwareConcurrency", "deviceMemory",
        "viewport", "screen", "devicePixelRatio", "webglVendor",
    )
    return {key: findings.get(key) for key in keys}
