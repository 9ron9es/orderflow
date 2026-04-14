"""
MetricsLogger — writes structured JSONL events for the live dashboard.

Each line is a JSON object:
  {"event": "<type>", "ts": <unix_ms>, "data": {...}}

The dashboard (dashboard.py) tails this file and renders it live.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class MetricsLogger:
    """
    Append-only structured event logger.

    Creates one file per UTC day:
        <metrics_dir>/orderflow_YYYY-MM-DD.jsonl

    The dashboard auto-discovers the latest file so log rotation
    is handled transparently.
    """

    def __init__(self, metrics_dir: str) -> None:
        self._dir  = Path(metrics_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path: Path | None = None
        self._fh = None
        self._current_date: str = ""
        self._open_file()

    # ── Public API ──────────────────────────────────────────────────────────────

    def log_event(self, event: str, data: dict[str, Any]) -> None:
        """
        Write a structured event.

        Parameters
        ----------
        event : str
            Event type key. Recognized by dashboard:
              entry_signal, exit, entry_rejected,
              risk_halt, error, warning
        data : dict
            Arbitrary payload — all values should be JSON-serialisable.
        """
        self._maybe_rotate()

        record = {
            "event": event,
            "ts":    int(time.time() * 1000),   # unix ms
            "data":  self._sanitise(data),
        }
        try:
            if self._fh:
                self._fh.write(json.dumps(record) + "\n")
                self._fh.flush()
        except OSError as exc:
            # Non-fatal — don't crash the bot over a log write
            print(f"[MetricsLogger] write error: {exc}")

    def log_error(self, msg: str, exc: Exception | None = None) -> None:
        payload: dict[str, Any] = {"msg": msg}
        if exc is not None:
            payload["exc"] = type(exc).__name__
            payload["detail"] = str(exc)
        self.log_event("error", payload)

    def log_warning(self, msg: str) -> None:
        self.log_event("warning", {"msg": msg})

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    # ── Internal ────────────────────────────────────────────────────────────────

    def _open_file(self) -> None:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._current_date = today
        self._path = self._dir / f"orderflow_{today}.jsonl"
        try:
            self._fh = self._path.open("a", buffering=1)   # line-buffered
        except OSError as exc:
            print(f"[MetricsLogger] could not open log file: {exc}")
            self._fh = None

    def _maybe_rotate(self) -> None:
        """Roll over to a new file at UTC midnight."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self.close()
            self._open_file()

    @staticmethod
    def _sanitise(data: dict) -> dict:
        """Make values JSON-serialisable (convert Decimal, Quantity, etc.)."""
        out = {}
        for k, v in data.items():
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = str(v)
        return out