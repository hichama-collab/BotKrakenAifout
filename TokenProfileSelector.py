#!/usr/bin/env python3
import os
import re
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml

from core.trade_memory import load_token_scores, sync_trade_memory
from state.token_quality import load_quality_map, save_quality_map

BASE_URL = os.getenv("KRAKEN_BASE_URL", "https://api.kraken.com").rstrip("/")
ROOT_PATH = Path((os.getenv("BOT_ROOT_DIR") or str(Path(__file__).resolve().parent)).strip()).resolve()
SERVICE_ENV_PATH = str(Path(os.getenv("BOT_SERVICE_ENV_PATH") or (ROOT_PATH / ".service.env")).resolve())
BLOCKED_SYMBOLS_PATH = Path(
    (os.getenv("BOT_BLOCKED_SYMBOLS_PATH") or str(ROOT_PATH / "data" / "blocked_symbols.txt")).strip()
).resolve()
SELECTOR_STATE_PATH = ROOT_PATH / "data" / "runtime" / "selector_state.json"
RUNTIME_POSITION_PATH = ROOT_PATH / "data" / "runtime" / "position.json"
RUNTIME_PORTFOLIO_PATH = ROOT_PATH / "data" / "runtime" / "portfolio.json"
def _load_selector_settings() -> dict:
    try:
        with (ROOT_PATH / "config" / "risk.yaml").open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        settings = data.get("tokenProfileSelector") or {}
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


_SELECTOR_SETTINGS = _load_selector_settings()


def _selector_setting(name: str, env_name: str, default, cast):
    raw = os.getenv(env_name)
    if raw not in (None, ""):
        return cast(raw)
    return cast(_SELECTOR_SETTINGS.get(name, default))


# Selector thresholds live in config/risk.yaml. Environment overrides remain
# available for service-level emergency overrides without changing trading logic.
SELECTOR_MIN_HOLD_MINUTES = _selector_setting("min_hold_minutes", "SELECTOR_MIN_HOLD_MINUTES", 10, float)
SELECTOR_FLAT_MIN_HOLD_MINUTES = _selector_setting("flat_min_hold_minutes", "SELECTOR_FLAT_MIN_HOLD_MINUTES", 5, float)
SELECTOR_HYSTERESIS_PCT = _selector_setting("hysteresis_pct", "SELECTOR_HYSTERESIS_PCT", 0.25, float)
SELECTOR_MIN_ACTIVE_VAR_PCT = _selector_setting("min_active_var_pct", "SELECTOR_MIN_ACTIVE_VAR_PCT", 0.15, float)
SELECTOR_MIN_DIRECTION_PCT = _selector_setting("min_direction_pct", "SELECTOR_MIN_DIRECTION_PCT", -0.15, float)
WINDOW_MINUTES = max(2, _selector_setting("window_minutes", "SELECTOR_WINDOW_MINUTES", 2, int))
HTTP_TIMEOUT = 5
MAX_WORKERS = 16
SELECTOR_MAX_SPREAD_PCT = _selector_setting("max_spread_pct", "SELECTOR_MAX_SPREAD_PCT", 0.0025, float)
SELECTOR_MIN_PRICE_USDC = _selector_setting("min_price_usdc", "SELECTOR_MIN_PRICE_USDC", 0.05, float)
SELECTOR_MIN_QUOTE_VOLUME_USDC_24H = _selector_setting("min_quote_volume_usdc_24h", "SELECTOR_MIN_QUOTE_VOLUME_USDC_24H", 250000, float)
SELECTOR_MIN_TRADE_COUNT_24H = _selector_setting("min_trade_count_24h", "SELECTOR_MIN_TRADE_COUNT_24H", 1000, int)
SELECTOR_MIN_WINDOW_PCT = _selector_setting("min_window_pct", "SELECTOR_MIN_WINDOW_PCT", 0.15, float)
SELECTOR_MAX_WINDOW_PCT = _selector_setting("max_window_pct", "SELECTOR_MAX_WINDOW_PCT", 1.80, float)
SELECTOR_MAX_WINDOW_PCT_UNKNOWN = _selector_setting("max_window_pct_unknown", "SELECTOR_MAX_WINDOW_PCT_UNKNOWN", 1.20, float)
SELECTOR_MIN_MOVE_TO_SPREAD = _selector_setting("min_move_to_spread", "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0, float)
SELECTOR_MIN_24H_CHANGE_PCT = _selector_setting("min_24h_change_pct", "SELECTOR_MIN_24H_CHANGE_PCT", -8.0, float)
SELECTOR_MAX_24H_CHANGE_PCT = _selector_setting("max_24h_change_pct", "SELECTOR_MAX_24H_CHANGE_PCT", 12.0, float)
SELECTOR_UNKNOWN_SCORE_PENALTY = _selector_setting("unknown_score_penalty", "SELECTOR_UNKNOWN_SCORE_PENALTY", 0.05, float)
SELECTOR_RESPECT_WALLET_POSITION = os.getenv("SELECTOR_RESPECT_WALLET_POSITION", "1") != "0"
SELECTOR_RUNTIME_LOCK_MAX_AGE_SEC = _selector_setting("runtime_lock_max_age_sec", "SELECTOR_RUNTIME_LOCK_MAX_AGE_SEC", 300, float)
SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT = _selector_setting("max_distance_from_5m_high_pct", "SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT", 0.0020, float)
SELECTOR_FALLBACK_ON_RECENT_HIGH = _selector_setting("fallback_on_recent_high", "SELECTOR_FALLBACK_ON_RECENT_HIGH", True, lambda value: str(value).lower() not in {"0", "false", "no"})
# A raw top-mover fallback previously bypassed the tradability gates below and
# restarted the bot every two minutes on negligible moves. Keep it opt-in only.
SELECTOR_TOP_MOVER_FALLBACK = os.getenv("SELECTOR_TOP_MOVER_FALLBACK", "0") != "0"
SELECTOR_RESTART_BOT_ON_CHANGE = os.getenv("SELECTOR_RESTART_BOT_ON_CHANGE", "1") != "0"
BOT_SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "kraken-aifout-bot.service")
DEFAULT_PROFILE = (os.getenv("SELECTOR_PROFILE", "strict") or "strict").strip()


