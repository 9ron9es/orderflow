"""
Lightweight JSONL metrics sink (no external services required).
"""

from __future__ import annotations

import json
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


class MetricsLogger:
    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        self._path = self._dir / f"orderflow_metrics_{day}.jsonl"

    def log_event(self, event: str, fields: dict[str, Any]) -> None:
        # Keep schema compatible with `dashboard.py`:
        #   {"event": "...", "ts": <unix_ms>, "data": {...}}
        row = {
            "ts": int(time.time() * 1000),
            "event": event,
            "data": fields,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
