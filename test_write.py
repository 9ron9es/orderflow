#!/usr/bin/env python3
"""Force create metrics file to test permissions"""

from pathlib import Path
import json
import time

metrics_dir = Path("orderflow/logs/metrics")
metrics_dir.mkdir(parents=True, exist_ok=True)

test_file = metrics_dir / "orderflow_metrics_2026-04-05.jsonl"

print(f"Writing to: {test_file}")
print(f"Directory exists: {metrics_dir.exists()}")
print(f"Directory writable: {oct(metrics_dir.stat().st_mode)[-3:]}")

try:
    with test_file.open("w") as f:
        for i in range(3):
            row = {
                "ts": int(time.time() * 1000),
                "event": "test_event",
                "data": {"test": i, "price": 42513.0 + i}
            }
            f.write(json.dumps(row) + "\n")
    print("✅ Successfully wrote test data")
    
    print(f"File contents:")
    print(test_file.read_text())
    
except Exception as e:
    print(f"❌ Error: {e}")
