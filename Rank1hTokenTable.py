#!/usr/bin/env python3
# Table view for Kraken Spot 1h token ranking.

import argparse
import time

import requests

KRAKEN_REST = "https://api.kraken.com"


def http_get_json(path, params=None, timeout=10.0, retries=3):
    last = None
    url = f"{KRAKEN_REST}{path}"
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1 + i)
                continue
            r.raise_for_status()
            data = r.json()
            errors = data.get("error") if isinstance(data, dict) else None
            if errors:
                raise RuntimeError(",".join(errors))
            return data.get("result") if isinstance(data, dict) and "result" in data else data
        except Exception as e:
            last = e
            time.sleep(0.5 + i)
    raise RuntimeError(last)


def asset_display(asset):
    raw = str(asset or "").upper()
    aliases = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ZUSD": "USD", "ZEUR": "EUR"}
    return aliases.get(raw, raw.removeprefix("X").removeprefix("Z") if len(raw) > 3 else raw)


def get_symbols(quote):
    data = http_get_json("/0/public/AssetPairs")
    out = []
    for pair_id, row in (data or {}).items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "online").lower()
        if status not in ("online", ""):
            continue
        base = asset_display(row.get("base", ""))
        row_quote = asset_display(row.get("quote", ""))
        if row_quote == quote:
            out.append((f"{base}{row_quote}", pair_id))
    return out


def get_last_2h_closes(pair_id):
    kl = http_get_json("/0/public/OHLC", params={"pair": pair_id, "interval": 60})
    rows = (kl or {}).get(pair_id) or next((v for k, v in (kl or {}).items() if k != "last"), [])
    now = int(time.time())
    closed = [k for k in rows if int(float(k[0])) + 3600 <= now]
    if len(closed) < 2:
        return None
    prev, last = closed[-2], closed[-1]
    return float(prev[4]), float(last[4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote", default="USDC")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    rows = []
    for sym, pair_id in get_symbols(args.quote.upper().strip()):
        try:
            p1, p2 = get_last_2h_closes(pair_id)
            var = (p2 / p1 - 1) * 100
            rows.append((sym, p1, p2, var))
        except Exception:
            pass
        time.sleep(args.sleep)

    rows.sort(key=lambda x: abs(x[3]), reverse=True)
    rows = rows[: args.top]

    col = 15

    print(f"{'TOKEN':<{col}}{'H-1':<{col}}{'H':<{col}}{'VAR%':<{col}}")
    print("-" * (col * 4))

    for sym, p1, p2, v in rows:
        print(
            f"{sym:<{col}}"
            f"{p1:<{col}.6f}"
            f"{p2:<{col}.6f}"
            f"{v:+<{col}.2f}"
        )


if __name__ == "__main__":
    main()
