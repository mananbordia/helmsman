"""Report what a page can learn about this browser beyond ordinary use.

Run against the same URL before and after any change to the browser stack:

    uv run python scripts/check_detection.py https://example.com

Exit status is 1 when any check fails, so a change can be judged against a
recorded baseline instead of a feeling.
"""

import json
import sys

from jev_ultrafast.browser import Browser
from jev_ultrafast.detection import PROBE, inventory, summarize, worst


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "about:blank"
    browser = Browser(url)
    try:
        findings = browser.evaluate(PROBE)
    finally:
        browser.close()
    if not isinstance(findings, dict):
        print("The page prevented the probe from running; nothing was measured.")
        return 1
    results = summarize(findings)
    print(json.dumps({"inventory": inventory(findings), "checks": results}, indent=2))
    for result in results:
        if result["level"] in {"fail", "warn"}:
            print(f"[{result['level']}] {result['name']}: {result['detail']}")
            print(f"        {result['advice']}")
    failures = sum(1 for result in results if result["level"] == "fail")
    print(f"\n{worst(results)}: {failures} failure(s) across {len(results)} checks")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