def _load_selector_state() -> dict:
    try:
        if SELECTOR_STATE_PATH.exists():
            return json.loads(SELECTOR_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_selector_state(state: dict) -> None:
    try:
        SELECTOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SELECTOR_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _record_selector_decision(reason: str, **fields) -> None:
    """Persist the last selector verdict for dashboard and offline audit."""
    state = _load_selector_state()
    state.pop("current", None)
    state.update({
        "updated_at": time.time(),
        "last_reason": str(reason),
        **{key: value for key, value in fields.items() if value is not None},
    })
    _save_selector_state(state)


def _minimum_hold_remaining_minutes(hold_age_min: float) -> float:
    return max(0.0, SELECTOR_FLAT_MIN_HOLD_MINUTES - max(0.0, hold_age_min))


def _candidate_window_is_eligible(pct: float, spread_pct: float) -> bool:
    move_pct = abs(float(pct))
    if move_pct < SELECTOR_MIN_WINDOW_PCT or move_pct > SELECTOR_MAX_WINDOW_PCT:
        return False
    required_vs_spread = max(0.0, float(spread_pct)) * max(0.0, SELECTOR_MIN_MOVE_TO_SPREAD)
    return move_pct >= required_vs_spread

# Universe
QUOTE_ASSET = os.getenv("QUOTE_ASSET", "USDC").upper()
EXCLUDED = {"USDCUSDT", "USDTUSDC"}
EXCLUDED_BASE_ASSETS = {
    "USDC", "USDT", "FDUSD", "TUSD", "USDP", "USDS", "USDE", "DAI", "PYUSD",
    "EUR", "EURC", "AEUR", "USD1",
}

_SESSION = requests.Session()
_PAIR_META: dict[str, dict] = {}


def _asset_display(asset: str) -> str:
    raw = str(asset or "").upper()
    aliases = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ZUSD": "USD", "ZEUR": "EUR"}
    return aliases.get(raw, raw.removeprefix("X").removeprefix("Z") if len(raw) > 3 else raw)


def _kraken_result(response):
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(",".join(payload.get("error") or []))
    return payload.get("result") if isinstance(payload, dict) and "result" in payload else payload


def load_blocked_symbols(path: Path) -> set[str]:
    try:
        if not path.exists():
            return set()
        blocked = set()
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            symbol = raw.strip().upper()
            if symbol:
                blocked.add(symbol)
        return blocked
    except Exception:
        return set()

def _read_env_file(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _runtime_wallet_lock() -> dict | None:
    if not SELECTOR_RESPECT_WALLET_POSITION:
        return None

    pos = _read_json(RUNTIME_POSITION_PATH)
    try:
        qty = float(pos.get("qty", 0.0) or 0.0)
    except Exception:
        qty = 0.0
    symbol = str(pos.get("symbol") or "").upper()
    if symbol and qty > 0:
        return {
            "source": "position",
            "symbol": symbol,
            "qty": qty,
            "reason": str(pos.get("reason") or ""),
            "age_sec": max(0.0, time.time() - float(pos.get("ts", 0.0) or 0.0)),
        }

    portfolio = _read_json(RUNTIME_PORTFOLIO_PATH)
    try:
        age_sec = max(0.0, time.time() - float(portfolio.get("ts", 0.0) or 0.0))
    except Exception:
        age_sec = SELECTOR_RUNTIME_LOCK_MAX_AGE_SEC + 1
    if age_sec > SELECTOR_RUNTIME_LOCK_MAX_AGE_SEC:
        return None
    holdings = portfolio.get("holdings") or []
    if not isinstance(holdings, list) or not holdings:
        return None
    top = holdings[0] if isinstance(holdings[0], dict) else {}
    symbol = str(top.get("symbol") or "").upper()
    try:
        qty = float(top.get("qty", 0.0) or 0.0)
        notional = float(top.get("notional", 0.0) or 0.0)
    except Exception:
        qty = 0.0
        notional = 0.0
    if symbol and qty > 0 and notional > 0:
        return {
            "source": "portfolio",
            "symbol": symbol,
            "qty": qty,
            "notional": notional,
            "age_sec": age_sec,
        }
    return None

def _is_symbol_safe(sym: str) -> bool:
    # Exclude any non-ASCII symbols and empty values.
    return bool(sym) and sym.isascii()


def _base_asset(symbol: str) -> str:
    if symbol.endswith(QUOTE_ASSET):
        return symbol[:-len(QUOTE_ASSET)]
    return symbol

def get_symbols_usdc_trading():
    global _PAIR_META
    r = _SESSION.get(f"{BASE_URL}/0/public/AssetPairs", timeout=HTTP_TIMEOUT)
    data = _kraken_result(r)

    out = []
    _PAIR_META = {}
    for pair_id, s in (data or {}).items():
        if not isinstance(s, dict):
            continue
        if str(s.get("status") or "online").lower() not in ("online", ""):
            continue
        base = _asset_display(s.get("base", ""))
        quote = _asset_display(s.get("quote", ""))
        if quote != QUOTE_ASSET:
            continue
        sym = f"{base}{quote}"
        if not sym or sym in EXCLUDED:
            continue
        if not _is_symbol_safe(sym):
            continue
        _PAIR_META[sym] = {"pair_id": pair_id, "ws_symbol": s.get("wsname") or f"{base}/{quote}"}
        out.append(sym)
    return sorted(out)

def get_spread_map():
    out = {}
    r = _SESSION.get(f"{BASE_URL}/0/public/Ticker", timeout=HTTP_TIMEOUT)
    data = _kraken_result(r)
    if not isinstance(data, dict):
        return out
    for sym, meta in _PAIR_META.items():
        try:
            row = data.get(meta["pair_id"]) or {}
            bid = float((row.get("b") or [0])[0])
            ask = float((row.get("a") or [0])[0])
            if bid <= 0 or ask <= 0:
                continue
            out[sym] = (ask - bid) / bid
        except Exception:
            continue
    return out


def get_market_stats_map():
    out = {}
    r = _SESSION.get(f"{BASE_URL}/0/public/Ticker", timeout=HTTP_TIMEOUT)
    data = _kraken_result(r)
    if not isinstance(data, dict):
        return out
    for sym, meta in _PAIR_META.items():
        try:
            row = data.get(meta["pair_id"]) or {}
            last = float((row.get("c") or [0])[0])
            volume = float((row.get("v") or [0, 0])[-1])
            vwap = float((row.get("p") or [0, 0])[-1] or last)
            session_open = float(row.get("o") or 0.0)
            session_change = ((last - session_open) / session_open * 100.0) if session_open > 0 else 0.0
            out[sym] = {
                "last_price": last,
                "quote_volume_24h": volume * vwap,
                "trade_count_24h": int((row.get("t") or [0, 0])[-1] or 0),
                # Kraken's ticker open is the start of the current UTC day.
                "change_pct_24h": session_change,
            }
        except Exception:
            continue
    return out


def _tradable_symbols(
    symbols,
    spread_map,
    market_map,
    excluded_symbols: set[str],
    *,
    include_rejections: bool = False,
):
    filter_counts = {
        "blocked": 0,
        "base_excluded": 0,
        "spread": 0,
        "market": 0,
        "price": 0,
        "quote_volume": 0,
        "trade_count": 0,
        "change_24h": 0,
    }
    eligible_symbols = []
    rejected = []

    def reject(sym, reason):
        filter_counts[reason] += 1
        if not include_rejections:
            return
        market = market_map.get(sym) or {}
        rejected.append({
            "symbol": sym,
            "reason_rejected": reason,
            "spread_pct": float(spread_map.get(sym, 0.0) or 0.0) * 100.0,
            "last_price": float(market.get("last_price") or 0.0),
            "quote_volume_24h": float(market.get("quote_volume_24h") or 0.0),
            "trade_count_24h": int(market.get("trade_count_24h") or 0),
            "change_pct_24h": float(market.get("change_pct_24h") or 0.0),
        })

    for sym in symbols:
        if sym in excluded_symbols:
            reject(sym, "blocked")
            continue
        if _base_asset(sym) in EXCLUDED_BASE_ASSETS:
            reject(sym, "base_excluded")
            continue
        spread = spread_map.get(sym)
        if spread is None or spread > SELECTOR_MAX_SPREAD_PCT:
            reject(sym, "spread")
            continue
        market = market_map.get(sym)
        if market is None:
            reject(sym, "market")
            continue
        if float(market["last_price"]) < SELECTOR_MIN_PRICE_USDC:
            reject(sym, "price")
            continue
        if float(market["quote_volume_24h"]) < SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:
            reject(sym, "quote_volume")
            continue
        if int(market["trade_count_24h"]) < SELECTOR_MIN_TRADE_COUNT_24H:
            reject(sym, "trade_count")
            continue
        change_24h = float(market["change_pct_24h"])
        if change_24h < SELECTOR_MIN_24H_CHANGE_PCT or change_24h > SELECTOR_MAX_24H_CHANGE_PCT:
            reject(sym, "change_24h")
            continue
        eligible_symbols.append(sym)
    if include_rejections:
        return eligible_symbols, filter_counts, rejected
    return eligible_symbols, filter_counts


def _log_tradable_universe(symbols, eligible_symbols, filter_counts) -> None:
    print(
        "TOKEN_SELECTOR: universe "
        f"total={len(symbols)} eligible={len(eligible_symbols)} "
        f"reject_blocked={filter_counts['blocked']} "
        f"reject_base={filter_counts['base_excluded']} "
        f"reject_spread={filter_counts['spread']} reject_market={filter_counts['market']} "
        f"reject_price={filter_counts['price']} reject_quote_volume={filter_counts['quote_volume']} "
        f"reject_trade_count={filter_counts['trade_count']} reject_change24h={filter_counts['change_24h']} "
        f"min_window={SELECTOR_MIN_WINDOW_PCT:.2f}% max_window={SELECTOR_MAX_WINDOW_PCT:.2f}% "
        f"min_price={SELECTOR_MIN_PRICE_USDC:.4f} "
        f"min_quote_volume_24h={SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:.0f} "
        f"min_trade_count_24h={SELECTOR_MIN_TRADE_COUNT_24H}"
    )


def _format_selector_candidate(prefix: str, item: dict) -> str:
    return (
        f"{prefix} symbol={item.get('symbol', '')} "
        f"spread={float(item.get('spread_pct', 0.0) or 0.0):.4f}% "
        f"quote_volume_24h={float(item.get('quote_volume_24h', 0.0) or 0.0):.0f} "
        f"trade_count_24h={int(item.get('trade_count_24h', 0) or 0)} "
        f"price={float(item.get('last_price', 0.0) or 0.0):.8f} "
        f"change_2m={item.get('pct', item.get('change_2m', 'na'))} "
        f"change_24h={float(item.get('change_pct_24h', 0.0) or 0.0):.4f}% "
        f"reason_rejected={item.get('reason_rejected', 'eligible')}"
    )


def _log_selector_rejections(filter_counts: dict, rejected: list[dict]) -> None:
    print(
        "TOKEN_SELECTOR_REJECT_SUMMARY "
        + " ".join(f"{key}={value}" for key, value in sorted(filter_counts.items()))
    )
    ranked = sorted(
        rejected,
        key=lambda item: (
            -float(item.get("quote_volume_24h", 0.0) or 0.0),
            float(item.get("spread_pct", 0.0) or 0.0),
            item.get("symbol", ""),
        ),
    )
    for item in ranked[:5]:
        try:
            item["change_2m"] = f"{float(change_window_pct(item['symbol']) or 0.0):.4f}%"
        except Exception:
            item["change_2m"] = "na"
        print(_format_selector_candidate("TOKEN_SELECTOR_TOP_REJECTED", item))


def _log_selector_eligible(candidates: list[dict]) -> None:
    for item in candidates[:5]:
        print(_format_selector_candidate("TOKEN_SELECTOR_TOP_ELIGIBLE", item))


def _log_selector_selected_reason(reason: str, **fields) -> None:
    _record_selector_decision(reason, **fields)
    detail = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"TOKEN_SELECTOR_SELECTED_REASON reason={reason} {detail}".rstrip())

def _recent_ohlc_bars(symbol: str) -> list:
    pair = (_PAIR_META.get(symbol) or {}).get("pair_id", symbol.replace("BTC", "XBT", 1))
    r = _SESSION.get(f"{BASE_URL}/0/public/OHLC", params={"pair": pair, "interval": 1}, timeout=HTTP_TIMEOUT)
    data = _kraken_result(r)
    if isinstance(data, list):
        return data

    payload = data or {}
    bars = payload.get(pair) or next((value for key, value in payload.items() if key != "last"), [])
    if not isinstance(bars, list):
        return []
    try:
        last_timestamp = int(float(payload.get("last")))
    except (TypeError, ValueError):
        return bars

    # Kraken may append an empty next-minute candle after `last`. It is not a
    # market update and made the former `k[-2:]` calculation read two flat bars.
    return [bar for bar in bars if isinstance(bar, (list, tuple)) and bar and int(float(bar[0])) <= last_timestamp]


def change_window_pct(symbol: str, minutes: int = WINDOW_MINUTES):
    # 1m OHLC change over the requested completed/current Kraken window.
    limit = max(2, minutes)
    k = _recent_ohlc_bars(symbol)
    if not isinstance(k, list) or len(k) < limit:
        return None
    window = k[-limit:]
    o = float(window[0][1])
    c = float(window[-1][4])
    if o <= 0:
        return None
    return (c - o) / o * 100.0


def current_direction_pct(symbol: str) -> float | None:
    """1-minute price direction check — is the token still moving up right now?"""
    try:
        return change_window_pct(symbol, minutes=2)
    except Exception:
        return None


def distance_from_recent_high_pct(symbol: str, minutes: int = 5) -> float | None:
    try:
        limit = max(2, int(minutes))
        k = _recent_ohlc_bars(symbol)
        if not isinstance(k, list) or len(k) < 2:
            return None
        window = k[-limit:]
        highs = [float(row[2]) for row in window]
        last = float(window[-1][4])
        high = max(highs)
        if high <= 0:
            return None
        return (high - last) / high
    except Exception:
        return None

def collect_candidates(excluded_symbols: set[str] | None = None, positive_only: bool = True):
    symbols = get_symbols_usdc_trading()
    spread_map = get_spread_map()
    market_map = get_market_stats_map()
    excluded_symbols = {str(sym).strip().upper() for sym in (excluded_symbols or set()) if str(sym).strip()}
    eligible_symbols, filter_counts, rejected = _tradable_symbols(
        symbols,
        spread_map,
        market_map,
        excluded_symbols,
        include_rejections=True,
    )
    _log_tradable_universe(symbols, eligible_symbols, filter_counts)
    _log_selector_rejections(filter_counts, rejected)
    candidates = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_symbol = {
            pool.submit(change_window_pct, sym): sym
            for sym in eligible_symbols
        }
        for future in as_completed(future_to_symbol):
            try:
                pct = future.result()
            except Exception:
                continue
            if pct is None:
                continue
            if positive_only and pct <= 0:
                continue
            sym = future_to_symbol[future]
            spread_pct = float(spread_map.get(sym, 0.0)) * 100.0
            if not _candidate_window_is_eligible(pct, spread_pct):
                continue
            market = market_map.get(sym) or {}
            candidates.append({
                "symbol": sym,
                "pct": pct,
                "spread_pct": spread_pct,
                "last_price": float(market.get("last_price") or 0.0),
                "quote_volume_24h": float(market.get("quote_volume_24h") or 0.0),
                "trade_count_24h": int(market.get("trade_count_24h") or 0),
                "change_pct_24h": float(market.get("change_pct_24h") or 0.0),
            })
    candidates.sort(key=lambda item: (item["pct"], -item["spread_pct"], item["symbol"]), reverse=True)
    _log_selector_eligible(candidates)
    return candidates


def collect_top_movers(excluded_symbols: set[str] | None = None, positive_only: bool = True):
    # A fallback must never relax liquidity, spread, price, or movement gates.
    # It only changes ranking behaviour after the normal tradable universe is built.
    return collect_candidates(excluded_symbols=excluded_symbols, positive_only=positive_only)


def choose_tradable_anchor(current_symbol: str, excluded_symbols: set[str] | None = None):
    """Return a liquid anchor only when the active symbol is no longer tradable.

    The anchor does not create an entry signal; it gives the common strategy a
    reliable market-data stream while the selector waits for its next real mover.
    """
    symbols = get_symbols_usdc_trading()
    spread_map = get_spread_map()
    market_map = get_market_stats_map()
    excluded = {str(sym).strip().upper() for sym in (excluded_symbols or set()) if str(sym).strip()}
    eligible_symbols, _ = _tradable_symbols(symbols, spread_map, market_map, excluded)
    active = str(current_symbol or "").strip().upper()
    if active in eligible_symbols:
        return None, True
    anchors = []
    for sym in eligible_symbols:
        market = market_map.get(sym) or {}
        anchors.append({
            "symbol": sym,
            "pct": 0.0,
            "spread_pct": float(spread_map.get(sym, 0.0)) * 100.0,
            "last_price": float(market.get("last_price") or 0.0),
            "quote_volume_24h": float(market.get("quote_volume_24h") or 0.0),
            "trade_count_24h": int(market.get("trade_count_24h") or 0),
            "change_pct_24h": float(market.get("change_pct_24h") or 0.0),
        })
    anchors.sort(
        key=lambda item: (
            -item["quote_volume_24h"],
            -item["trade_count_24h"],
            item["spread_pct"],
            item["symbol"],
        )
    )
    return (anchors[0] if anchors else None), False


def rank_candidates(candidates, score_map, quality_map=None):
    min_qs = float(os.getenv("SELECTOR_MIN_QUALITY_SCORE", "0.3"))
    respect_blocked = os.getenv("SELECTOR_RESPECT_BLOCKED", "1") != "0"

    ranked = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        memory = score_map.get(symbol, {})
        closed_trades = int(memory.get("closed_trades") or 0)
        if closed_trades <= 0 and abs(float(candidate["pct"])) > SELECTOR_MAX_WINDOW_PCT_UNKNOWN:
            continue
        history_bonus = float(memory.get("history_bonus") or 0.0)
        unknown_penalty = SELECTOR_UNKNOWN_SCORE_PENALTY if closed_trades <= 0 else 0.0

        # Quality score filter
        quality_score = 0.5  # neutral default (no history)
        if quality_map:
            tok = quality_map.get(symbol, {})
            quality_score = float(tok.get("quality_score", 0.5))
            if quality_score == 0.0 and respect_blocked:
                print(f"TOKEN_SELECTOR: skip {symbol} quality_score=0.0 (blocked) reason={tok.get('block_reason','')}")
                continue
            if quality_score < min_qs:
                print(f"TOKEN_SELECTOR: skip {symbol} quality_score={quality_score:.3f} < min={min_qs}")
                continue

        # Final score = momentum-based score × quality multiplier
        raw_score = float(candidate["pct"]) + history_bonus - unknown_penalty
        final_score = raw_score * quality_score

        ranked.append({
            **candidate,
            "final_score": final_score,
            "raw_score": raw_score,
            "quality_score": quality_score,
            "history_bonus": history_bonus,
            "unknown_penalty": unknown_penalty,
            "is_toxic": bool(memory.get("is_toxic")),
            "toxic_reasons": str(memory.get("toxic_reasons") or ""),
            "closed_trades": closed_trades,
            "winrate_5": memory.get("winrate_5"),
            "pnl_usdc_5": memory.get("pnl_usdc_5"),
            "avg_pnl_pct_5": memory.get("avg_pnl_pct_5"),
        })
    ranked.sort(
        key=lambda item: (
            1 if item["is_toxic"] else 0,
            -(item["final_score"]),
            -(item["pct"]),
            item["symbol"],
        )
    )
    return ranked


def pick_best_candidate(score_map, excluded_symbols: set[str] | None = None, quality_map=None):
    candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=True)
    if not candidates:
        return None, []
    ranked = rank_candidates(candidates, score_map, quality_map=quality_map)
    # Choose best candidate whose current 2-min direction is acceptable (not in free fall)
    chosen = None
    near_high_rejected: set[str] = set()
    for item in ranked:
        if item.get("is_toxic"):
            continue
        dir_pct = current_direction_pct(item["symbol"])
        if dir_pct is not None and dir_pct < SELECTOR_MIN_DIRECTION_PCT:
            print(
                f"TOKEN_SELECTOR: skip {item['symbol']} direction_2m={dir_pct:.3f}% < {SELECTOR_MIN_DIRECTION_PCT}% — move exhausted"
            )
            continue
        dist = distance_from_recent_high_pct(item["symbol"], minutes=5)
        if dist is not None and dist < SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT:
            near_high_rejected.add(item["symbol"])
            print(
                f"TOKEN_SELECTOR: skip {item['symbol']} near_5m_high "
                f"dist={dist*100:.3f}% < {SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT*100:.3f}%"
            )
            continue
        chosen = item
        break
    if chosen is None:
        # Preserve Binance's preference for a non-chasing move. On Kraken's
        # smaller USDC universe, however, keeping a stale symbol forever is
        # worse than selecting the best liquid mover and letting entry gates
        # decide whether it is actionable.
        fallback = next((item for item in ranked if not item["is_toxic"] and item["symbol"] not in near_high_rejected), None)
        if fallback is None and SELECTOR_FALLBACK_ON_RECENT_HIGH:
            fallback = next((item for item in ranked if not item["is_toxic"]), None)
            if fallback is not None:
                fallback = {**fallback, "selection_mode": "RECENT_HIGH_FALLBACK"}
                print(
                    f"TOKEN_SELECTOR: selecting {fallback['symbol']} after recent-high filter "
                    f"var={fallback['pct']:.2f}%"
                )
        chosen = fallback
    return chosen, ranked


