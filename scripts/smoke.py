"""Explicit live smoke: the local fixture plus paid model APIs. Not run by pytest.

The fixture page is served in-process on a free loopback port, so this needs no
separately started server.
"""

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from helmsman import Agent

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "tests"
GOALS = (
    "Use the destination search and filters to find Design stays in Lisbon with Free cancellation, "
    "then open Casa Flora."
)


def load_environment():
    """Read the project's .env without overwriting values already set."""
    path = Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


class QuietFixture(SimpleHTTPRequestHandler):
    """Serves the fixture directory without writing a request log."""

    def log_message(self, *_args):
        pass


def start_fixture():
    """Serve the fixture on a free loopback port. Returns ``(base_url, server)``."""
    handler = partial(QuietFixture, directory=str(FIXTURE_DIRECTORY))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-actions", type=int, default=15)
    parser.add_argument("--goal", default=GOALS)
    args = parser.parse_args()
    load_environment()
    base_url, server = start_fixture()
    output = Path("artifacts/dynamic/fixture") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=True)
    print(f"Trace: {output}", flush=True)
    try:
        with Agent(f"{base_url}/fixture.html?scenario=travel", args.goal) as agent:
            try:
                for state in agent.run():
                    history = state["history"]
                    print(
                        state["elapsed_ms"], "ms", len(history), "actions",
                        history[-1]["action"] if history else "", flush=True,
                    )
                    (output / "state.json").write_text(json.dumps(state, indent=2))
                    if len(history) >= args.max_actions:
                        raise RuntimeError(f"Diagnostic stopped at {args.max_actions} actions")
            finally:
                state = agent.snapshot()
                try:
                    state["verification_text"] = agent.browser.evaluate("document.body.innerText")
                finally:
                    (output / "state.json").write_text(json.dumps(state, indent=2))
            assert state["status"] == "done"
            assert state["page"]["url"].endswith("#casa-flora")
            assert "Your filters: Design · Free cancellation enabled · Destination Lisbon" in state["verification_text"]
            result = {
                "ms": state["elapsed_ms"],
                "verified": True,
                "decisions": len(state["decisions"]),
                "actions": len(state["history"]),
            }
            print(json.dumps(result, indent=2))
            (output / "summary.json").write_text(json.dumps(result, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
