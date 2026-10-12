"""Process-wide pacing for Groq calls so parallel workflow nodes respect RPM limits."""

from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_last_request_at = 0.0


def wait_for_groq_slot() -> None:
    """Space Groq requests; set GROQ_MIN_REQUEST_INTERVAL to tune for the account."""
    global _last_request_at
    interval = max(0.0, float(os.getenv("GROQ_MIN_REQUEST_INTERVAL", "7")))
    with _lock:
        now = time.monotonic()
        wait = _last_request_at + interval - now
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
