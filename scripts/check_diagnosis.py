"""Ask the vision fallback about a filed stop. Makes one paid provider call.

Run it once a task has been stopped and filed under the app's artifacts/incidents.

    uv run python scripts/check_diagnosis.py [incident.json]

With no argument it uses the most recent filing. Set GEMINI_API_KEY in the app's .env
first; without it the script says so instead of guessing.
"""

import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.parent / "jev-task-app"
INCIDENTS = APP / "artifacts" / "incidents"


def load_environment():
    """The app's .env, since that is where the provider keys live."""
    for line in (APP / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_incident(argument):
    if argument:
        return Path(argument)
    filings = sorted(INCIDENTS.glob("*.json"))
    if not filings:
        raise SystemExit(f"No filed stops in {INCIDENTS}. Run a task until it stops.")
    return filings[-1]


def main():
    from jev_ultrafast import diagnose

    load_environment()
    if not diagnose.configured():
        raise SystemExit("Set GEMINI_API_KEY in jev-task-app/.env, then run this again.")

    record = load_incident(sys.argv[1] if len(sys.argv) > 1 else None)
    incident = json.loads(record.read_text())
    picture = incident.get("screenshot_file")
    if picture:
        incident["screenshot"] = base64.b64encode((record.parent / picture).read_bytes()).decode()

    print(f"  filing   : {record.name}")
    print(f"  stopped  : {json.dumps(incident.get('block_reason'))}")
    print(f"  page     : {incident.get('url')}")
    print(f"  picture  : {picture or '(none)'}")
    print("  asking   : the vision fallback")
    result = diagnose.diagnose(incident, incident.get("goal", ""))
    print()
    print(f"  layer    : {result['layer']}")
    print(f"  route    : {result['route']}")
    print(f"  confidence: {result['confidence']}")
    if result["message"]:
        print(f"  message  : {result['message']}")
    if result["needs"]:
        print(f"  needs    : {result['needs']}")
    if result["reason"]:
        print(f"  abstained: {result['reason']}")


if __name__ == "__main__":
    main()
