#!/usr/bin/env python3
"""Read-only Kraken API healthcheck. It never calls AddOrder."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import applyRiskConfig, loadConfig
from core.entry_diagnostics import quote_sizing_snapshot
from exchange.kraken import Kraken


def _quote_free(balances: dict, quote_asset: str) -> float:
    for balance in balances.get("balances", []) if isinstance(balances, dict) else []:
        if str(balance.get("asset", "")).upper() == quote_asset:
            try:
                return float(balance.get("free", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _minimum_notional(client: Kraken, cfg, symbol: str) -> tuple[float, float]:
    """Return configured/exchange minimum without submitting an order."""
    configured = float(getattr(cfg, "minOrderNotionalUsdc", 0.0) or 0.0)
    exchange_minimum = 0.0
    if symbol:
        try:
            exchange_minimum = float(client.resolve_pair(symbol).min_notional or 0.0)
        except Exception:
            pass
    return max(configured, exchange_minimum), exchange_minimum


def run_healthcheck(client: Kraken, cfg, txid: str = "", now=time.time) -> dict:
    results: dict[str, Any] = {"lines": [], "ok": True}

    try:
        server_time = client.get("/0/public/Time") or {}
        server_epoch = float(server_time.get("unixtime", 0) or 0)
        drift = abs(float(now()) - server_epoch) if server_epoch > 0 else None
        results["lines"].append("API_OK")
        results["lines"].append(f"CLOCK_DRIFT_OK drift_sec={drift:.3f}" if drift is not None else "CLOCK_DRIFT_UNKNOWN")
    except Exception as exc:
        results["ok"] = False
        results["lines"].append(f"API_ERROR endpoint=Time type={type(exc).__name__}")

    try:
        client.get("/0/public/AssetPairs")
        results["lines"].append("ASSET_PAIRS_OK")
    except Exception as exc:
        results["ok"] = False
        results["lines"].append(f"ASSET_PAIRS_ERROR type={type(exc).__name__}")

    balances: dict = {}
    try:
        balances = client.account_balances()
        results["lines"].append("BALANCE_OK")
        results["lines"].append("NONCE_OK")
    except Exception as exc:
        results["ok"] = False
        results["lines"].append(f"BALANCE_ERROR type={type(exc).__name__}")

    try:
        client.post("/0/private/OpenOrders", {})
        results["lines"].append("OPEN_ORDERS_OK")
    except Exception as exc:
        results["ok"] = False
        results["lines"].append(f"OPEN_ORDERS_ERROR type={type(exc).__name__}")

    try:
        client.post("/0/private/TradesHistory", {"trades": False})
        results["lines"].append("TRADES_HISTORY_OK")
    except Exception as exc:
        results["ok"] = False
        results["lines"].append(f"TRADES_HISTORY_ERROR type={type(exc).__name__}")

    try:
        key_info = client.post("/0/private/GetApiKeyInfo", {}) or {}
        permissions = {str(item).strip().lower() for item in key_info.get("permissions", [])}
        results["lines"].append("API_KEY_INFO_OK")
        results["lines"].append(
            "TRADING_PERMISSION_GRANTED"
            if "modify-trades" in permissions
            else "TRADING_PERMISSION_NOT_GRANTED"
        )
    except Exception as exc:
        results["lines"].append(f"TRADING_PERMISSION_UNKNOWN type={type(exc).__name__}")

    if txid:
        try:
            client.post("/0/private/QueryOrders", {"txid": txid})
            results["lines"].append("QUERY_ORDERS_OK")
        except Exception as exc:
            results["ok"] = False
            results["lines"].append(f"QUERY_ORDERS_ERROR type={type(exc).__name__}")

    quote_asset = str(getattr(cfg, "quoteAsset", "USDC") or "USDC").upper()
    quote_free = _quote_free(balances, quote_asset)
    symbol = str(os.getenv("SYMBOL", "") or "").strip().upper()
    min_notional, exchange_minimum = _minimum_notional(client, cfg, symbol)
    sizing = quote_sizing_snapshot(
        quote_free=quote_free,
        quote_asset=quote_asset,
        min_notional=min_notional,
        cap=float(getattr(cfg, "maxUsdcPerTrade", 0.0) or 0.0),
        fee_buffer_pct=float(getattr(cfg, "feeBufPct", 0.0) or 0.0),
        dry_run=False,
        has_position=False,
    )
    results["lines"].append(
        "QUOTE_BALANCE "
        f"quote_asset={quote_asset} quote_free={quote_free:.8f} "
        f"symbol={symbol or '-'} min_notional={min_notional:.8f} "
        f"exchange_min_notional={exchange_minimum:.8f} "
        f"sizing_cap={sizing['sizing_cap']:.8f}"
    )
    results["lines"].append("QUOTE_BALANCE_OK" if sizing["can_buy"] else "NO_QUOTE_BALANCE")
    results["lines"].append("NO_LIVE_ORDER_SUBMITTED")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--txid", default="", help="Optional known Kraken transaction id for QueryOrders")
    args = parser.parse_args()

    cfg = applyRiskConfig(loadConfig())
    client = Kraken(
        cfg.apiKey,
        cfg.apiSecret,
        cfg.baseUrl,
        cfg.httpTimeout,
        cfg.httpRetries,
        cfg.httpBackoff,
        cfg.quoteAsset,
    )
    result = run_healthcheck(client, cfg, txid=args.txid)
    for line in result["lines"]:
        print(line)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
