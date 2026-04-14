#!/usr/bin/env python3
"""
Market-close any open USDT-M position for one symbol.
Uses the same env vars as Nautilus: DEMO -> BINANCE_DEMO_* + testnet futures URL;
live -> BINANCE_API_KEY / BINANCE_API_SECRET + fapi.binance.com.

  source .venv/bin/activate
  export BINANCE_DEMO_API_KEY=... BINANCE_DEMO_API_SECRET=...
  python scripts/flatten_binance_futures.py --demo
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse

import requests

BASE_LIVE = "https://fapi.binance.com"
BASE_DEMO = "https://testnet.binancefuture.com"


def _sign(secret: str, params: dict) -> str:
    params = {k: v for k, v in params.items() if v is not None}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 60_000
    q = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode("utf-8"), q.encode("utf-8"), hashlib.sha256).hexdigest()
    return q + "&signature=" + sig


def _post(base: str, path: str, key: str, secret: str, params: dict) -> requests.Response:
    body = _sign(secret, params)
    url = base + path
    return requests.post(
        url,
        headers={"X-MBX-APIKEY": key, "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
        timeout=60,
    )


def _get(base: str, path: str, key: str, secret: str, params: dict | None = None) -> requests.Response:
    params = dict(params or {})
    body = _sign(secret, params)
    url = base + path + "?" + body
    return requests.get(url, headers={"X-MBX-APIKEY": key}, timeout=60)


def _step_size(base: str, key: str, secret: str, symbol: str) -> float:
    r = requests.get(f"{base}/fapi/v1/exchangeInfo?symbol={symbol}", timeout=60)
    r.raise_for_status()
    for s in r.json().get("symbols", []):
        if s.get("symbol") != symbol:
            continue
        for f in s.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                return float(f["stepSize"])
    return 0.001


def _floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    import math

    n = math.floor(qty / step + 1e-12)
    return max(0.0, n * step)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="DEMO keys + testnet futures base URL")
    p.add_argument("--symbol", default="BTCUSDT")
    args = p.parse_args()

    if args.demo:
        key = os.environ.get("BINANCE_DEMO_API_KEY", "")
        sec = os.environ.get("BINANCE_DEMO_API_SECRET", "")
        base = BASE_DEMO
    else:
        key = os.environ.get("BINANCE_API_KEY", "")
        sec = os.environ.get("BINANCE_API_SECRET", "")
        base = BASE_LIVE

    if not key or not sec:
        print("Missing API credentials in env.", file=sys.stderr)
        return 1

    sym = args.symbol.upper()
    pr = _get(base, "/fapi/v2/positionRisk", key, sec, {"symbol": sym})
    if pr.status_code != 200:
        print(pr.text, file=sys.stderr)
        return 1
    rows = pr.json()
    if not rows:
        print("No position data.")
        return 0
    amt = float(rows[0].get("positionAmt", 0))
    if abs(amt) < 1e-12:
        print(f"{sym}: already flat.")
        return 0

    step = _step_size(base, key, sec, sym)
    q = _floor_to_step(abs(amt), step)
    if q <= 0:
        print(f"{sym}: position {amt} rounds to 0 qty — close manually on Binance UI.", file=sys.stderr)
        return 1

    side = "SELL" if amt > 0 else "BUY"
    print(f"{sym}: closing {amt} → MARKET {side} qty={q}")

    ordr = _post(
        base,
        "/fapi/v1/order",
        key,
        sec,
        {
            "symbol": sym,
            "side": side,
            "type": "MARKET",
            "quantity": f"{q:.10f}".rstrip("0").rstrip("."),
            "reduceOnly": "true",
        },
    )
    print(ordr.status_code, ordr.text)
    if ordr.status_code != 200:
        return 1
    try:
        print(json.dumps(ordr.json(), indent=2))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
