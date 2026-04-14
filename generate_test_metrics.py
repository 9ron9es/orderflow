#!/usr/bin/env python3
"""
Quick test to generate some sample metrics data for the dashboard.
"""

import json
import time
from pathlib import Path

# Create metrics directory
metrics_dir = Path("orderflow/logs/metrics")
metrics_dir.mkdir(parents=True, exist_ok=True)

# Generate sample data
sample_events = [
    {"event": "entry_rejected", "data": {"failed": ["no_signal"], "price": 42510.5, "side": "BUY"}},
    {"event": "entry_rejected", "data": {"failed": ["risk_max_position"], "price": 42511.2, "side": "SELL"}},
    {"event": "entry_signal", "data": {"price": 42512.8, "qty": 0.1, "side": "BUY", "signal_type": "imbalance_continuation_long"}},
    {"event": "order_submitted", "data": {"order_id": "ORDER-001", "client_order_id": "CLIENT-001", "side": "BUY", "qty": 0.1}},
    {"event": "order_accepted", "data": {"order_id": "ORDER-001", "side": "BUY", "qty": 0.1}},
    {"event": "order_filled", "data": {"order_id": "ORDER-001", "side": "BUY", "qty": 0.1, "price": 42513.0, "commission": 0.425}},
    {"event": "position_opened", "data": {"position_id": "POS-001", "side": "LONG", "qty": 0.1, "entry_price": 42513.0}},
]

# Write to today's metrics file
from datetime import datetime, UTC
day = datetime.now(UTC).strftime("%Y-%m-%d")
metrics_file = metrics_dir / f"orderflow_metrics_{day}.jsonl"

print(f"Writing sample data to {metrics_file}")

with metrics_file.open("w") as f:
    for i, event in enumerate(sample_events):
        row = {
            "ts": int(time.time() * 1000) - (len(sample_events) - i) * 5000,  # Stagger timestamps
            **event
        }
        f.write(json.dumps(row, default=str) + "\n")

print(f"Generated {len(sample_events)} sample events")
print("Now run: python dashboard.py")
