"""Read-only helpers for explaining entry availability and hold decisions."""

from __future__ import annotations

import math
from typing import Any


TRACE_FIELD_ORDER = (
    "symbol",
    "profile",
    "strategy",
    "entry_stage",
    "has_new_tick",
    "pending_switch",
    "p_entry_enabled",
    "has_p_window",
    "p1",
    "p2",
    "p3",
    "p4",
    "p_rising",
    "p_sample_interval_sec",
    "mom_ok",
    "mom_pct",
    "mom_min_pct",
    "mom_range_pct",
    "max_mom_pct",
    "range_enabled",
    "range_ok",
    "burst_enabled",
    "burst_ok",
    "up_ratio",
    "hard_min_up_ratio",
    "strict_up_moves",
    "entry_min_strict_ups",
    "tape_progress_pct",
    "entry_min_tape_progress_pct",
    "required_tape_progress_pct",
    "min_range_entry_pct",
    "min_range_vs_spread",
    "required_range_pct",
    "spread_pct",
    "max_spread_pct",
    "planned_tp_pct",
    "planned_cost_pct",
    "planned_net_pct",
    "plan_viable",
    "signal_snapshot_ready",
    "rsi",
    "ema1_ok",
    "ema5_ok",
    "vol_ok",
    "near_peak",
    "pic_filter_enabled",
    "blocked_symbol",
    "quote_asset",
    "quote_free",
    "min_notional",
    "sizing_cap",
    "qty_estimated",
    "sizing_ok",
    "can_buy",
    "order_limit_price",
    "order_qty",
    "order_notional",
    "final_hold_reason",
)


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def p_tape_snapshot(p1: Any, p2: Any, p3: Any, p4: Any) -> dict:
    """Describe the existing P1..P4 gate without changing its behavior."""
    points = tuple(_float_or_none(value) for value in (p1, p2, p3, p4))
    has_p_window = all(value is not None and value > 0 for value in points)
    if not has_p_window:
        return {
            "has_p_window": False,
            "p_rising": False,
            "strict_up_moves": 0,
            "tape_progress_pct": 0.0,
        }

    newest, p2_value, p3_value, oldest = points
    strict_up_moves = int(newest > p2_value) + int(p2_value > p3_value) + int(p3_value > oldest)
    tape_progress_pct = ((newest - oldest) / oldest) * 100.0
    return {
        "has_p_window": True,
        "p_rising": bool(
            newest >= p2_value
            and p2_value >= p3_value
            and p3_value >= oldest
            and newest > oldest
        ),
        "strict_up_moves": strict_up_moves,
        "tape_progress_pct": tape_progress_pct,
    }


def entry_gate_requirements(*, spread: Any, cfg: Any) -> dict:
    """Expose the existing P-entry thresholds without changing their policy."""
    spread_value = max(0.0, _float_or_none(spread) or 0.0)
    max_mom_pct = float(getattr(cfg, "momMaxPct", 1.0) or 1.0)
    min_range_entry_pct = float(getattr(cfg, "minRangeEntryPct", 0.0) or 0.0)
    min_range_vs_spread = float(getattr(cfg, "minRangeVsSpread", 0.0) or 0.0)
    min_tape_progress_pct = float(getattr(cfg, "entryMinTapeProgressPct", 0.0) or 0.0)
    min_tape_progress_vs_spread = float(
        getattr(cfg, "entryMinTapeProgressVsSpread", 0.0) or 0.0
    )

    return {
        "max_mom_pct": max_mom_pct,
        "min_range_entry_pct": min_range_entry_pct,
        "min_range_vs_spread": min_range_vs_spread,
        "required_range_pct": max(
            min_range_entry_pct,
            spread_value * min_range_vs_spread,
        ),
        "min_tape_progress_pct": min_tape_progress_pct,
        "min_tape_progress_vs_spread": min_tape_progress_vs_spread,
        "required_tape_progress_pct": max(
            min_tape_progress_pct,
            spread_value * min_tape_progress_vs_spread,
        ),
        "entry_min_strict_ups": max(
            1,
            int(getattr(cfg, "entryMinStrictUps", 1) or 1),
        ),
        "hard_min_up_ratio": float(
            getattr(cfg, "entryHardMinUpRatio", 0.0) or 0.0
        ),
    }


def quote_sizing_snapshot(
    *,
    quote_free: Any,
    quote_asset: str,
    min_notional: Any,
    cap: Any,
    fee_buffer_pct: Any,
    dry_run: bool,
    has_position: bool,
) -> dict:
    """Mirror the existing pre-order quote reserve calculation for diagnostics."""
    free = _float_or_none(quote_free)
    minimum = max(0.0, _float_or_none(min_notional) or 0.0)
    configured_cap = max(0.0, _float_or_none(cap) or 0.0)
    fee_buffer = max(0.0, _float_or_none(fee_buffer_pct) or 0.0)

    quote_reserve = 0.0
    spendable_quote = 0.0
    sizing_cap = 0.0
    if free is not None:
        safety_buffer = max(fee_buffer, 0.0025)
        quote_reserve = min(free * safety_buffer, max(0.0, free - minimum))
        spendable_quote = max(0.0, free - quote_reserve)
        sizing_cap = min(configured_cap, spendable_quote)

    can_buy = bool(not has_position and free is not None and sizing_cap >= minimum and minimum > 0)
    blocking_reason = ""
    if not dry_run and not has_position and free is not None and not can_buy:
        blocking_reason = "LIVE_NO_QUOTE_BALANCE"

    return {
        "quote_asset": str(quote_asset or "USDC").upper(),
        "quote_free": free,
        "min_notional": minimum,
        "configured_cap": configured_cap,
        "quote_reserve": quote_reserve,
        "spendable_quote": spendable_quote,
        "sizing_cap": sizing_cap,
        "can_buy": can_buy,
        "blocking_reason": blocking_reason,
    }


def format_entry_gate_trace(trace: dict) -> str:
    """Format a compact, secret-free, machine-readable trace line."""
    fields = []
    emitted = set()
    for key in TRACE_FIELD_ORDER:
        if key not in trace:
            continue
        emitted.add(key)
        fields.append(f"{key}={_format_trace_value(trace[key])}")
    for key in sorted(k for k in trace if k not in emitted):
        fields.append(f"{key}={_format_trace_value(trace[key])}")
    return " ".join(fields)


def _format_trace_value(value: Any) -> str:
    if value is None:
        return "na"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "na"
        return f"{value:.10g}"
    text = str(value).strip()
    return text.replace(" ", "_") if text else "na"