def choose_fallback_candidate(score_map, excluded_symbols: set[str] | None = None, quality_map=None):
    positive_candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=True)
    positive_ranked = rank_candidates(positive_candidates, score_map, quality_map=quality_map) if positive_candidates else []
    any_candidates = collect_candidates(excluded_symbols=excluded_symbols, positive_only=False)
    any_ranked = rank_candidates(any_candidates, score_map, quality_map=quality_map) if any_candidates else []

    fallback = None
    fallback_mode = ""

    if any_ranked:
        fallback = next((item for item in any_ranked if not item["is_toxic"]), None)
        fallback_mode = "best_eligible_any_momentum"

    return fallback, fallback_mode, positive_ranked, any_ranked


def choose_top_mover_fallback(excluded_symbols: set[str] | None = None, quality_map=None):
    if not SELECTOR_TOP_MOVER_FALLBACK:
        return None, []
    candidates = collect_top_movers(excluded_symbols=excluded_symbols, positive_only=True)
    respect_blocked = os.getenv("SELECTOR_RESPECT_BLOCKED", "1") != "0"
    ranked = []
    for item in candidates:
        tok = (quality_map or {}).get(item["symbol"], {})
        if respect_blocked and float(tok.get("quality_score", 0.5)) == 0.0:
            print(
                f"TOKEN_SELECTOR: top_mover skip {item['symbol']} "
                f"quality_score=0.0 reason={tok.get('block_reason','')}"
            )
            continue
        ranked.append({
            **item,
            "final_score": float(item["pct"]),
            "raw_score": float(item["pct"]),
            "quality_score": float(tok.get("quality_score", 0.5)),
            "history_bonus": 0.0,
            "unknown_penalty": 0.0,
            "is_toxic": False,
            "toxic_reasons": "",
            "closed_trades": 0,
            "winrate_5": None,
            "pnl_usdc_5": None,
            "avg_pnl_pct_5": None,
        })
    ranked.sort(key=lambda item: (item["pct"], -item["spread_pct"], item["symbol"]), reverse=True)
    return (ranked[0] if ranked else None), ranked

