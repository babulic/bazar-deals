from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone

_phase = "start"
_lock = threading.Lock()
_heartbeat: threading.Thread | None = None
_stop = threading.Event()


def emit(message: str) -> None:
    """Print hunt progress so GitHub Actions shows it while the job is still running.

    The Actions *web UI* streams stdout live. `gh run view --log` does not, until
    the job ends. Workflow notices and the job summary are visible on the run page
    without waiting for that download.
    """
    text = (message or "").strip()
    if not text:
        return
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"{stamp} {text}"
    print(line, flush=True)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::notice title=hunt::{_gha_escape(text)}", flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            try:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write(f"- `{stamp}` {text}\n")
            except OSError:
                pass


def set_phase(name: str) -> None:
    global _phase
    with _lock:
        _phase = name or "start"


def current_phase() -> str:
    with _lock:
        return _phase


def start_heartbeat(interval_seconds: float = 60.0) -> None:
    global _heartbeat
    if _heartbeat is not None:
        return
    _stop.clear()

    def _beat() -> None:
        while not _stop.wait(interval_seconds):
            emit(f"still running ({current_phase()})")

    _heartbeat = threading.Thread(target=_beat, name="hunt-heartbeat", daemon=True)
    _heartbeat.start()


def stop_heartbeat() -> None:
    global _heartbeat
    _stop.set()
    _heartbeat = None


def _gha_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
