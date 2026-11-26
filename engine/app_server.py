"""The Design Job Finder app: a small page with a "Find new jobs" button, your
settings and the weekly schedule. It opens in its own window (or in your
browser if the window part isn't installed).

It runs only on your Mac. The page is served from 127.0.0.1, which other
computers can't reach, and every action needs a secret code made fresh
each time the app starts, so websites you visit can't use it either.
It quits on its own about 15 minutes after you close the page.

  python engine/app_server.py            open the app (start it if needed)
  python engine/app_server.py --browser  open it in your browser instead
  python engine/app_server.py --serve    run the server in this window
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import fcntl  # Mac and Linux
except ImportError:  # Windows: searches aren't locked, so there's nothing to follow
    fcntl = None

import config_store as store
import history
import schedule
import updates
from settings import CONFIG_PATH, CRASH_MESSAGE, DATA, ROOT, SANDBOX, load_env, scrub

STATIC = Path(__file__).resolve().parent / "app"
STATE_PATH = DATA / ".app_state.json"
WINDOW_PATH = DATA / ".window.json"  # where the window was and how big, for next time
LOGS = DATA / "logs"
RUN_SUMMARY = LOGS / "app_run.json"
RUN_LOG = LOGS / "app_run.log"
LAST_RUN = DATA / "last_run.json"  # older versions; only read when there's no history yet
LOCK_PATH = DATA / ".run.lock"                  # held by whichever search is running
SCHEDULED_SUMMARY = LOGS / "scheduled_summary.json"  # the Monday check's progress
FIRST_PORT = 8785 if SANDBOX else 8765  # its own port, so the two never share saved page settings
IDLE_SECONDS = 15 * 60
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".woff2": "font/woff2"}


# ── Turning the app's form into settings, and back ──────────────────

def settings_for_page() -> dict:
    cfg = store.current_settings()
    locations = cfg.get("locations") or []
    return {
        "field": store.field_choice(cfg),
        "level": store.level_choice(cfg),
        "cities": [l for l in locations if l != "remote"],
        "remote": "remote" in locations or not locations,
        "min_salary": cfg.get("min_salary") or 0,
        "companies": [{"key": key, "slug": slug} for key in store.COMPANY_KEYS.values()
                      for slug in cfg.get(key) or []],
        "tracker": (cfg.get("tracker") or "spreadsheet").lower(),
        # Job sites searched on every check, apart from Adzuna (the page
        # adds that one when it's connected).
        "job_sites": [str(s).replace("_", " ").title()
                      for s in (cfg.get("jobspy") or {}).get("sites", ["indeed"])]
                     if cfg.get("jobspy") else [],
    }


def settings_from_page(form: dict) -> dict:
    level = int(form.get("level") or 1)
    if level not in range(1, len(store.LEVEL_CHOICES) + 1):
        raise ValueError("Pick a level.")
    field = int(form.get("field") or 0) or None
    if field is not None and field not in range(1, len(store.FIELD_CHOICES) + 1):
        raise ValueError("Pick a type of design.")
    role = store.drafts_role(store.current_settings().get("your_role"), level, field)
    cities = [c.strip() for c in form.get("cities") or [] if c.strip()]
    pay = store.parse_money(form.get("min_salary") or 0)
    if pay is None:
        raise ValueError("Type the minimum pay as a number, like 150k.")
    # An empty list would mean "anywhere", the opposite of what turning
    # everything off looks like.
    if not cities and not form.get("remote"):
        raise ValueError("Add a city, or turn on “Include remote jobs”.")
    lists = {key: [] for key in store.COMPANY_KEYS.values()}
    for c in form.get("companies") or []:
        if c.get("key") in lists and c.get("slug") and c["slug"] not in lists[c["key"]]:
            lists[c["key"]].append(c["slug"])
    tracker = "notion" if form.get("tracker") == "notion" else "spreadsheet"
    # No type picked means a hand-written title list, which is left alone.
    titles = {} if field is None else {"title_keywords": store.FIELD_CHOICES[field - 1][1],
                                       "search_terms": store.FIELD_CHOICES[field - 1][2]}
    return {
        **titles,
        "seniority_keywords": store.LEVEL_CHOICES[level - 1][1],
        "your_role": role,
        "locations": [c.lower() for c in cities] + (["remote"] if form.get("remote") else []),
        "search_location": cities[0] if cities else "United States",
        "min_salary": pay,
        "tracker": tracker,
        **lists,
    }


def test_copy():
    """engine/sandbox.py, only ever loaded in the test copy."""
    import sandbox
    return sandbox


def next_check() -> str | None:
    return schedule.next_run(datetime.now()).isoformat() if schedule.is_on() else None


def home_data() -> dict:
    """What the home page shows: the last few weeks of jobs, when the
    latest search finished, and when the next one runs."""
    runs = history.load()
    return {
        "weeks": history.weeks(runs),
        "last_checked": runs[-1]["finished_at"] if runs else None,
        "last_run": read_json(LAST_RUN),
        "next_check": next_check(),
    }


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ── Running a search in the background ──────────────────────────────

def search_running(lock_path: Path) -> bool:
    """True while any search holds the lock (the app's, or the Monday check)."""
    if fcntl is None:
        return False
    try:
        with open(lock_path, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except OSError:
        return False


def log_tail(path: Path, lines: int = 25) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class Runner:
    def __init__(self, command=None, summary: Path = RUN_SUMMARY, log: Path = RUN_LOG,
                 lock: Path = LOCK_PATH, other_summary: Path = SCHEDULED_SUMMARY):
        self.command = command or [sys.executable, "-u", str(ROOT / "engine" / "main.py")]
        self.summary = summary
        self.log = log
        self.lock_path = lock
        self.other_summary = other_summary
        self.process = None
        self.lock = threading.Lock()

    def busy(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> dict:
        with self.lock:
            if self.busy():
                return self.status()
            self.log.parent.mkdir(exist_ok=True)
            self.summary.unlink(missing_ok=True)
            log = open(self.log, "w")
            self.process = subprocess.Popen(
                [*self.command, "--summary", str(self.summary)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            log.close()
        return {"state": "running", "step": "Starting…"}

    def other_search(self) -> dict | None:
        """Progress of a search this app didn't start (the Monday check)."""
        if not search_running(self.lock_path):
            return None
        other = read_json(self.other_summary) or {}
        step = other.get("step") if other.get("state") == "running" else None
        return {"state": "running", "sites": other.get("sites") or [],
                "step": f"The Monday check is running. {step or 'Looking for jobs…'}"}

    def status(self) -> dict | None:
        if self.process is None:
            return self.other_search()
        summary = read_json(self.summary) or {"state": "running", "step": "Starting…"}
        if self.process.poll() is None:
            return summary
        if summary.get("state") == "busy":  # another search had started first: follow it
            return self.other_search() or {"state": "done"}
        if summary.get("state") == "running":  # stopped without saying why
            return {"state": "error", "message": CRASH_MESSAGE,
                    "details": scrub(log_tail(self.log))}
        return summary


# ── The web server ──────────────────────────────────────────────────

class App:
    def __init__(self, port: int, token: str):
        self.port = port
        self.token = token
        self.runner = Runner()
        self.updater = updates.Updater()
        self.last_seen = time.time()

    def state(self) -> dict:
        return {
            "set_up": CONFIG_PATH.exists(),
            "settings": settings_for_page(),
            "levels": [label for label, _ in store.LEVEL_CHOICES],
            "fields": [label for label, *_ in store.FIELD_CHOICES],
            "connected": store.connected(),
            **home_data(),
            "schedule_on": schedule.is_on(),
            "can_schedule": sys.platform == "darwin",
            "run": self.runner.status(),
            "notes": store.read_notes(),
            "update": updates.newer_version(),
            "updating": self.updater.status(),
            "sandbox": test_copy().for_page() if SANDBOX else None,
        }

    def handle(self, path: str, body: dict) -> tuple[int, dict]:
        if path.startswith("/api/scout/"):
            import scout_bridge
            return scout_bridge.handle(path, body)
        if path == "/api/state":
            return 200, self.state()
        if path == "/api/settings":
            store.save_settings(settings_from_page(body))
            return 200, self.state()
        if path == "/api/company":
            return 200, store.lookup_company(str(body.get("name", ""))[:100])
        if path == "/api/keys":
            problems = store.save_keys(str(body.get("anthropic", "")),
                                       str(body.get("adzuna_id", "")),
                                       str(body.get("adzuna_key", "")))
            return 200, {"problems": problems, "connected": store.connected()}
        if path == "/api/notion":
            ok, message = store.connect_notion(str(body.get("token", "")),
                                               str(body.get("link", "")))
            if ok:
                store.save_settings({"tracker": "notion"})
            return 200, {"ok": ok, "message": message, "connected": store.connected()}
        if path == "/api/notes":
            store.save_notes(str(body.get("text", "")))
            return 200, {"ok": True}
        if path == "/api/run":
            if not CONFIG_PATH.exists():
                return 400, {"error": "Answer the setup questions first."}
            return 200, self.runner.start()
        if path == "/api/run/status":
            return 200, {"run": self.runner.status(), **home_data()}
        if path == "/api/open-tracker":
            from open_tracker import open_tracker
            opened, message = open_tracker()
            return 200, {"ok": opened, "message": message}
        if path == "/api/schedule":
            if sys.platform != "darwin":
                return 400, {"error": "Monday checks only work on a Mac."}
            schedule.turn_on() if body.get("on") else schedule.turn_off()
            return 200, {"schedule_on": schedule.is_on(), "next_check": next_check()}
        if path == "/api/update":
            if not updates.can_update():
                raise ValueError("This copy of Design Job Finder can’t update itself.")
            if self.runner.busy() or search_running(LOCK_PATH):
                raise ValueError("Wait for the search to finish, then update.")
            return 200, {"updating": self.updater.start(in_window=bool(body.get("window")))}
        if path == "/api/update/status":
            return 200, {"updating": self.updater.status()}
        if path == "/api/sandbox" and SANDBOX:
            if self.runner.busy() or search_running(LOCK_PATH):
                raise ValueError("Wait for the search to finish, then switch.")
            test_copy().load(str(body.get("scenario", "")))
            return 200, self.state()
        if path == "/api/ping":
            return 200, {"ok": True}
        return 404, {"error": "Not found"}

    def idle(self) -> bool:
        return (time.time() - self.last_seen > IDLE_SECONDS and not self.runner.busy()
                and not self.updater.busy())


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the window quiet
            pass

        def host_ok(self) -> bool:
            # Blocks "DNS rebinding": a website pretending to be localhost.
            return self.headers.get("Host", "") in (f"127.0.0.1:{app.port}",
                                                    f"localhost:{app.port}")

        def send(self, code: int, data: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, code: int, payload: dict) -> None:
            self.send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, b"Forbidden", "text/plain")
            path = urlparse(self.path).path
            name = "index.html" if path == "/" else path.removeprefix("/static/")
            file = (STATIC / name).resolve()
            inside = file.is_relative_to(STATIC.resolve())
            if path != "/" and not path.startswith("/static/") or not inside or \
                    not file.is_file() or file.suffix not in CONTENT_TYPES:
                return self.send(404, b"Not found", "text/plain")
            self.send(200, file.read_bytes(), CONTENT_TYPES.get(file.suffix, "text/plain"))

        def do_POST(self):
            if not self.host_ok():
                return self.send(403, b"Forbidden", "text/plain")
            if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                return self.send_json(403, {"error": "This window is out of date. Click Design Job Finder in your Dock to open it again."})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("bad request")
            except ValueError:
                return self.send_json(400, {"error": "Bad request"})
            app.last_seen = time.time()
            try:
                code, payload = app.handle(urlparse(self.path).path, body)
            except ValueError as e:
                code, payload = 400, {"error": str(e)}
            except Exception as e:  # show it on the page instead of a blank failure
                code, payload = 500, {"error": f"Something went wrong: {e}"}
            self.send_json(code, payload)

    return Handler


def bind(port: int = FIRST_PORT) -> ThreadingHTTPServer:
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), None)
        except OSError:
            continue
    raise OSError("No free port for Design Job Finder")


def serve() -> None:
    load_env()
    token = secrets.token_urlsafe(24)
    server = bind()
    app = App(server.server_address[1], token)
    server.RequestHandlerClass = make_handler(app)
    STATE_PATH.write_text(json.dumps({"port": app.port, "token": token, "pid": os.getpid()}))
    STATE_PATH.chmod(0o600)

    def watch_idle():
        while not app.idle():
            time.sleep(30)
        server.shutdown()

    threading.Thread(target=watch_idle, daemon=True).start()
    if not SANDBOX:
        updates.check_in_background()
    try:
        server.serve_forever()
    finally:
        if (read_json(STATE_PATH) or {}).get("pid") == os.getpid():
            STATE_PATH.unlink(missing_ok=True)


# ── Opening the app (what the Dock icon runs) ───────────────────────

def alive(url: str) -> bool:
    """True if the server at this address (http://127.0.0.1:PORT/#t=TOKEN) answers.
    Asking also counts as activity, so it doesn't quit while a window is open."""
    base, _, token = url.partition("/#t=")
    req = urllib.request.Request(f"{base}/api/ping", data=b"{}", method="POST",
                                 headers={"X-Token": token})
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        return False
    return True


def running_url() -> str | None:
    """The page's address if the app is already running."""
    state = read_json(STATE_PATH)
    if not state:
        return None
    url = f"http://127.0.0.1:{state['port']}/#t={state['token']}"
    return url if alive(url) else None


def open_app(use_browser: bool = False) -> int:
    url = running_url()
    if not url:
        STATE_PATH.unlink(missing_ok=True)
        LOGS.mkdir(exist_ok=True)
        with open(LOGS / "app.log", "a") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"],
                             cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            time.sleep(0.1)
            url = running_url()
            if url:
                break
    if not url:
        print("Design Job Finder couldn't start. See logs/app.log in your job-search folder.")
        return 1
    if use_browser or not open_window(url):
        webbrowser.open(url)
    return 0


def open_window(url: str) -> bool:
    """Shows the page in its own Mac window and waits until it's closed.
    Returns False if the window part (pywebview) isn't installed.

    The server keeps running after the window closes, so a search that's
    underway finishes; it quits on its own once it's been idle a while."""
    try:
        import webview
    except ImportError:
        return False
    look_like_job_finder()
    # Links to other sites (like console.anthropic.com) open in your browser.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    place = window_place(WINDOW_PATH, [(s.x, s.y, s.width, s.height) for s in webview.screens])
    title = "Design Job Finder (test copy)" if SANDBOX else "Design Job Finder"
    window = webview.create_window(title, url, **place, min_size=MIN_WINDOW,
                                   background_color="#111214")

    # Saved every time it changes (a tiny file), so it's kept however the
    # app ends: closing the window, Cmd+Q, or restarting the Mac.
    def resized(width, height):
        place.update(width=int(width), height=int(height))
        save_window_place(WINDOW_PATH, place)

    def moved(x, y):
        place.update(x=int(x), y=int(y))
        save_window_place(WINDOW_PATH, place)

    window.events.resized += resized
    window.events.moved += moved
    threading.Thread(target=stay_connected, args=(url,), daemon=True).start()
    webview.start(private_mode=False)  # keeps saved sort order etc. between launches
    return True


def stay_connected(url: str) -> None:
    """Keeps the window's server running, and if it stops (an update
    restarts it), starts this app over so the window shows the new version."""
    misses = 0
    while True:
        time.sleep(3)
        misses = 0 if alive(url) else misses + 1
        if misses >= 2:
            # Replace this process with a fresh start: new code, new server,
            # the window back where it was.
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])


