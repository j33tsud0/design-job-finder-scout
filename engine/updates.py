"""Updates: knowing when there's a newer Design Job Finder, and installing it.

About once a day the app reads one small file (VERSION) from GitHub to see
whether there's a newer version. Nothing about you is sent. "Update" runs
the same install line people paste into Terminal; it keeps your settings,
notes, keys and tracker.

To offer an update to everyone, raise the number in VERSION.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from settings import DATA, ROOT, scrub

REPO = os.environ.get("JOBSEARCH_REPO") or "eriklevitch/design-job-finder"
VERSION_PATH = ROOT / "VERSION"
CHECK_PATH = DATA / ".update_check.json"  # the newest version GitHub had, and when we asked
LOG_PATH = DATA / "logs" / "update.log"
CHECK_EVERY = 24 * 60 * 60


def parse(version: str) -> tuple[int, ...]:
    """'1.10' -> (1, 10), so versions compare as numbers, not text."""
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (AttributeError, ValueError):
        return ()


def current() -> str:
    try:
        return VERSION_PATH.read_text().strip()
    except OSError:
        return ""


def can_update() -> bool:
    """Only installed copies update themselves. A copy made with git (a
    developer's) would have its work overwritten, so it never offers."""
    return not (ROOT / ".git").exists() and bool(current())


def _read_check() -> dict:
    try:
        data = json.loads(CHECK_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def newer_version() -> str | None:
    """The newer version to offer, from the last check; None if there isn't one."""
    if not can_update():
        return None
    latest = str(_read_check().get("latest") or "")
    return latest if parse(latest) > parse(current()) else None


def fetch_latest(timeout: float = 5) -> str | None:
    url = f"https://raw.githubusercontent.com/{REPO}/main/VERSION"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            latest = r.read(100).decode().strip()
    except Exception:
        return None  # offline, or GitHub is slow: try again next time
    return latest if parse(latest) else None


def check(now: float | None = None, fetch=fetch_latest) -> None:
    """Asks GitHub for the newest version, at most once a day."""
    if not can_update():
        return
    now = now or time.time()
    if now - float(_read_check().get("checked_at") or 0) < CHECK_EVERY:
        return
    latest = fetch()
    if latest:
        try:
            CHECK_PATH.write_text(json.dumps({"latest": latest, "checked_at": now}))
        except OSError:
            pass


_checking = threading.Lock()


def check_in_background() -> None:
    def run():
        if _checking.acquire(blocking=False):
            try:
                check()
            finally:
                _checking.release()
    threading.Thread(target=run, daemon=True).start()


def install_command() -> list[str]:
    """The install line, as the app runs it. JOBSEARCH_SOURCE_DIR (used
    for testing) installs from a local folder instead of GitHub."""
    local = os.environ.get("JOBSEARCH_SOURCE_DIR")
    if local:
        return ["bash", str(Path(local) / "install.sh")]
    url = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"
    return ["bash", "-c", 'set -o pipefail; curl -fsSL "$1" | bash', "update", url]


class Updater:
    def __init__(self, command=None, log: Path = LOG_PATH):
        self.command = command
        self.log = log
        self.process = None

    def busy(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, in_window: bool) -> dict:
        from settings import PACKAGED

        if PACKAGED:
            raise ValueError("Scout Edition cannot use the upstream installer.")

        if self.busy():
            return self.status()
        self.log.parent.mkdir(exist_ok=True)
        env = {**os.environ, "JOBSEARCH_DIR": str(ROOT), "JOBSEARCH_REPO": REPO}
        if in_window:
            env["JOBSEARCH_FROM_APP"] = "1"  # the window reopens itself; don't open another
        with open(self.log, "w") as log:
            # Its own session, so it keeps going when it stops this app to restart it.
            self.process = subprocess.Popen(
                self.command or install_command(), cwd=ROOT, env=env, stdout=log,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        return {"state": "running"}

    def status(self) -> dict | None:
        if self.process is None:
            return None
        code = self.process.poll()
        if code is None or code == 0:
            # Once it's done, the installer restarts the app, so this is
            # rarely seen as finished.
            return {"state": "running" if code is None else "done"}
        try:
            details = "\n".join(self.log.read_text(errors="replace").splitlines()[-25:])
        except OSError:
            details = ""
        return {"state": "error",
                "message": "The update didn’t finish. Check your internet connection and try again.",
                "details": scrub(details)}
