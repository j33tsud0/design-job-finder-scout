"""The optional weekly search ("Check for me every Monday morning").

Uses macOS's own scheduler (launchd): a small file in
~/Library/LaunchAgents tells the Mac to run scheduled_run.py every
Monday at 9:00. If the Mac is asleep or off then, it runs the next time
it wakes. Turning the checkbox off removes the file.

In the test copy of the app (engine/sandbox.py) the switch only flips a
note in the test folder, so it never replaces your real Monday check.
"""
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from settings import DATA, ROOT, SANDBOX, PACKAGED

LABEL = (
    "com.productdesignjobfinder.scout.weekly"
    if PACKAGED else "com.productdesignjobfinder.weekly"
)
WEEKDAY, HOUR, MINUTE = 1, 9, 0  # Monday, 9:00


def next_run(now: datetime) -> datetime:
    """When the Monday search runs next. launchd counts Sunday as 0 and
    Monday as 1; Python counts Monday as 0."""
    target = now.replace(hour=HOUR, minute=MINUTE, second=0, microsecond=0)
    target += timedelta(days=(WEEKDAY - 1 - now.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return target


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_content(root: Path = ROOT, python: str = sys.executable) -> dict:
    logs = (DATA if PACKAGED else root) / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [python, str(root / "engine" / "scheduled_run.py")],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Weekday": WEEKDAY, "Hour": HOUR, "Minute": MINUTE},
        "StandardOutPath": str(logs / "scheduled.log"),
        "StandardErrorPath": str(logs / "scheduled.log"),
        "ProcessType": "Background",
        **({"EnvironmentVariables": {
            "JOBSEARCH_SCOUT_PACKAGED": "1"
        }} if PACKAGED else {}),
    }


def _launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True)


PRETEND_PATH = DATA / ".schedule_on"  # the test copy's switch


def is_on() -> bool:
    if SANDBOX:
        return PRETEND_PATH.exists()
    return plist_path().exists()


def turn_on() -> None:
    if SANDBOX:
        PRETEND_PATH.touch()
        return
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (DATA / "logs").mkdir(exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")  # in case an older one is loaded
    with open(path, "wb") as f:
        plistlib.dump(plist_content(), f)
    _launchctl("bootstrap", domain, str(path))


def turn_off() -> None:
    if SANDBOX:
        PRETEND_PATH.unlink(missing_ok=True)
        return
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