def log_selector_memory_state(sync_info, ranked):
    print(
        "TOKEN_SELECTOR: memory "
        f"db={sync_info.get('db_path','')} scanned_files={sync_info.get('scanned_files',0)} "
        f"skipped_files={sync_info.get('skipped_files',0)} imported_closed={sync_info.get('imported_closed_trades',0)} "
        f"scored_tokens={sync_info.get('scored_tokens',0)}"
    )
    for idx, item in enumerate(ranked[:5], start=1):
        winrate = item.get("winrate_5")
        pnl_usdc_5 = item.get("pnl_usdc_5")
        toxic = f" toxic={item['toxic_reasons']}" if item.get("is_toxic") else ""
        print(
            "TOKEN_SELECTOR: "
            f"cand#{idx} {item['symbol']} var={item['pct']:.2f}% "
            f"bonus={item['history_bonus']:+.2f} unk_penalty={item.get('unknown_penalty', 0.0):+.2f} "
            f"score={item['final_score']:.2f} closed={item['closed_trades']} "
            f"price={item.get('last_price', 0.0):.6f} "
            f"qv24h={item.get('quote_volume_24h', 0.0):.0f} trades24h={item.get('trade_count_24h', 0)} "
            f"pnl5={0.0 if pnl_usdc_5 is None else float(pnl_usdc_5):+.4f} "
            f"win5={'--' if winrate is None else f'{float(winrate):.1f}%'}"
            f"{toxic}"
        )

