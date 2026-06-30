"""Portable memory cap for heavy fq-derive / serve_corpus builds. macOS can't
lower RLIMIT_AS, so a WATCHDOG THREAD polls RSS and os._exit's (137) once it
crosses $BENCH_MEM_CAP_GB (default 24 GB; 0 disables), before the machine OOMs.
"""
from __future__ import annotations

import contextlib
import os
import resource
import sys
import threading
import time

_DEFAULT_CAP_GB = 36.0
_EXIT_CODE = 137                 # conventional OOM-kill code


def _cap_bytes() -> float:
    try:
        gb = float(os.getenv("BENCH_MEM_CAP_GB", _DEFAULT_CAP_GB))
    except ValueError:
        gb = _DEFAULT_CAP_GB
    return gb * 1024 ** 3 if gb > 0 else 0.0


def _current_rss_bytes() -> int:
    """Process high-water RSS. ru_maxrss is BYTES on macOS, KILOBYTES on Linux."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru if sys.platform == "darwin" else ru * 1024


@contextlib.contextmanager
def memory_guard(cap_bytes: float | None = None, *, poll_seconds: float = 1.0,
                 label: str = "build"):
    """Run the body under a memory watchdog. If RSS crosses `cap_bytes` (default
    $BENCH_MEM_CAP_GB, 24 GB), print and hard-exit before the machine OOMs."""
    cap = _cap_bytes() if cap_bytes is None else cap_bytes
    if not cap:                                  # disabled
        yield
        return
    stop = threading.Event()

    def _watch():
        while not stop.wait(poll_seconds):
            rss = _current_rss_bytes()
            if rss > cap:
                sys.stderr.write(
                    f"\n[memcap] ABORT: {label} RSS {rss/1024**3:.1f} GB exceeded "
                    f"cap {cap/1024**3:.1f} GB (set BENCH_MEM_CAP_GB to change, 0 "
                    f"to disable). Killing to protect the machine.\n")
                sys.stderr.flush()
                os._exit(_EXIT_CODE)

    t = threading.Thread(target=_watch, name="memcap-watchdog", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def install_memory_guard(cap_bytes: float | None = None, *, label: str = "process"):
    """Start a process-lifetime watchdog (no context manager). For CLI entrypoints
    where the whole run should be capped. Idempotent."""
    if getattr(install_memory_guard, "_installed", False):
        return
    cap = _cap_bytes() if cap_bytes is None else cap_bytes
    if not cap:
        return
    cm = memory_guard(cap, label=label)
    cm.__enter__()                               # never exit — lives for the process
    install_memory_guard._installed = True
