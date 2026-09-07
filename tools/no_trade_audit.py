#!/usr/bin/env python3
"""Read-only audit explaining why a bot session did not reach an order."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


ORDER_EVENT_MARKERS = ("ORDER", "BUY", "SELL")
API_ORDER_ERROR_MARKERS = (
    "ADDORDER",
    "EORDER:",
    "ORDER_STATE_UNKNOWN",
    "INSUFFICIENT FUNDS",
    "NOT PERMITTED FOR THIS ACCOUNT",
)


def _safe_float(value: Any) -> float | None:
    try:
        result = float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    return None


def _reason_type(value: Any) -> str:
    return str(value or "").strip().split()[0] or "UNKNOWN"


def _parse_kv(line: str) -> dict[str, str]:
    return {
        key: value
        for key, value in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)=([^\s]+)", line)
    }


def _p_rising(values: dict[str, Any]) -> bool | None:
    explicit = _bool(values.get("p_rising"))
    if explicit is not None:
        return explicit
    points = [_safe_float(values.get(f"p{idx}", values.get(f"P{idx}"))) for idx in range(1, 5)]
    if any(point is None or point <= 0 for point in points):
        return None
    newest, p2, p3, oldest = points
    return newest >= p2 >= p3 >= oldest and newest > oldest


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": round(mean(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _runtime_snapshot(runtime_dir: Path) -> dict:
    files = {
        name: _read_json(runtime_dir / name)
        for name in (
            "wallet.json",
            "account.json",
            "position.json",
            "portfolio.json",
            "bot_status.json",
            "selector_state.json",
        )
    }
    status = files["bot_status.json"]
    wallet = files["wallet.json"]
    position = files["position.json"]
    quote_asset = str(status.get("quote_asset") or "").upper()
    balances = wallet.get("balances") if isinstance(wallet, dict) else []
    if not quote_asset:
        known_quotes = {str(row.get("asset", "")).upper() for row in balances or []}
        quote_asset = next((asset for asset in ("USDC", "USD", "EUR") if asset in known_quotes), "USDC")

    quote_free = status.get("quote_free")
    if quote_free is None:
        for row in balances or []:
            if str(row.get("asset", "")).upper() == quote_asset:
                quote_free = row.get("free")
                break

    qty = _safe_float(position.get("qty")) or 0.0
    return {
        "files": files,
        "quote_asset": quote_asset,
        "quote_free": _safe_float(quote_free),
        "min_notional": _safe_float(status.get("min_notional")),
        "sizing_cap": _safe_float(status.get("sizing_cap")),
        "can_buy": _bool(status.get("can_buy")),
        "blocking_reason": str(status.get("blocking_reason") or ""),
        "position_present": qty > 0,
        "position": {key: position.get(key) for key in ("symbol", "qty", "reason")},
        "selector_state": files["selector_state.json"],
        "status": status,
    }


def audit_paths(logs_dir: Path, runtime_dir: Path) -> dict:
    csv_rows: list[dict] = []
    csv_trace_records: list[dict] = []
    log_records: list[dict] = []
    entry_gate_passes: list[dict] = []
    api_order_errors: list[str] = []
    selector_no_eligible = 0

    for csv_path in sorted(logs_dir.rglob("*.csv")) if logs_dir.exists() else []:
        try:
            with csv_path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    if row:
                        row["_path"] = str(csv_path)
                        csv_rows.append(row)
                        trace_raw = row.get("entry_gate_trace")
                        if trace_raw:
                            try:
                                trace = json.loads(trace_raw)
                            except (TypeError, ValueError, json.JSONDecodeError):
                                trace = None
                            if isinstance(trace, dict):
                                trace["_kind"] = "csv_trace"
                                csv_trace_records.append(trace)
        except (OSError, csv.Error):
            continue

    for log_path in sorted(logs_dir.rglob("*.log")) if logs_dir.exists() else []:
        try:
            for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if "TOKEN_SELECTOR:" in line and "no eligible positive" in line:
                    selector_no_eligible += 1
                if any(marker in line.upper() for marker in API_ORDER_ERROR_MARKERS):
                    api_order_errors.append(line[-500:])
                if "ENTRY_GATE_TRACE " in line:
                    payload = _parse_kv(line.split("ENTRY_GATE_TRACE ", 1)[1])
                    payload["_kind"] = "trace"
                    log_records.append(payload)
                elif "ENTRY_GATE_PASS " in line:
                    payload = _parse_kv(line.split("ENTRY_GATE_PASS ", 1)[1])
                    payload["_kind"] = "gate_pass"
                    entry_gate_passes.append(payload)
                elif "DECIDE_HOLD reason=" in line:
                    payload = _parse_kv(line)
                    reason_match = re.search(r"DECIDE_HOLD reason=([^\s]+)", line)
                    payload["reason"] = reason_match.group(1) if reason_match else payload.get("reason", "")
                    payload["_kind"] = "hold"
                    log_records.append(payload)
        except OSError:
            continue

    events = Counter(row.get("event") or "UNKNOWN" for row in csv_rows)
    reasons = Counter(_reason_type(row.get("reason")) for row in csv_rows if row.get("reason"))
    event_by_symbol: dict[str, Counter] = defaultdict(Counter)
    reason_by_symbol: dict[str, Counter] = defaultdict(Counter)
    for row in csv_rows:
        symbol = row.get("symbol") or "UNKNOWN"
        event_by_symbol[symbol][row.get("event") or "UNKNOWN"] += 1
        if row.get("reason"):
            reason_by_symbol[symbol][_reason_type(row.get("reason"))] += 1

    order_events = {
        event: count
        for event, count in events.items()
        if any(marker in event.upper() for marker in ORDER_EVENT_MARKERS)
    }
    traces = csv_trace_records or [
        record for record in log_records if record.get("_kind") in {"trace", "hold"}
    ]
    mom_ok_p_not_rising = 0
    p_rising_then_blocked = 0
    tape_progress = []
    fee_edge = []
    tape_to_fee_ratios = []
    spreads = []
    near_peak_distances = []
    near_peak_mom_ok = 0
    near_peak_spread_ok = 0
    near_peak_candidate_otherwise = 0
    entry_stages = Counter()

    for trace in traces:
        entry_stages[str(trace.get("entry_stage") or "UNSPECIFIED")] += 1
        p_rising = _p_rising(trace)
        mom_ok = _bool(trace.get("mom_ok"))
        final_reason = _reason_type(trace.get("final_hold_reason") or trace.get("reason"))
        if mom_ok is True and p_rising is False:
            mom_ok_p_not_rising += 1
        if p_rising is True and final_reason not in {"", "P_RISING_FALSE", "HOLD_NO_ENTRY_SIGNAL"}:
            p_rising_then_blocked += 1

        tape = _safe_float(trace.get("tape_progress_pct", trace.get("tape_progress", trace.get("p_progress"))))
        fee = _safe_float(trace.get("fee_edge_pct", trace.get("fee_edge")))
        spread = _safe_float(trace.get("spread_pct", trace.get("spread")))
        if tape is not None:
            tape_progress.append(tape)
        if fee is not None:
            fee_edge.append(fee)
        if tape is not None and fee is not None and fee > 0:
            tape_to_fee_ratios.append(tape / fee)
        if spread is not None:
            spreads.append(spread)

        if final_reason == "NEAR_PEAK":
            distance = _safe_float(trace.get("dist"))
            if distance is not None:
                near_peak_distances.append(distance)
            if mom_ok is True:
                near_peak_mom_ok += 1
            spread_ok = _bool(trace.get("spread_ok"))
            if spread_ok is None:
                max_spread = _safe_float(trace.get("max_spread_pct"))
                spread_ok = spread is not None and max_spread is not None and spread <= max_spread
            if spread_ok:
                near_peak_spread_ok += 1
            if mom_ok is True and spread_ok and p_rising is True:
                near_peak_candidate_otherwise += 1

    runtime = _runtime_snapshot(runtime_dir)
    if str(runtime["selector_state"].get("last_reason") or "") == "NO_ELIGIBLE_POSITIVE":
        selector_no_eligible = max(selector_no_eligible, 1)
    conclusions = []
    if not order_events:
        conclusions.append("NO_ORDER_ATTEMPT")
    quote_free = runtime["quote_free"]
    min_notional = runtime["min_notional"]
    if quote_free is not None and (min_notional is None or quote_free < min_notional):
        conclusions.append("NO_QUOTE_BALANCE")
    if not order_events and (events.get("DECIDE_HOLD", 0) or traces):
        conclusions.append("ENTRY_GATES_BLOCKING")
    if selector_no_eligible:
        conclusions.append("SELECTOR_NO_ELIGIBLE")
    if api_order_errors:
        conclusions.append("API_ORDER_BLOCKED")

    return {
        "csv_rows": len(csv_rows),
        "events": dict(events.most_common()),
        "reasons": dict(reasons.most_common()),
        "events_by_symbol": {symbol: dict(values.most_common()) for symbol, values in sorted(event_by_symbol.items())},
        "reasons_by_symbol": {symbol: dict(values.most_common()) for symbol, values in sorted(reason_by_symbol.items())},
        "order_events": order_events,
        "entry_gate_passes": len(entry_gate_passes),
        "runtime": runtime,
        "top_blockages": reasons.most_common(20),
        "gate_analysis": {
            "mom_ok_p_rising_false": mom_ok_p_not_rising,
            "p_rising_then_blocked": p_rising_then_blocked,
            "tape_progress_pct": _distribution(tape_progress),
            "fee_edge_pct": _distribution(fee_edge),
            "tape_to_fee_ratio": _distribution(tape_to_fee_ratios),
            "spread_pct": _distribution(spreads),
            "near_peak": {
                "count": len(near_peak_distances),
                "distance_pct": _distribution(near_peak_distances),
                "mom_ok": near_peak_mom_ok,
                "spread_ok": near_peak_spread_ok,
                "candidate_otherwise": near_peak_candidate_otherwise,
            },
            "entry_stages": dict(entry_stages.most_common()),
        },
        "selector_no_eligible": selector_no_eligible,
        "api_order_errors": api_order_errors[:20],
        "conclusions": conclusions,
    }


def _print_counter(title: str, values: dict[str, int], limit: int = 20) -> None:
    print(f"{title}:")
    if not values:
        print("  none")
        return
    for key, count in list(values.items())[:limit]:
        print(f"  {key}: {count}")


def print_report(report: dict) -> None:
    runtime = report["runtime"]
    print("NO_TRADE_AUDIT")
    print(f"csv_rows: {report['csv_rows']}")
    _print_counter("events", report["events"])
    _print_counter("top_blockages", dict(report["top_blockages"]))
    print("runtime:")
    print(f"  quote_asset: {runtime['quote_asset']}")
    print(f"  quote_free: {runtime['quote_free']}")
    print(f"  min_notional: {runtime['min_notional']}")
    print(f"  sizing_cap: {runtime['sizing_cap']}")
    print(f"  can_buy: {runtime['can_buy']}")
    print(f"  blocking_reason: {runtime['blocking_reason'] or 'none'}")
    print(f"  position_present: {runtime['position_present']}")
    print(f"  position: {runtime['position']}")
    print(f"  selector_state: {runtime['selector_state']}")
    print(f"order_attempts: {sum(report['order_events'].values())}")
    _print_counter("order_events", report["order_events"])
    print(f"entry_gate_passes: {report['entry_gate_passes']}")
    print("gate_analysis:")
    for key, value in report["gate_analysis"].items():
        print(f"  {key}: {value}")
    print(f"selector_no_eligible: {report['selector_no_eligible']}")
    print(f"api_order_errors: {len(report['api_order_errors'])}")
    print(f"conclusions: {', '.join(report['conclusions']) or 'NONE'}")
    print("TUNING_PROPOSE_NON_APPLIQUE:")
    print("  No threshold changed. HOLD_EDGE and NEAR_PEAK require the new trace on a funded run before any tuning proposal.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", default="data/logs", type=Path)
    parser.add_argument("--runtime", default="data/runtime", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    report = audit_paths(args.logs, args.runtime)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
