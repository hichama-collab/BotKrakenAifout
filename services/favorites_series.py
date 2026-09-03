"""Public price series for the manually managed dashboard watchlist.

The service fetches only the currently selected favorite.  It has no access to
private Kraken endpoints and never participates in the bot runtime.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exchange.kraken import Kraken
from services.favorites_store import get_favorite_snapshot_history


PERIODS = {
    "5m": {"interval": "1m", "limit": 5, "delta": timedelta(minutes=5)},
    "15m": {"interval": "1m", "limit": 15, "delta": timedelta(minutes=15)},
    "1h": {"interval": "5m", "limit": 12, "delta": timedelta(hours=1)},
    "4h": {"interval": "15m", "limit": 16, "delta": timedelta(hours=4)},
    "24h": {"interval": "1h", "limit": 24, "delta": timedelta(hours=24)},
    "7d": {"interval": "4h", "limit": 42, "delta": timedelta(days=7)},
}
CACHE_TTL_SEC = 30.0
_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _normalize_symbol(raw: str) -> str:
    return str(raw or "").upper().replace("/", "").replace("-", "").strip()


def _payload(period: str, source: str, bars: list[dict], reason: str = "") -> dict:
    valid = [bar for bar in bars if _number(bar.get("close")) is not None]
    highs = [_number(bar.get("high")) for bar in valid]
    lows = [_number(bar.get("low")) for bar in valid]
    closes = [_number(bar.get("close")) for bar in valid]
    return {
        "period": period,
        "source": source,
        "bars": valid,
        "min": min(low for low in lows if low is not None) if lows else None,
        "max": max(high for high in highs if high is not None) if highs else None,
        "current": closes[-1] if closes else None,
        "reason": reason,
    }


def _ohlc_bars(symbol: str, period: str, base_url: str) -> list[dict]:
    config = PERIODS[period]
    client = Kraken(baseUrl=base_url, httpTimeout=5, httpRetries=1, quoteAsset="USD")
    rows = client.klines(symbol, interval=config["interval"], limit=config["limit"])
    bars = []
    for row in rows:
        try:
            timestamp_ms, open_, high, low, close, volume = row
            bars.append(
                {
                    "time": int(float(timestamp_ms) / 1000),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume) if _number(volume) is not None else None,
                }
            )
        except (TypeError, ValueError):
            continue
    return bars


def _snapshot_bars(symbol: str, period: str, history_db_path: str | Path, now: datetime) -> list[dict]:
    cutoff = now - PERIODS[period]["delta"]
    rows = get_favorite_snapshot_history(symbol, db_path=history_db_path, limit=3000)
    bars = []
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            continue
        price = _number(row.get("price"))
        if price is None:
            continue
        bars.append({"time": int(created.timestamp()), "open": price, "high": price, "low": price, "close": price, "volume": None})
    return bars


def get_favorite_series(
    symbol: str,
    *,
    period: str,
    history_db_path: str | Path,
    base_url: str = "https://api.kraken.com",
    now: datetime | None = None,
) -> dict:
    """Return one selected pair's public OHLC series, then local fallback."""
    selected_period = period if period in PERIODS else "24h"
    normalized = _normalize_symbol(symbol)
    cache_key = (base_url.rstrip("/"), normalized, selected_period)
    cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] <= CACHE_TTL_SEC:
        return dict(cached[1])
    try:
        ohlc = _ohlc_bars(normalized, selected_period, base_url)
        if ohlc:
            payload = _payload(selected_period, "kraken_ohlc", ohlc)
            _CACHE[cache_key] = (time.monotonic(), payload)
            return dict(payload)
    except Exception:
        pass
    local = _snapshot_bars(normalized, selected_period, history_db_path, (now or datetime.now(timezone.utc)).astimezone(timezone.utc))
    if local:
        return _payload(selected_period, "local_snapshots", local)
    return _payload(selected_period, "unavailable", [], "historique indisponible")


def clear_favorite_series_cache() -> None:
    """Test helper; the dashboard never needs to clear its short-lived cache."""
    _CACHE.clear()
