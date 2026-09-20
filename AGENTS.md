# Jev Ultrafast

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- TypeSafe chooses an operation and operation-specific target heads in one request. Consume only the selected operation's target.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text LLM. Cache a stale retry's value only while its entire helper input is identical.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional and the model does not consume them. A viewer sees a CDP screencast through `Browser.start_live()` instead, and only one stream works at a time: the daemon hands out a single event queue that a drain empties.
- Report only text a person can see. Text clipped away by an overflowing ancestor, or drawn transparently, is not page content; nor is a snippet quoted inside a result a statement from the page itself.
- The pointer is always on and must leave no global behind. Its injected script needs `Page.enable`, because a new-document script is accepted without it but never runs.
- The user agent alignment is unconditional and applies only when the browser actually claims to be headless.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep the measured task, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check on jev_ultrafast/{snapshot,cursor,detection}.js, uv build.
Real-browser checks, no model calls: `scripts/check_guards.py` and `scripts/check_detection.py <url>`. Paid calls: `scripts/smoke.py`, `scripts/measure_flights.py`.