def write_service_env(symbol: str, pct: float, profile: str) -> bool:
    # IMPORTANT: This tool must NOT change DRY_RUN or unrelated keys.
    # It updates SYMBOL and PROFILE in-place inside .service.env.
    existing_txt = ""
    try:
        existing_txt = open(SERVICE_ENV_PATH, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        existing_txt = ""

    lines = existing_txt.splitlines(True)

    def is_symbol_line(raw: str) -> bool:
        return bool(re.match(r'^\s*SYMBOL\s*=.*$', raw))

    def is_profile_line(raw: str) -> bool:
        return bool(re.match(r'^\s*PROFILE\s*=.*$', raw))

    changed = False
    profile_changed = False
    new_lines = []
    for raw in lines:
        if is_symbol_line(raw):
            new_lines.append(f"SYMBOL={symbol}\n")
            changed = True
        elif is_profile_line(raw):
            new_lines.append(f"PROFILE={profile}\n")
            profile_changed = True
        else:
            new_lines.append(raw)

    if not changed:
        # If no SYMBOL line exists, prepend it.
        new_lines = [f"SYMBOL={symbol}\n"] + new_lines
    if not profile_changed:
        new_lines = [f"PROFILE={profile}\n"] + new_lines

    updated_txt = "".join(new_lines)
    if updated_txt == existing_txt:
        print(
            f"TOKEN_SELECTOR: unchanged {SERVICE_ENV_PATH} "
            f"SYMBOL={symbol} PROFILE={profile} DRY_RUN={_read_env_file(SERVICE_ENV_PATH).get('DRY_RUN','')}"
        )
        return False

    with open(SERVICE_ENV_PATH, "w", encoding="utf-8") as f:
        f.write(updated_txt)

    # Observability only (read-only).
    env_now = _read_env_file(SERVICE_ENV_PATH)
    print(
        f"TOKEN_SELECTOR: wrote {SERVICE_ENV_PATH} "
        f"SYMBOL={symbol} VAR{WINDOW_MINUTES}M={pct:.2f}% MAX_SPREAD={SELECTOR_MAX_SPREAD_PCT*100:.2f}% "
        f"PROFILE={env_now.get('PROFILE', profile)} DRY_RUN={env_now.get('DRY_RUN','')} QUOTE={QUOTE_ASSET}"
    )
    return True


def restart_bot_if_symbol_changed(previous_symbol: str, new_symbol: str):
    if not SELECTOR_RESTART_BOT_ON_CHANGE:
        return
    previous = (previous_symbol or "").strip().upper()
    new = (new_symbol or "").strip().upper()
    if not new or previous == new:
        return
    try:
        import subprocess
        result = subprocess.run(
            ["systemctl", "restart", BOT_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=20,
        )
        ok = result.returncode == 0
        output = (result.stdout or result.stderr or "").strip()
        print(
            f"TOKEN_SELECTOR: bot restart requested service={BOT_SERVICE_NAME} "
            f"symbol={previous or '-'}->{new} ok={int(ok)} output={output[:200]}"
        )
    except Exception as e:
        print(
            f"TOKEN_SELECTOR: bot restart failed service={BOT_SERVICE_NAME} "
            f"symbol={previous or '-'}->{new} err={e}"
        )


def main():
    env_now = _read_env_file(SERVICE_ENV_PATH)
    current_symbol = (env_now.get("SYMBOL") or "").strip().upper()
    wallet_lock = _runtime_wallet_lock()
    if wallet_lock:
        locked_symbol = wallet_lock.get("symbol", "")
        print(
            "TOKEN_SELECTOR: wallet position lock active — no token switch "
            f"current={current_symbol or '-'} locked={locked_symbol} "
            f"source={wallet_lock.get('source','')} qty={wallet_lock.get('qty','')} "
            f"notional={wallet_lock.get('notional','')} age={wallet_lock.get('age_sec',''):.1f}s"
        )
        if locked_symbol and locked_symbol != current_symbol:
            write_service_env(locked_symbol, 0.0, DEFAULT_PROFILE)
        _log_selector_selected_reason("WALLET_POSITION_LOCK", symbol=locked_symbol or current_symbol or "-")
        return 0

    sync_info = sync_trade_memory()
    score_map = load_token_scores()
    blocked_symbols = load_blocked_symbols(BLOCKED_SYMBOLS_PATH)
    current_symbol_score = score_map.get(current_symbol, {}) if current_symbol else {}
    current_symbol_blocked = current_symbol in blocked_symbols or bool(current_symbol_score.get("is_toxic"))
    if blocked_symbols:
        print(
            f"TOKEN_SELECTOR: loaded blocked symbols count={len(blocked_symbols)} "
            f"file={BLOCKED_SYMBOLS_PATH}"
        )

    # Load token quality map (rebuild if requested)
    quality_file = ROOT_PATH / "state" / "token_quality.json"
    rebuild_on_start = os.getenv("SELECTOR_QUALITY_REBUILD_ON_START", "1") != "0"
    if rebuild_on_start:
        try:
            import subprocess, sys
            result = subprocess.run(
                [sys.executable, str(ROOT_PATH / "tools" / "rebuild_token_quality.py"),
                 "--out", str(quality_file)],
                capture_output=True, text=True, timeout=30, cwd=str(ROOT_PATH),
            )
            if result.returncode == 0:
                print(f"TOKEN_SELECTOR: quality map rebuilt → {quality_file}")
            else:
                print(f"TOKEN_SELECTOR: quality rebuild failed: {result.stderr[:200]}")
        except Exception as e:
            print(f"TOKEN_SELECTOR: quality rebuild error: {e}")
    quality_map = load_quality_map(quality_file)
    blocked_by_quality = [s for s, t in quality_map.items() if t.get("quality_score", 1.0) == 0.0]
    if blocked_by_quality:
        print(f"TOKEN_SELECTOR: quality-blocked tokens ({len(blocked_by_quality)}): {blocked_by_quality}")

    chosen, ranked = pick_best_candidate(score_map, excluded_symbols=blocked_symbols, quality_map=quality_map)
    log_selector_memory_state(sync_info, ranked)

    if not chosen:
        if current_symbol and current_symbol_blocked:
            fallback, fallback_mode, positive_ranked, any_ranked = choose_fallback_candidate(
                score_map,
                excluded_symbols=blocked_symbols,
                quality_map=quality_map,
            )
            fallback_ranked = positive_ranked if positive_ranked else any_ranked
            if fallback_ranked:
                log_selector_memory_state(sync_info, fallback_ranked)
            if fallback:
                current_reason = current_symbol_score.get("toxic_reasons") or "blocked_symbol"
                print(
                    f"TOKEN_SELECTOR: fallback selected {fallback['symbol']} "
                    f"mode={fallback_mode} current_blocked={current_symbol} "
                    f"reason={current_reason} "
                    f"var={fallback['pct']:.2f}% toxic={int(bool(fallback.get('is_toxic')))}"
                )
                write_service_env(fallback["symbol"], fallback["pct"], DEFAULT_PROFILE)
                restart_bot_if_symbol_changed(current_symbol, fallback["symbol"])
                _log_selector_selected_reason(
                    "CURRENT_SYMBOL_BLOCKED_FALLBACK",
                    symbol=fallback["symbol"],
                    current=current_symbol,
                )
                return 0
        anchor, current_is_tradable = choose_tradable_anchor(
            current_symbol,
            excluded_symbols=blocked_symbols,
        )
        if anchor and not current_is_tradable:
            print(
                f"TOKEN_SELECTOR: anchor selected {anchor['symbol']} "
                f"reason=current_not_tradable current={current_symbol or '-'} "
                f"qv24h={anchor['quote_volume_24h']:.0f} "
                f"spread={anchor['spread_pct']:.3f}%"
            )
            write_service_env(anchor["symbol"], anchor["pct"], DEFAULT_PROFILE)
            restart_bot_if_symbol_changed(current_symbol, anchor["symbol"])
            _save_selector_state({
                "last_switch_ts": time.time(),
                "last_switch_symbol": anchor["symbol"],
            })
            _log_selector_selected_reason(
                "ANCHOR_CURRENT_NOT_TRADABLE",
                symbol=anchor["symbol"],
                current=current_symbol,
            )
            return 0
        top_mover, top_ranked = choose_top_mover_fallback(
            excluded_symbols=blocked_symbols,
            quality_map=quality_map,
        )
        if top_mover:
            log_selector_memory_state(sync_info, top_ranked)
            print(
                f"TOKEN_SELECTOR: top_mover_fallback selected {top_mover['symbol']} "
                f"var={top_mover['pct']:.2f}% strict_reason=no_eligible_positive "
                f"window={WINDOW_MINUTES}m qv24h={top_mover.get('quote_volume_24h', 0.0):.0f} "
                f"spread={top_mover.get('spread_pct', 0.0):.3f}%"
            )
            write_service_env(top_mover["symbol"], top_mover["pct"], DEFAULT_PROFILE)
            restart_bot_if_symbol_changed(current_symbol, top_mover["symbol"])
            _save_selector_state({
                "last_switch_ts": time.time(),
                "last_switch_symbol": top_mover["symbol"],
            })
            _log_selector_selected_reason(
                "TOP_MOVER_FALLBACK",
                symbol=top_mover["symbol"],
                current=current_symbol,
            )
            return 0
        print(
            f"TOKEN_SELECTOR: no eligible positive {QUOTE_ASSET} symbol found "
            f"({WINDOW_MINUTES}m window, max_spread={SELECTOR_MAX_SPREAD_PCT*100:.2f}% "
            f"min_price={SELECTOR_MIN_PRICE_USDC:.4f} "
            f"min_quote_volume_24h={SELECTOR_MIN_QUOTE_VOLUME_USDC_24H:.0f} "
            f"min_trade_count_24h={SELECTOR_MIN_TRADE_COUNT_24H})"
        )
        _log_selector_selected_reason("NO_ELIGIBLE_POSITIVE", symbol=current_symbol or "-")
        return 0
    raw_top = ranked[0] if ranked else None
    if raw_top and raw_top["symbol"] != chosen["symbol"] and raw_top.get("is_toxic"):
        print(
            f"TOKEN_SELECTOR: skipped toxic top candidate {raw_top['symbol']} "
            f"var={raw_top['pct']:.2f}% reasons={raw_top.get('toxic_reasons') or 'n/a'}"
        )

    # --- Garde minimale : ne switche pas si le token actuel est jeune et encore valable ---
    selector_state = _load_selector_state()
    last_switch_ts = float(selector_state.get("last_switch_ts", 0))
    last_switch_symbol = selector_state.get("last_switch_symbol", "")
    hold_age_min = (time.time() - last_switch_ts) / 60.0

    is_new_token = chosen["symbol"] != current_symbol
    # Un token plat peut sortir plus tôt que la garde standard, mais pas avant
    # d'avoir laissé au bot le temps de construire une fenêtre d'entrée exploitable.
    current_in_ranked = next((c for c in ranked if c["symbol"] == current_symbol), None)
    current_var_pct = abs(float(current_in_ranked["pct"])) if current_in_ranked else 0.0
    current_is_flat = current_var_pct < SELECTOR_MIN_ACTIVE_VAR_PCT
    minimum_hold_remaining = _minimum_hold_remaining_minutes(hold_age_min)
    if (
        is_new_token
        and not current_symbol_blocked
        and current_symbol
        and minimum_hold_remaining > 0
    ):
        print(
            f"TOKEN_SELECTOR: keeping {current_symbol} absolute hold "
            f"hold_age={hold_age_min:.1f}min < {SELECTOR_FLAT_MIN_HOLD_MINUTES}min "
            f"remaining={minimum_hold_remaining:.1f}min "
            f"flat={int(current_is_flat)} — no switch"
        )
        current_score = current_in_ranked["final_score"] if current_in_ranked else 0.0
        write_service_env(current_symbol, current_score, DEFAULT_PROFILE)
        restart_bot_if_symbol_changed(current_symbol, current_symbol)
        _log_selector_selected_reason("MINIMUM_HOLD", symbol=current_symbol)
        return 0
    if current_is_flat and is_new_token:
        print(
            f"TOKEN_SELECTOR: current {current_symbol} is flat "
            f"(var={current_var_pct:.2f}% < {SELECTOR_MIN_ACTIVE_VAR_PCT}%) "
            f"and flat hold elapsed ({hold_age_min:.1f}min >= {SELECTOR_FLAT_MIN_HOLD_MINUTES}min) — switching"
        )
    elif (
        is_new_token
        and not current_symbol_blocked
        and current_symbol
        and hold_age_min < SELECTOR_MIN_HOLD_MINUTES
    ):
        # Seulement switcher si le gain de score est suffisant pour justifier l'interruption
        current_score = current_in_ranked["final_score"] if current_in_ranked else 0.0
        score_delta = chosen["final_score"] - current_score
        if score_delta < SELECTOR_HYSTERESIS_PCT:
            print(
                f"TOKEN_SELECTOR: keeping {current_symbol} "
                f"hold_age={hold_age_min:.1f}min < {SELECTOR_MIN_HOLD_MINUTES}min "
                f"delta={score_delta:+.3f} < {SELECTOR_HYSTERESIS_PCT} — no switch"
            )
            write_service_env(current_symbol, current_score, DEFAULT_PROFILE)
            restart_bot_if_symbol_changed(current_symbol, current_symbol)
            _log_selector_selected_reason("HYSTERESIS_HOLD", symbol=current_symbol)
            return 0
        print(
            f"TOKEN_SELECTOR: early switch authorized "
            f"hold_age={hold_age_min:.1f}min delta={score_delta:+.3f} >= {SELECTOR_HYSTERESIS_PCT}"
        )

    print(
        f"TOKEN_SELECTOR: selected {chosen['symbol']} "
        f"var={chosen['pct']:.2f}% bonus={chosen['history_bonus']:+.2f} score={chosen['final_score']:.2f} "
        f"price={chosen.get('last_price', 0.0):.6f} "
        f"qv24h={chosen.get('quote_volume_24h', 0.0):.0f} "
        f"trades24h={chosen.get('trade_count_24h', 0)}"
    )
    write_service_env(chosen["symbol"], chosen["pct"], DEFAULT_PROFILE)
    restart_bot_if_symbol_changed(current_symbol, chosen["symbol"])
    if is_new_token or not last_switch_ts:
        _save_selector_state({
            "last_switch_ts": time.time(),
            "last_switch_symbol": chosen["symbol"],
        })
    _log_selector_selected_reason(
        "BEST_ELIGIBLE_CANDIDATE",
        symbol=chosen["symbol"],
        current=current_symbol or "-",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
