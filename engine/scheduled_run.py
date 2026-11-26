"""The weekly search that runs on its own (see schedule.py). Finds jobs
like "Find new jobs" does, then shows a Mac notification with the result.
"""
import json
import subprocess
import sys
from datetime import datetime

from settings import ROOT, DATA

SUMMARY = DATA / "logs" / "scheduled_summary.json"


def notify(message: str) -> None:
    script = f'display notification {json.dumps(message)} with title "Design Job Finder"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def message_for(summary: dict, exit_code: int) -> str:
    """The notification text; empty when there's nothing to say."""
    if summary.get("state") == "busy":
        return ""  # you were already searching from the app; that search reports itself
    if summary.get("state") == "done":
        added = len(summary.get("added") or [])
        if added:
            return (f"Found {added} new job{'s' if added != 1 else ''}. "
                    "Open Design Job Finder to see them.")
        return "No new jobs this week."
    return summary.get("message") or "This week's check didn't finish. Open Design Job Finder to try again."


def main() -> int:
    SUMMARY.parent.mkdir(exist_ok=True)
    SUMMARY.unlink(missing_ok=True)
    print(f"--- {datetime.now():%Y-%m-%d %H:%M} weekly search ---", flush=True)
    code = subprocess.run([sys.executable, str(ROOT / "engine" / "main.py"),
                           "--summary", str(SUMMARY)], cwd=ROOT).returncode
    try:
        summary = json.loads(SUMMARY.read_text())
    except (OSError, ValueError):
        summary = {}
    message = message_for(summary, code)
    if message:
        notify(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