DEFAULT_WINDOW = {"width": 1280, "height": 820}
MIN_WINDOW = (900, 600)


def window_place(path: Path, screens: list[tuple[int, int, int, int]]) -> dict:
    """Where to open the window: where it was last time, if that still
    fits on a screen you have now (say you unplugged a monitor)."""
    saved = read_json(path) or {}
    try:
        place = {"width": max(int(saved["width"]), MIN_WINDOW[0]),
                 "height": max(int(saved["height"]), MIN_WINDOW[1])}
    except (KeyError, TypeError, ValueError):
        return dict(DEFAULT_WINDOW)
    try:
        x, y = int(saved["x"]), int(saved["y"])
    except (KeyError, TypeError, ValueError):
        return place
    # Keep the position only if the window's top-left corner area is on a screen.
    for sx, sy, sw, sh in screens:
        if sx <= x + 100 <= sx + sw and sy <= y + 20 <= sy + sh:
            place.update(x=x, y=y)
            break
    return place


def save_window_place(path: Path, place: dict) -> None:
    try:
        path.write_text(json.dumps(place))
    except OSError:
        pass  # not worth bothering anyone about


def look_like_job_finder() -> None:
    """The window runs inside Python, so without this the menu bar and
    Dock would say "Python" and show Python's rocket icon."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
    except ImportError:
        return
    info = NSBundle.mainBundle().infoDictionary()
    info["CFBundleName"] = "Design Job Finder"
    info["CFBundleDisplayName"] = "Design Job Finder"
    icon = ROOT / "assets" / "JobFinder.icns"
    if icon.exists():
        image = NSImage.alloc().initWithContentsOfFile_(str(icon))
        NSApplication.sharedApplication().setApplicationIconImage_(image)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        sys.exit(open_app(use_browser="--browser" in sys.argv))
