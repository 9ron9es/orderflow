#!/usr/bin/env python3
"""
Quick diagnostic to verify live trader is logging events.
Run this in a separate terminal while live trader is running.
"""

import sys
from pathlib import Path
import json
import time

sys.path.insert(0, str(Path(__file__).parent))

def main():
    metrics_dir = Path("orderflow/logs/metrics")
    
    if not metrics_dir.exists():
        print("❌ Metrics directory doesn't exist")
        return
    
    # Find latest metrics file
    files = sorted(metrics_dir.glob("orderflow_metrics_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("❌ No metrics file found")
        return
    
    metrics_file = files[0]
    print(f"✅ Found metrics file: {metrics_file.name}")
    
    # Read all events
    events = []
    try:
        with open(metrics_file) as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    except json.JSONDecodeError as e:
        print(f"❌ JSON decode error: {e}")
        return
    
    print(f"✅ {len(events)} events in log")
    
    if not events:
        print("❌ No events logged yet - waiting for evaluations...")
        return
    
    # Count by event type
    event_counts = {}
    for event in events:
        ev_type = event.get("event", "unknown")
        event_counts[ev_type] = event_counts.get(ev_type, 0) + 1
    
    print("\nEvent counts:")
    for ev_type, count in sorted(event_counts.items()):
        print(f"  {ev_type:30s} {count:4d}")
    
    # Show last event
    if events:
        last = events[-1]
        print(f"\nLast event ({last.get('event')}):")
        print(f"  Time: {last.get('ts')}")
        print(f"  Data: {json.dumps(last.get('data', {}), indent=2)}")
    
    # Check if we're getting entry_rejected events
    rejected_count = event_counts.get("entry_rejected", 0)
    if rejected_count > 0:
        print(f"\n✅ Getting signal evaluations! ({rejected_count} rejections)")
    else:
        print("\n⚠️  No entry_rejected events yet - evaluations may not be running")

if __name__ == "__main__":
    main()
