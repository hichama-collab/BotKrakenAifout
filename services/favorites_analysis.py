"""Read-only personal analysis for radar favorites.

The module deliberately consumes only existing radar snapshots and closed bot
trades. It does not query Kraken, write trading state, or produce execution
signals.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

try:
    import yaml
except ImportError:  # Dashboard remains available if an incomplete venv is used.
    yaml = None

from services.token_radar_store import (
    get_token_snapshot_history,
    list_favorites,
    resolve_db_path,
)


RANGE_WINDOWS = (
    ("5m", timedelta(minutes=5)),
    ("15m", timedelta(minutes=15)),
    ("1h", timedelta(hours=1)),
    ("4h", timedelta(hours=4)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
)

DEFAULT_SETTINGS = {
    "minimum_samples_per_range": 2,
    "minimum_range_coverage_pct": 0.8,
    "history_limit_per_favorite": 3000,
    "freshness_sec": 1800,
    "near_high_pct": 0.006,
    "return_from_low_pct": 0.02,
    "acceleration_5m_pct": 0.004,
    "calm_change_pct": 0.001,
    "tight_range_pct": 0.006,
    "high_volatility_pct": 0.06,
    "noisy_risk_score": 55.0,
    "scalping_clean_max_spread_pct": 0.001,
    "scalping_acceptable_max_spread_pct": 0.0025,
    "scalping_low_quote_volume_24h": 250000.0,
    "scalping_good_quote_volume_24h": 1000000.0,
    "scalping_clean_reliability_score": 70.0,
    "history_min_trades_for_signal": 3,
}


def _number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _pct(value) -> float | None:
    parsed = _number(value)
    return round(parsed, 8) if parsed is not None else None


def _symbol(value) -> str:
    return str(value or "").strip().upper().replace("/", "").replace("-", "")


def _parse_ts(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_settings(raw: Mapping | None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for key in settings:
        if isinstance(raw, Mapping) and key in raw:
            candidate = _number(raw.get(key))
            if candidate is not None and candidate >= 0:
                settings[key] = int(candidate) if key.endswith("_limit_per_favorite") or key.startswith("minimum_") or key.startswith("history_min_") else candidate
    settings["minimum_samples_per_range"] = max(1, int(settings["minimum_samples_per_range"]))
    settings["minimum_range_coverage_pct"] = max(0.0, min(1.0, float(settings["minimum_range_coverage_pct"])))
    settings["history_limit_per_favorite"] = max(100, min(10000, int(settings["history_limit_per_favorite"])))
    settings["history_min_trades_for_signal"] = max(1, int(settings["history_min_trades_for_signal"]))
    return settings


def load_favorites_settings(risk_yaml_path: str | Path | None = None) -> dict:
    """Load dashboard-only thresholds without changing bot configuration use."""
    raw: Mapping | None = None
    if yaml is not None and risk_yaml_path:
        try:
            with Path(risk_yaml_path).open(encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            dashboard = data.get("dashboard") if isinstance(data, Mapping) else {}
            raw = dashboard.get("favorites") if isinstance(dashboard, Mapping) else {}
        except Exception:
            raw = None
    return _safe_settings(raw)


def _freshness(latest_at: datetime | None, now: datetime, settings: Mapping) -> dict:
    if latest_at is None:
        return {"status": "no_data", "age_sec": None, "is_fresh": False}
    age_sec = max(0.0, (now - latest_at).total_seconds())
    return {
        "status": "fresh" if age_sec <= float(settings["freshness_sec"]) else "stale",
        "age_sec": round(age_sec, 1),
        "is_fresh": age_sec <= float(settings["freshness_sec"]),
    }


def _range_payload(samples: list[dict], current_price: float | None, label: str, source: str = "radar_snapshots") -> dict:
    prices = [price for price in (_number(item.get("price")) for item in samples) if price is not None and price > 0]
    if current_price is None or current_price <= 0 or not prices:
        return {"period": label, "available": False, "samples": len(samples), "source": source, "reason": "no_data"}
    low = min(prices)
    high = max(prices)
    distance_low = (current_price - low) / low if low > 0 else None
    distance_high = (current_price - high) / high if high > 0 else None
    width = (high - low) / low if low > 0 else None
    position = 50.0 if high == low else (current_price - low) / (high - low) * 100.0
    return {
        "period": label,
        "available": True,
        "samples": len(samples),
        "source": source,
        "min": round(low, 12),
        "max": round(high, 12),
        "current": round(current_price, 12),
        "distance_from_min_pct": _pct(distance_low),
        "distance_to_max_pct": _pct(distance_high),
        "position_in_range_pct": round(max(0.0, min(100.0, position)), 2),
        "range_pct": _pct(width),
    }


def _ranges(history: list[dict], latest: Mapping | None, now: datetime, settings: Mapping) -> dict:
    latest = latest or {}
    current = _number(latest.get("price"))
    ranges = {}
    dated = [(item, _parse_ts(item.get("created_at"))) for item in history]
    min_samples = int(settings["minimum_samples_per_range"])
    for label, delta in RANGE_WINDOWS:
        cutoff = now - delta
        samples = [item for item, created_at in dated if created_at is not None and created_at >= cutoff]
        sample_times = [created_at for _item, created_at in dated if created_at is not None and created_at >= cutoff]
        coverage_sec = (max(sample_times) - min(sample_times)).total_seconds() if len(sample_times) >= 2 else 0.0
        required_coverage_sec = delta.total_seconds() * float(settings["minimum_range_coverage_pct"])
        if len(samples) >= min_samples and coverage_sec >= required_coverage_sec:
            ranges[label] = _range_payload(samples, current, label)
            continue
        # Kraken's latest ticker snapshot has a real 24h low/high even when
        # the local radar history is still too young.
        if label == "24h":
            low = _number(latest.get("low_24h"))
            high = _number(latest.get("high_24h"))
            if current and low and high and low > 0 and high >= low:
                ranges[label] = _range_payload(
                    [{"price": low}, {"price": high}], current, label, source="kraken_24h_ticker"
                )
                ranges[label]["samples"] = len(samples)
                continue
        ranges[label] = {
            "period": label,
            "available": False,
            "samples": len(samples),
            "source": "radar_snapshots",
            "reason": "insufficient_coverage" if len(samples) >= min_samples else "insufficient_samples",
        }
    return ranges


def _behavior(latest: Mapping | None, ranges: Mapping, settings: Mapping) -> dict:
    if not latest:
        return {"summary": "données insuffisantes", "labels": ["données insuffisantes"]}

    c5 = _number(latest.get("change_5m_pct"))
    c15 = _number(latest.get("change_15m_pct"))
    c1h = _number(latest.get("change_1h_pct"))
    c4h = _number(latest.get("change_4h_pct"))
    c24 = _number(latest.get("change_24h_pct"))
    distance_high = _number(latest.get("distance_high_24h_pct"))
    distance_low = _number(latest.get("distance_low_24h_pct"))
    volatility = _number(latest.get("volatility_pct"))
    if volatility is None:
        volatility = _number(latest.get("amplitude_pct"))
    noise = _number(latest.get("movement_risk_score", latest.get("noise_score")))
    reliability = _number(latest.get("reliability_score", latest.get("consistency_score")))
    labels: list[str] = []

    if volatility is not None and volatility >= float(settings["high_volatility_pct"]):
        labels.append("volatilité excessive")
    if noise is not None and noise >= float(settings["noisy_risk_score"]):
        labels.append("mouvement bruité")
    if distance_high is not None and distance_high >= -float(settings["near_high_pct"]):
        labels.append("proche du plus haut récent")
        if c24 is not None and c24 >= float(settings["high_volatility_pct"]):
            labels.append("pompe déjà passée")
    if distance_low is not None and distance_low <= float(settings["return_from_low_pct"]):
        labels.append("revient depuis un point bas")
    if c5 is not None and c15 is not None and c1h is not None and c5 >= float(settings["acceleration_5m_pct"]) and c15 > 0 and c1h > 0:
        labels.append("accélération")
    elif c5 is not None and c15 is not None and c5 < 0 and c15 > 0:
        labels.append("essoufflement")
    if all(value is not None and abs(value) <= float(settings["calm_change_pct"]) for value in (c5, c15, c1h)):
        labels.append("calme")
    one_hour = ranges.get("1h") if isinstance(ranges, Mapping) else None
    if isinstance(one_hour, Mapping) and one_hour.get("available") and _number(one_hour.get("range_pct")) is not None:
        if float(one_hour["range_pct"]) <= float(settings["tight_range_pct"]):
            labels.append("range serré")
    if reliability is not None and reliability >= float(settings["scalping_clean_reliability_score"]) and not labels:
        labels.append("mouvement propre")
    if not labels:
        labels.append("à observer")
    return {"summary": labels[0], "labels": labels[:4]}


def _scalping_quality(latest: Mapping | None, settings: Mapping) -> dict:
    if not latest:
        return {"label": "données insuffisantes", "reason": "no data", "grade": "unknown"}
    spread = _number(latest.get("spread_pct"))
    volume = _number(latest.get("quote_volume_24h"))
    volatility = _number(latest.get("volatility_pct"))
    if volatility is None:
        volatility = _number(latest.get("amplitude_pct"))
    risk = _number(latest.get("movement_risk_score", latest.get("noise_score")))
    reliability = _number(latest.get("reliability_score", latest.get("consistency_score")))
    if spread is None or volume is None:
        return {"label": "données insuffisantes", "reason": "spread ou volume absent", "grade": "unknown"}
    if volume < float(settings["scalping_low_quote_volume_24h"]):
        return {"label": "sale", "reason": "volume faible", "grade": "dirty"}
    if spread > float(settings["scalping_acceptable_max_spread_pct"]):
        return {"label": "sale", "reason": "spread instable", "grade": "dirty"}
    if risk is not None and risk >= float(settings["noisy_risk_score"]):
        return {"label": "bruité", "reason": "mouvement instable", "grade": "noisy"}
    if volatility is not None and volatility >= float(settings["high_volatility_pct"]):
        return {"label": "bruité", "reason": "amplitude élevée", "grade": "noisy"}
    if (
        spread <= float(settings["scalping_clean_max_spread_pct"])
        and volume >= float(settings["scalping_good_quote_volume_24h"])
        and reliability is not None
        and reliability >= float(settings["scalping_clean_reliability_score"])
    ):
        return {"label": "propre", "reason": "spread, volume et régularité corrects", "grade": "clean"}
    return {"label": "acceptable", "reason": "conditions partielles", "grade": "acceptable"}


def _personal_history(symbol: str, trades: Iterable[Mapping], settings: Mapping) -> dict:
    rows = [row for row in trades or [] if _symbol(row.get("symbol")) == symbol]
    if not rows:
        return {
            "available": False,
            "assessment": "no bot trade history",
            "complete_trades": 0,
            "net_pnl_usdc": None,
            "best_trade_usdc": None,
            "worst_trade_usdc": None,
            "last_activity": None,
            "frequent_exits": [],
        }
    pnl_values = [_number(row.get("pnl_usdc", row.get("pnl"))) for row in rows]
    pnl_values = [value for value in pnl_values if value is not None]
    exits = Counter(str(row.get("reason") or "non renseignée").strip() or "non renseignée" for row in rows)
    rows.sort(key=lambda row: (_number(row.get("ts_epoch")) or 0.0, str(row.get("ts_utc") or "")))
    trade_count = len(rows)
    net_pnl = sum(pnl_values)
    if trade_count < int(settings["history_min_trades_for_signal"]):
        assessment = "échantillon limité"
    elif net_pnl > 0:
        assessment = "historique positif"
    elif net_pnl < 0:
        assessment = "historique négatif"
    else:
        assessment = "historique neutre"
    return {
        "available": True,
        "assessment": assessment,
        "complete_trades": trade_count,
        "net_pnl_usdc": round(net_pnl, 8),
        "best_trade_usdc": round(max(pnl_values), 8) if pnl_values else None,
        "worst_trade_usdc": round(min(pnl_values), 8) if pnl_values else None,
        "last_activity": rows[-1].get("ts_utc") or rows[-1].get("ts_epoch"),
        "frequent_exits": [{"reason": reason, "count": count} for reason, count in exits.most_common(3)],
    }


def _verdict(latest: Mapping | None, freshness: Mapping, behavior: Mapping, scalping: Mapping, ranges: Mapping, settings: Mapping) -> str:
    if not latest or freshness.get("status") == "no_data":
        return "données insuffisantes"
    if freshness.get("status") == "stale":
        return "données anciennes"
    volume = _number(latest.get("quote_volume_24h"))
    if volume is not None and volume < float(settings["scalping_low_quote_volume_24h"]):
        return "volume faible"
    if scalping.get("label") in {"sale", "bruité"}:
        return "trop volatile" if "volatil" in str(behavior.get("summary", "")) else "spread sale"
    day = ranges.get("24h") if isinstance(ranges, Mapping) else None
    if isinstance(day, Mapping) and day.get("available") and _number(day.get("distance_to_max_pct")) is not None:
        if float(day["distance_to_max_pct"]) >= -float(settings["near_high_pct"]):
            return "trop haut"
    if "calme" in behavior.get("labels", []):
        return "calme"
    if scalping.get("label") == "propre" and behavior.get("summary") in {"accélération", "mouvement propre", "revient depuis un point bas"}:
        return "intéressant"
    return "à surveiller"


def _risk_rank(item: Mapping) -> float:
    risk = _number(item.get("movement_risk_score", item.get("noise_score")))
    if risk is not None:
        return risk
    label = str(item.get("risk_level") or "").upper()
    return {"EXTREME": 100.0, "HIGH": 75.0, "MEDIUM": 45.0, "LOW": 15.0}.get(label, 0.0)


def _build_item(favorite: Mapping, history: list[dict], trades: Iterable[Mapping], now: datetime, settings: Mapping, include_series: bool = False) -> dict:
    latest = history[-1] if history else None
    latest_at = _parse_ts(latest.get("created_at")) if latest else None
    ranges = _ranges(history, latest, now, settings)
    freshness = _freshness(latest_at, now, settings)
    behavior = _behavior(latest, ranges, settings)
    scalping = _scalping_quality(latest, settings)
    symbol = _symbol(favorite.get("symbol"))
    item = {
        "symbol": symbol,
        "note": str(favorite.get("note") or ""),
        "current_price": _number(latest.get("price")) if latest else None,
        "updated_at": latest.get("created_at") if latest else None,
        "freshness": freshness,
        "changes": {
            key: _pct(latest.get(key)) if latest else None
            for key in ("change_5m_pct", "change_15m_pct", "change_1h_pct", "change_4h_pct", "change_24h_pct", "change_7d_pct")
        },
        "market": {
            "spread_pct": _pct(latest.get("spread_pct")) if latest else None,
            "quote_volume_24h": _number(latest.get("quote_volume_24h")) if latest else None,
            "volatility_pct": _pct(
                (latest or {}).get("volatility_pct")
                if (latest or {}).get("volatility_pct") is not None
                else (latest or {}).get("amplitude_pct")
            ),
            "risk_level": (latest or {}).get("risk_level"),
            "risk_label": (latest or {}).get("risk_label"),
            "risk_reason": (latest or {}).get("risk_reason"),
            "reliability_score": _number((latest or {}).get("reliability_score", (latest or {}).get("consistency_score"))),
            "movement_risk_score": _number((latest or {}).get("movement_risk_score", (latest or {}).get("noise_score"))),
        },
        "ranges": ranges,
        "behavior": behavior,
        "scalping_quality": scalping,
        "bot_history": _personal_history(symbol, trades, settings),
    }
    item["verdict"] = _verdict(latest, freshness, behavior, scalping, ranges, settings)
    if include_series:
        item["series"] = [
            {"created_at": point.get("created_at"), "price": _number(point.get("price"))}
            for point in history[-80:]
            if _number(point.get("price")) is not None
        ]
    return item


def _summary(items: list[dict]) -> dict:
    valid = [item for item in items if item.get("current_price") is not None]
    def _watch_score(item: Mapping) -> tuple:
        scalping = item.get("scalping_quality") or {}
        grade = {"clean": 3, "acceptable": 2, "noisy": 1, "dirty": 0, "unknown": -1}.get(scalping.get("grade"), -1)
        momentum = _number((item.get("changes") or {}).get("change_1h_pct")) or 0.0
        reliability = _number((item.get("market") or {}).get("reliability_score")) or 0.0
        return grade, momentum, reliability, item.get("symbol", "")

    def _active(item: Mapping) -> float:
        return abs(_number((item.get("changes") or {}).get("change_5m_pct")) or 0.0)

    def _volatility(item: Mapping) -> float:
        return _number((item.get("market") or {}).get("volatility_pct")) or -1.0

    active_items = [
        item for item in valid
        if _number((item.get("changes") or {}).get("change_5m_pct")) is not None
    ]
    volatility_items = [
        item for item in valid
        if _number((item.get("market") or {}).get("volatility_pct")) is not None
    ]
    watchable_items = [
        item for item in valid
        if item.get("verdict") in {"intéressant", "à surveiller", "calme"}
        and (item.get("freshness") or {}).get("status") == "fresh"
    ]
    latest_dates = [_parse_ts(item.get("updated_at")) for item in valid]
    latest_dates = [value for value in latest_dates if value is not None]
    summary = {
        "followed_count": len(items),
        "best_to_watch": max(watchable_items, key=_watch_score, default=None),
        "most_active": max(active_items, key=_active, default=None),
        "calmest": min(volatility_items, key=_volatility, default=None),
        "most_volatile": max(volatility_items, key=_volatility, default=None),
        "most_risky": max(valid, key=lambda item: _risk_rank(item.get("market") or {}), default=None),
        "latest_data_at": max(latest_dates).isoformat() if latest_dates else None,
    }
    return summary


def build_favorites_analysis(
    *,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
    risk_yaml_path: str | Path | None = None,
    trades: Iterable[Mapping] = (),
    now: datetime | None = None,
) -> dict:
    """Build the global read-only favorites view from existing local data."""
    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    settings = load_favorites_settings(risk_yaml_path)
    path = resolve_db_path(db_path=db_path, base_dir=base_dir)
    trade_rows = list(trades or [])
    try:
        favorites = list_favorites(db_path=path)
    except Exception:
        favorites = []
    items = []
    for favorite in favorites:
        try:
            history = get_token_snapshot_history(
                favorite.get("symbol", ""), limit=settings["history_limit_per_favorite"], db_path=path
            )
            items.append(_build_item(favorite, history, trade_rows, current_now, settings))
        except Exception:
            items.append(_build_item(favorite, [], trade_rows, current_now, settings))
    items.sort(key=lambda item: item.get("symbol") or "")
    summary = _summary(items)
    summary["freshness"] = _freshness(_parse_ts(summary.get("latest_data_at")), current_now, settings)
    return {
        "generated_at": current_now.isoformat(),
        "data_source": "token_radar.sqlite3",
        "summary": summary,
        "items": items,
    }


def build_favorite_detail(
    symbol: str,
    *,
    db_path: str | Path | None = None,
    base_dir: str | Path | None = None,
    risk_yaml_path: str | Path | None = None,
    trades: Iterable[Mapping] = (),
    now: datetime | None = None,
) -> dict | None:
    """Return one active favorite with its compact local price series."""
    target = _symbol(symbol)
    if not target:
        return None
    settings = load_favorites_settings(risk_yaml_path)
    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = resolve_db_path(db_path=db_path, base_dir=base_dir)
    try:
        favorite = next(item for item in list_favorites(db_path=path) if _symbol(item.get("symbol")) == target)
        history = get_token_snapshot_history(target, limit=settings["history_limit_per_favorite"], db_path=path)
    except Exception:
        return None
    return _build_item(favorite, history, list(trades or []), current_now, settings, include_series=True)
