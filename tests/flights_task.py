"""The flight search used as a measured task, and an independent check on its result.

This is not library behaviour: it defines one task and verifies the page the agent
ends on, without trusting the model's own DONE answer. The measured run and the test
suite both use it, so it lives beside them rather than in an examples folder.
"""

import base64
from urllib.parse import parse_qs, urlparse

URL = "https://www.google.com/travel/flights?hl=en"
GOALS = (
    "Find one-way flights from Zurich to London on September 20, 2026, for one adult in economy. "
    "Stop when matching flight options are visible. Do not select or book a flight."
)


def verify(page):
    """Independent checks on the resulting page, not the model's DONE answer."""
    parsed = urlparse(page["url"])
    encoded = parse_qs(parsed.query).get("tfs", [""])[0]
    try:
        date_in_url = b"2026-09-20" in base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError:
        date_in_url = False
    actions = page["actions"]
    values = {a["label"].strip(): a.get("value") for a in actions}
    flights = [a["label"] for a in actions if "Select flight" in a["label"]]
    checks = {
        "search_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/search",
        "one_way": values.get("Change ticket type. One way") == "One way",
        "origin": values.get("Where from?") == "Zürich",
        "destination": values.get("Where to?") == "London",
        "date": values.get("Departure") == "Sun, Sep 20",
        "year": date_in_url or "departing 2026-09-20" in page["text"],
        "results": bool(flights) and all("Sunday, September 20" in f for f in flights),
    }
    return {"passed": all(checks.values()), "checks": checks, "visible_flights": flights}
