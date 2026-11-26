"""Optional native discovery engine."""

import json
import subprocess
import threading
from pathlib import Path

import config_store as store

BINARY = Path(__file__).resolve().parents[1] / "scout" / "scout"
SCAN_LOCK = threading.Lock()

MAX_QUERIES = 12


def suggestions():
    cfg = store.current_settings()

    terms = cfg.get("search_terms") or [
        cfg.get("your_role") or "designer"
    ]

    queries = []

    for term in terms[:3]:
        term = str(term).strip()[:100]

        queries.extend([
            f'"{term}" "we are hiring"',
            f'"{term}" "looking for" startup',
            f'"{term}" "first hire"',
            f'site:linkedin.com/posts "{term}" hiring',
        ])

    queries.extend([
        '"building our design team" hiring',
        '"creative technologist" opportunity',
        '"founding designer" startup',
    ])

    return list(dict.fromkeys(queries))[:MAX_QUERIES]


def handle(path, body):
    if path == "/api/scout/state":
        return 200, {
            "configured": True,
            "queries": suggestions(),
        }

    if path != "/api/scout/scan":
        return 404, {"error": "Unknown discovery action"}

    queries = body.get("queries")

    if (
        not isinstance(queries, list)
        or not 1 <= len(queries) <= MAX_QUERIES
        or any(
            not isinstance(q, str)
            or not q.strip()
            or len(q.encode("utf-8")) > 512
            or "\x00" in q
            for q in queries
        )
    ):
        return 400, {"error": "Invalid discovery queries"}

    if not SCAN_LOCK.acquire(blocking=False):
        return 409, {"error": "A discovery scan is already running"}

    try:
        import re

        # Prefer the first selected query's occupational term.
        quoted = re.findall(r'"([^"]+)"', queries[0])
        term = quoted[0] if quoted else queries[0]
        term = term.strip()[:100]

        try:
            proc = subprocess.run(
                [str(BINARY), term],
                capture_output=True,
                text=True,
                timeout=25,
                check=True,
            )

            records = json.loads(proc.stdout)
            found = {}

            for record in records:
                url = record["url"]
                record["queries"] = [term]
                found.setdefault(url, record)

            return 200, {
                "results": list(found.values()),
                "failures": [],
            }

        except subprocess.CalledProcessError as exc:
            return 200, {
                "results": [],
                "failures": [{
                    "query": term,
                    "error": exc.stderr.strip()[:200],
                }],
            }

        except (
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
        ):
            return 200, {
                "results": [],
                "failures": [{
                    "query": term,
                    "error": "Public discovery request failed",
                }],
            }

    finally:
        SCAN_LOCK.release()
