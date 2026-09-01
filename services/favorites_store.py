"""Dashboard-only Kraken favorites watchlist and compact local history.

Nothing here is used by the bot runtime, strategies, order execution, or the
Token Radar scanner.  It reads public Kraken market data for the personal
dashboard watchlist and stores only those selected pairs locally.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

try:
    import yaml
except ImportError:  # Keep the dashboard available if YAML is not installed.
    yaml = None

from exchange.kraken import Kraken
from exchange.symbols import SymbolMapper


DEFAULT_SETTINGS = {
    "snapshot_interval_sec": 60,
    "retention_days": 14,
    "request_timeout_sec": 4,
    "history_limit": 3000,
}


def _compact(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _settings(raw: Mapping | None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, Mapping):
        for key in settings:
            try:
                candidate = float(raw.get(key, settings[key]))
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate) and candidate > 0:
                settings[key] = candidate
    settings["snapshot_interval_sec"] = max(10, min(3600, int(settings["snapshot_interval_sec"])))
    settings["retention_days"] = max(1, min(30, int(settings["retention_days"])))
    settings["request_timeout_sec"] = max(1, min(15, int(settings["request_timeout_sec"])))
    settings["history_limit"] = max(100, min(10000, int(settings["history_limit"])))
    return settings


def _entry(raw: object) -> dict | None:
    if isinstance(raw, Mapping):
        symbol = str(raw.get("symbol") or "").strip()
        note = str(raw.get("note") or "").strip()
        quote = str(raw.get("quote_asset") or "").strip().upper()
    else:
        symbol = str(raw or "").strip()
        note = ""
        quote = ""
    if not symbol:
        return None

    normalized = symbol.upper().replace("-", "/").replace("_", "/")
    if "/" in normalized:
        base, quote_from_symbol = (part.strip() for part in normalized.split("/", 1))
        quote = quote_from_symbol or quote
    else:
        base = _compact(normalized)
    base = _compact(base)
    quote = _compact(quote or "USD")
    if not base or not quote:
        return None
    return {
        "symbol": f"{base}{quote}",
        "display_symbol": f"{base}/{quote}",
        "raw_symbol": symbol,
        "base_asset": base,
        "quote_asset": quote,
        "note": note[:500],
    }


def load_dashboard_watchlist(path: str | Path | None) -> tuple[list[dict], dict]:
    """Read the independent dashboard watchlist. Missing/invalid files are safe."""
    data: Mapping | None = None
    if yaml is not None and path:
        try:
            with Path(path).open(encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            data = loaded if isinstance(loaded, Mapping) else None
        except Exception:
            data = None
    settings = _settings(data.get("settings") if isinstance(data, Mapping) else None)
    rows = data.get("favorites", []) if isinstance(data, Mapping) else []
    if not isinstance(rows, list):
        rows = []
    favorites: list[dict] = []
    seen: set[str] = set()
    for raw in rows:
        entry = _entry(raw)
        if entry is None or entry["symbol"] in seen:
            continue
        seen.add(entry["symbol"])
        favorites.append(entry)
    return favorites, settings


def _connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=3000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_favorite_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            display_symbol TEXT NOT NULL,
            base_asset TEXT NOT NULL,
            quote_asset TEXT NOT NULL,
            pair_id TEXT,
            ws_symbol TEXT,
            created_at TEXT NOT NULL,
            created_epoch REAL NOT NULL,
            price REAL,
            bid REAL,
            ask REAL,
            spread_pct REAL,
            quote_volume_24h REAL,
            trade_count_24h INTEGER,
            low_24h REAL,
            high_24h REAL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dashboard_favorite_snapshots_symbol_epoch
        ON dashboard_favorite_snapshots(symbol, created_epoch)
        """
    )
    return connection


def _ticker_value(row: Mapping, key: str, index: int = -1) -> float | None:
    values = row.get(key) if isinstance(row, Mapping) else None
    if isinstance(values, (list, tuple)):
        return _number(values[index] if values else None)
    return _number(values)


def _ticker_row(tickers: Mapping, pair_id: str) -> Mapping | None:
    row = tickers.get(pair_id) if isinstance(tickers, Mapping) else None
    if isinstance(row, Mapping):
        return row
    compact_pair = _compact(pair_id)
    for key, candidate in (tickers or {}).items():
        if _compact(key) == compact_pair and isinstance(candidate, Mapping):
            return candidate
    return None


def _snapshot(entry: Mapping, metadata, ticker: Mapping, now: datetime) -> dict | None:
    price = _ticker_value(ticker, "c", 0)
    bid = _ticker_value(ticker, "b", 0)
    ask = _ticker_value(ticker, "a", 0)
    if price is None or price <= 0:
        return None
    spread = (ask - bid) / bid if bid and bid > 0 and ask is not None else None
    volume = _ticker_value(ticker, "v", -1)
    vwap = _ticker_value(ticker, "p", -1)
    quote_volume = volume * vwap if volume is not None and vwap is not None else None
    trades = _ticker_value(ticker, "t", -1)
    return {
        "symbol": metadata.symbol,
        "display_symbol": entry.get("display_symbol") or metadata.ws_symbol,
        "base_asset": metadata.base_asset,
        "quote_asset": metadata.quote_asset,
        "pair_id": metadata.pair_id,
        "ws_symbol": metadata.ws_symbol,
        "created_at": now.isoformat(),
        "created_epoch": now.timestamp(),
        "price": price,
        "bid": bid,
        "ask": ask,
        "spread_pct": spread,
        "quote_volume_24h": quote_volume,
        "trade_count_24h": int(trades) if trades is not None else None,
        "low_24h": _ticker_value(ticker, "l", -1),
        "high_24h": _ticker_value(ticker, "h", -1),
    }


