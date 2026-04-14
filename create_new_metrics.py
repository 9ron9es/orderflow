#!/usr/bin/env python3
"""Force create a new metrics file with different name"""

import json
import time
from pathlib import Path

metrics_dir = Path("orderflow/logs/metrics")
metrics_dir.mkdir(parents=True, exist_ok=True)

new_file = metrics_dir / "orderflow_pipeline_metrics_2026-04-05.jsonl"

print(f"Creating new metrics file: {new_file}")

# Add some test data
test_data = [
    {"ts": int(time.time() * 1000), "event": "market_update", "data": {"price": 67287.8, "cvd": 5318.68, "cvd_trend": "rising", "ob_imbalance": -0.75, "imbalance_ratio": 0.75, "absorption": None}},
    {"ts": int(time.time() * 1000), "event": "signal_evaluation", "data": {"long_signals": ["test_signal"], "short_signals": [], "failed_conditions": []}},
    {"ts": int(time.time() * 1000), "event": "entry_rejected", "data": {"failed": ["test_rejection"]}},
]

with new_file.open("w") as f:
    for event in test_data:
        f.write(json.dumps(event) + "\n")

print(f"Created {len(test_data)} test entries in {new_file}")
print(f"File size: {new_file.stat().st_size} bytes")
