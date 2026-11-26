"""Shared helpers: where files live, reading settings and keys.

Everything is resolved from the project folder, so the scripts work no
matter which folder you run them from (or when double-clicked).

Your personal files (settings, keys, tracker, history…) live in DATA,
which is the project folder too, unless JOBSEARCH_DATA_DIR points
somewhere else. The test copy of the app (engine/sandbox.py) uses that
to keep its made-up files apart from your real ones.
"""
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGED = os.environ.get("JOBSEARCH_SCOUT_PACKAGED") == "1"
SANDBOX = bool(os.environ.get("JOBSEARCH_DATA_DIR", "").strip()) and not PACKAGED
DATA = (
    Path.home() / "Library" / "Application Support" / "Design Job Finder Scout"
    if PACKAGED else
    Path(os.environ["JOBSEARCH_DATA_DIR"]).expanduser().resolve()
    if SANDBOX else ROOT
)

if PACKAGED:
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
CONFIG_PATH = DATA / "config.yaml"
ENV_PATH = DATA / ".env"

# Keys the app reads. In the test copy, ones exported in your shell
# profile are ignored, so only the test copy's own .env counts.
KEY_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
             "NOTION_TOKEN", "NOTION_DATABASE_ID")
if SANDBOX:
    DATA.mkdir(parents=True, exist_ok=True)
    for _name in KEY_NAMES:
        os.environ.pop(_name, None)


def save_file(path: Path, text: str, private: bool = False) -> None:
    """Saves text as UTF-8 without ever leaving a half-written file: it
    writes a temporary copy next to it, then swaps that in. If anything
    goes wrong, the old file is still there, untouched. private=True
    makes it readable by you only, from the first byte."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    mode = 0o600 if private else 0o644
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # in case an old leftover copy had other permissions
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_env(path: Path = ENV_PATH) -> None:
    """Reads KEY=value lines from .env into the environment. Values that
    are already set (for example in your shell profile) win, so older
    setups that export keys in ~/.zshrc keep working."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def save_env(updates: dict, path: Path = ENV_PATH) -> None:
    """Adds or replaces KEY=value lines in .env, keeping everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    save_file(path, "\n".join(out) + "\n", private=True)  # keys: readable by you only
    for k, v in updates.items():
        os.environ[k] = v


def env(name: str | None) -> str | None:
    """An environment value, or None if it's missing or blank."""
    if not name:
        return None
    value = os.environ.get(name, "").strip()
    return value or None


def load_config() -> dict:
    """Reads config.yaml, or explains how to create it."""
    if not CONFIG_PATH.exists():
        print("No settings found yet.")
        print("Open the Design Job Finder app to create them.")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config


def resolve_path(p: str) -> Path:
    """Relative paths in config.yaml are relative to the folder holding it."""
    path = Path(p).expanduser()
    return path if path.is_absolute() else DATA / path


# ── Problems shown on the page ──────────────────────────────────────

CRASH_MESSAGE = ("Something unexpected stopped the check. Try again. If it keeps happening, "
                 "use Copy details to ask someone for help.")

_SECRET = re.compile(r"sk-ant-[\w-]+|\b(?:secret|ntn)_\w+|"
                     r"\b(app_key|app_id|api_key|apikey|token|key)=[^&\s'\"]+", re.I)


def scrub(text: str) -> str:
    """Hides keys and secrets in error details before they're shown or
    copied, so "Copy details" never hands someone your keys."""
    return _SECRET.sub(lambda m: f"{m.group(1)}=[hidden]" if m.group(1) else "[hidden]", text or "")
