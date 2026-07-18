#!/usr/bin/env python3
# Rank Kraken Spot symbols by 1h % change using the last two closed 1h candles.
#
# Usage:
#   python3 TokenRank1h.py
#   python3 TokenRank1h.py --quote USDC --top 10 --mode up
#   python3 TokenRank1h.py --mode abs

import argparse
import sys
import time
from typing import List, Tuple

import requests

KRAKEN_REST = "https://api.kraken.com"


def http_get_json(path: str, params: dict | None = None, timeout: float = 10.0, retries: int = 3):
    last_exc = None
    url = f"{KRAKEN_REST}{path}"
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.0 + i)
                continue
            r.raise_for_status()
            data = r.json()
            errors = data.get("error") if isinstance(data, dict) else None
            if errors:
                raise RuntimeError(",".join(errors))
            return data.get("result") if isinstance(data, dict) and "result" in data else data
        except Exception as e:
            last_exc = e
            time.sleep(0.5 + i)
    raise RuntimeError(f"GET failed: {url} params={params} err={last_exc}")


def asset_display(asset: str) -> str:
    raw = str(asset or "").upper()
    aliases = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ZUSD": "USD", "ZEUR": "EUR"}
    return aliases.get(raw, raw.removeprefix("X").removeprefix("Z") if len(raw) > 3 else raw)


def get_spot_symbols(quote: str) -> List[tuple[str, str]]:
    data = http_get_json("/0/public/AssetPairs")
    out: List[tuple[str, str]] = []
    for pair_id, row in (data or {}).items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "online").lower()
        if status not in ("online", ""):
            continue
        base = asset_display(row.get("base", ""))
        row_quote = asset_display(row.get("quote", ""))
        if row_quote != quote:
            continue
        symbol = f"{base}{row_quote}"
        if symbol:
            out.append((symbol, pair_id))
    return out


def get_1h_change_pct(pair_id: str) -> float | None:
    kl = http_get_json(
        "/0/public/OHLC",
        params={"pair": pair_id, "interval": 60},
        timeout=10.0,
        retries=3,
    )
    rows = (kl or {}).get(pair_id) or next((v for k, v in (kl or {}).items() if k != "last"), [])
    if not isinstance(rows, list) or len(rows) < 3:
        return None
    now_sec = int(time.time())
    closed = [k for k in rows if int(float(k[0])) + 3600 <= now_sec]
    if len(closed) < 2:
        return None
    prev = closed[-2]
    last = closed[-1]
    try:
        prev_close = float(prev[4])
        last_close = float(last[4])
        if prev_close <= 0:
            return None
        return (last_close / prev_close - 1.0) * 100.0
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote", default="USDC", help="Quote asset filter (default: USDC)")
    ap.add_argument("--top", type=int, default=10, help="Top N (default: 10)")
    ap.add_argument("--mode", choices=["abs", "up", "down"], default="abs", help="Sort mode: abs/up/down")
    ap.add_argument("--sleep", type=float, default=0.05, help="Sleep between requests (rate limit safety)")
    args = ap.parse_args()

    quote = args.quote.upper().strip()
    symbols = get_spot_symbols(quote)
    if not symbols:
        print(f"No symbols found for quote={quote}", file=sys.stderr)
        return 2

    rows: List[Tuple[str, float]] = []
    for i, (sym, pair_id) in enumerate(symbols, 1):
        try:
            pct = get_1h_change_pct(pair_id)
            if pct is not None:
                rows.append((sym, pct))
        except Exception:
            pass

        if args.sleep > 0:
            time.sleep(args.sleep)

        if i % 200 == 0:
            print(f"scanned {i}/{len(symbols)}", file=sys.stderr)

    if not rows:
        print("No data returned.", file=sys.stderr)
        return 3

    if args.mode == "abs":
        rows.sort(key=lambda x: abs(x[1]), reverse=True)
    elif args.mode == "up":
        rows.sort(key=lambda x: x[1], reverse=True)
    else:
        rows.sort(key=lambda x: x[1])

    topn = rows[: max(1, args.top)]
    print(f"Top {len(topn)} movers (1h) quote={quote} mode={args.mode}")
    for sym, pct in topn:
        print(f"{sym}\t{pct:+.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
