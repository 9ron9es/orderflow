#!/usr/bin/env python3
"""
Lightweight health checks for local ops (Redis optional, kill-switch file).

Usage::

    python -m orderflow.nautilus.ops.health_check [--kill-switch-path PATH]
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path


def check_kill_switch(path: Path | None) -> bool:
    if path is None:
        return True
    if path.exists():
        print(f"KILL_SWITCH active: {path}", file=sys.stderr)
        return False
    return True


def check_redis(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError as exc:
        print(f"Redis unreachable {host}:{port} ({exc})", file=sys.stderr)
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kill-switch-path", type=Path, default=None)
    p.add_argument("--redis-host", default="127.0.0.1")
    p.add_argument("--redis-port", type=int, default=6379)
    p.add_argument("--skip-redis", action="store_true")
    args = p.parse_args()

    ok = check_kill_switch(args.kill_switch_path)
    if not args.skip_redis:
        ok = check_redis(args.redis_host, args.redis_port) and ok

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
