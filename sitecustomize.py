"""
sitecustomize.py is imported automatically by Python (if present on sys.path).

We use it to make Windows console output robust:
- Force UTF-8 text streams for stdout/stderr
- Use backslashreplace so prints/logs never crash on uncommon unicode (e.g. arrows →)

This prevents recurring `'charmap' codec can't encode ...` crashes when running Uplink/CLI in PowerShell.
"""

from __future__ import annotations

import io
import os
import sys


def _wrap(stream):
    if hasattr(stream, "buffer"):
        return io.TextIOWrapper(
            stream.buffer,
            encoding="utf-8",
            errors="backslashreplace",
            line_buffering=True,
        )
    # Fallback: attempt reconfigure if available
    try:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
    except Exception:
        pass
    return stream


if sys.platform == "win32":
    # Encourage UTF-8 everywhere (doesn't affect already-open streams, but helps child processes).
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        sys.stdout = _wrap(sys.stdout)
    except Exception:
        pass
    try:
        sys.stderr = _wrap(sys.stderr)
    except Exception:
        pass

