"""Run one task from the console, and report what happened.

    uv run python scripts/run_task.py --url https://example.com --goal "..."

Reads the app's .env for the provider keys, because that is where they live in this
workspace. Prints a line per step, and when the run stops it prints the stop, the
evidence that was captured, and what the vision fallback made of it -- including whether
its advice was applied, and whether the next action changed anything.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ENV = ROOT.parent / "jev-task-app" / ".env"


def load_environment():
    for candidate in (APP_ENV, ROOT / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
        return candidate
    return None


def steps(state):
    return state.get("history") or []


def report(state, seen):
    """One line per new step, as it happens."""
    for action in steps(state)[seen:]:
        usage = action.get("usage") or {}
        tokens = f"{usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)} tok" if usage else ""
        changed = "page updated" if action.get("page_changed") else "page unchanged"
        parts = [part for part in (
            f"{action.get('step', seen + 1):>3}",
            f"{action.get('operation') or action.get('kind', ''):<9}",
            (action.get("action") or "")[:46],
            changed,
            f"{action.get('elapsed_ms', 0) / 1000:.1f}s",
            tokens,
        ) if part]
        print("   " + "  ".join(parts), flush=True)
    return len(steps(state))


def report_stop(state):
    reason = state.get("block_reason")
    if not reason:
        return
    incident = state.get("incident") or {}
    print()
    print(f"  stopped      : {json.dumps(reason)}")
    print(f"  page         : {(incident.get('url') or '')[:78]}")
    print(f"  evidence     : {len(incident.get('text') or '')} chars of text, "
          f"{'a picture' if incident.get('screenshot') else 'no picture'}")
    escalations = state.get("escalations") or []
    if not escalations:
        print("  fallback     : not configured, or nothing worth asking")
        return
    last = escalations[-1]
    print(f"  fallback     : {last.get('count', len(escalations))} attempt(s), "
          f"layer={last.get('layer')} route={last.get('route')} "
          f"confidence={last.get('confidence')}")
    if last.get("message"):
        print(f"  asked Jev    : {last['message']}")
    if last.get("needs"):
        print(f"  needs a human: {last['needs']}")
    if last.get("reason"):
        print(f"  abstained    : {last['reason']}")
    print(f"  advice used  : {bool(last.get('applied'))}")
    outcome = last.get("outcome")
    if outcome:
        print(f"  next action  : {outcome.get('action')} -> "
              f"{'changed the page' if outcome.get('page_changed') else 'changed nothing'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--max-steps", type=int, default=12,
                        help="stop after this many actions, whatever the agent decides")
    args = parser.parse_args()

    loaded = load_environment()
    print(f"  env          : {loaded}")

    from helmsman import Agent

    seen, state = 0, None
    agent = Agent(args.url, args.goal)
    try:
        print()
        for state in agent.run():
            seen = report(state, seen)
            if len(steps(state)) >= args.max_steps:
                print(f"\n  stopped by --max-steps ({args.max_steps})")
                break
        report_stop(state or agent.snapshot())
    finally:
        state = agent.snapshot()
        _summary(state)
        # A blocked run keeps its tab: that is the one you want to look at, or take over.
        keep = state.get("status") == "blocked" or os.environ.get("JEV_KEEP_OPEN")
        if keep:
            print(f"  browser      : left open at {state.get('page', {}).get('url', '')[:60]}")
        else:
            agent.close()


def _summary(state):
    usage = {}
    for call in (state.get("decisions") or []) + (state.get("text_calls") or []):
        for key, value in (call.get("usage") or {}).items():
            usage[key] = usage.get(key, 0) + value
    print()
    print(f"  status       : {state.get('status')}")
    print(f"  steps        : {len(steps(state))}  in {state.get('elapsed_ms', 0) / 1000:.1f}s")
    print(f"  tokens       : {usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out")
    print(f"  page         : {(state.get('page') or {}).get('url', '')[:78]}")


if __name__ == "__main__":
    sys.exit(main())