def store_favorite_snapshots(
    snapshots: Iterable[Mapping],
    *,
    db_path: str | Path,
    settings: Mapping,
) -> int:
    """Persist at most one lightweight snapshot per favorite per interval."""
    rows = [dict(row) for row in snapshots if row.get("symbol")]
    if not rows:
        return 0
    interval = float(settings["snapshot_interval_sec"])
    retention_days = float(settings["retention_days"])
    inserted = 0
    with _connect(db_path) as connection:
        for row in rows:
            latest = connection.execute(
                "SELECT created_epoch FROM dashboard_favorite_snapshots WHERE symbol = ? ORDER BY created_epoch DESC LIMIT 1",
                (row["symbol"],),
            ).fetchone()
            created_epoch = float(row.get("created_epoch") or 0.0)
            if latest is not None and created_epoch - float(latest["created_epoch"] or 0.0) < interval:
                continue
            connection.execute(
                """
                INSERT INTO dashboard_favorite_snapshots(
                    symbol, display_symbol, base_asset, quote_asset, pair_id, ws_symbol,
                    created_at, created_epoch, price, bid, ask, spread_pct,
                    quote_volume_24h, trade_count_24h, low_24h, high_24h
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("symbol"), row.get("display_symbol"), row.get("base_asset"), row.get("quote_asset"),
                    row.get("pair_id"), row.get("ws_symbol"), row.get("created_at"), created_epoch,
                    row.get("price"), row.get("bid"), row.get("ask"), row.get("spread_pct"),
                    row.get("quote_volume_24h"), row.get("trade_count_24h"), row.get("low_24h"), row.get("high_24h"),
                ),
            )
            inserted += 1
        cutoff = _utc_now().timestamp() - retention_days * 86400
        connection.execute("DELETE FROM dashboard_favorite_snapshots WHERE created_epoch < ?", (cutoff,))
    return inserted


def get_favorite_snapshot_history(symbol: str, *, db_path: str | Path, limit: int = 3000) -> list[dict]:
    target = _compact(symbol)
    if not target or not Path(db_path).exists():
        return []
    try:
        with _connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM dashboard_favorite_snapshots
                    WHERE symbol = ?
                    ORDER BY created_epoch DESC, id DESC
                    LIMIT ?
                ) ORDER BY created_epoch ASC, id ASC
                """,
                (target, max(1, min(int(limit), 10000))),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def refresh_dashboard_favorites(
    favorites: Iterable[Mapping],
    *,
    db_path: str | Path,
    settings: Mapping,
    base_url: str = "https://api.kraken.com",
) -> tuple[list[dict], dict[str, dict], list[str]]:
    """Refresh configured pairs with public Kraken endpoints only.

    Failures are returned as per-pair status data so the page can still render
    its locally persisted history.
    """
    entries = [dict(entry) for entry in favorites]
    if not entries:
        return [], {}, []
    client = Kraken(
        baseUrl=base_url,
        httpTimeout=int(settings["request_timeout_sec"]),
        httpRetries=1,
        quoteAsset="USD",
    )
    errors: list[str] = []
    try:
        pairs = client.get("/0/public/AssetPairs")
    except Exception:
        return [dict(entry, data_status="unavailable", data_error="Kraken public API indisponible") for entry in entries], {}, [
            "Kraken public API indisponible"
        ]

    resolved: list[tuple[dict, object]] = []
    records: list[dict] = []
    for entry in entries:
        mapper = SymbolMapper(client, quote_asset=str(entry.get("quote_asset") or "USD"))
        mapper._pairs = pairs if isinstance(pairs, dict) else {}
        try:
            metadata = mapper.resolve_pair(str(entry.get("raw_symbol") or entry.get("display_symbol") or entry.get("symbol")))
        except Exception:
            records.append(dict(entry, data_status="unavailable", data_error="paire spot indisponible"))
            continue
        record = dict(entry)
        record.update(
            {
                "symbol": metadata.symbol,
                "base_asset": metadata.base_asset,
                "quote_asset": metadata.quote_asset,
                "pair_id": metadata.pair_id,
                "ws_symbol": metadata.ws_symbol,
                "data_status": "ready",
                "data_error": "",
            }
        )
        records.append(record)
        resolved.append((record, metadata))

    if not resolved:
        return records, {}, errors
    try:
        ticker_data = client.get("/0/public/Ticker", {"pair": ",".join(meta.pair_id for _entry, meta in resolved)})
    except Exception:
        for record, _metadata in resolved:
            record["data_status"] = "unavailable"
            record["data_error"] = "ticker Kraken indisponible"
        return records, {}, ["ticker Kraken indisponible"]

    now = _utc_now()
    snapshots: dict[str, dict] = {}
    for record, metadata in resolved:
        ticker = _ticker_row(ticker_data, metadata.pair_id)
        snapshot = _snapshot(record, metadata, ticker or {}, now) if ticker else None
        if snapshot is None:
            record["data_status"] = "unavailable"
            record["data_error"] = "donnée ticker absente"
            continue
        snapshots[snapshot["symbol"]] = snapshot
    try:
        store_favorite_snapshots(snapshots.values(), db_path=db_path, settings=settings)
    except Exception:
        errors.append("historique local indisponible")
    return records, snapshots, errors
