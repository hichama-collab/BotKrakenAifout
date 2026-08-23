import csv
import json

from core.logging import TRADE_CSV_FIELDNAMES
from tools.no_trade_audit import audit_paths


def test_no_trade_audit_reports_balance_and_p_gate(tmp_path):
    logs = tmp_path / "logs" / "live" / "main"
    runtime = tmp_path / "runtime"
    logs.mkdir(parents=True)
    runtime.mkdir()

    csv_path = logs / "XRPUSDC_20260823_trades.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow({
            "ts_utc": "2026-08-23T00:00:00Z",
            "symbol": "XRPUSDC",
            "event": "DECIDE_HOLD",
            "reason": "HOLD_NO_ENTRY_SIGNAL",
        })

    (logs / "XRPUSDC_20260823_trades.log").write_text(
        "[2026-08-23 00:00:00+0000] ENTRY_GATE_TRACE "
        "symbol=XRPUSDC mom_ok=1 p1=1.47035 p2=1.47039 p3=1.47027 p4=1.47004 "
        "p_rising=0 tape_progress_pct=0.0211 fee_edge_pct=0.4010 spread_pct=0.0340 "
        "final_hold_reason=P_RISING_FALSE\n"
        "[2026-08-23 00:00:00+0000] DECIDE_HOLD reason=HOLD_NO_ENTRY_SIGNAL\n",
        encoding="utf-8",
    )
    (runtime / "wallet.json").write_text(
        json.dumps({"balances": [{"asset": "USDC", "free": "0.00949213", "locked": "0"}]}),
        encoding="utf-8",
    )
    (runtime / "position.json").write_text(json.dumps({"qty": 0, "reason": "wallet_empty"}), encoding="utf-8")
    (runtime / "selector_state.json").write_text(
        json.dumps({"last_reason": "NO_ELIGIBLE_POSITIVE", "symbol": "XRPUSDC"}),
        encoding="utf-8",
    )
    (runtime / "bot_status.json").write_text(
        json.dumps({"quote_asset": "USDC", "quote_free": 0.00949213, "min_notional": 5.0}),
        encoding="utf-8",
    )

    report = audit_paths(logs.parent.parent, runtime)

    assert report["csv_rows"] == 1
    assert report["events"] == {"DECIDE_HOLD": 1}
    assert report["order_events"] == {}
    assert report["gate_analysis"]["mom_ok_p_rising_false"] == 1
    assert report["gate_analysis"]["tape_to_fee_ratio"]["mean"] == round(0.0211 / 0.4010, 6)
    assert "NO_ORDER_ATTEMPT" in report["conclusions"]
    assert "NO_QUOTE_BALANCE" in report["conclusions"]
    assert "ENTRY_GATES_BLOCKING" in report["conclusions"]
    assert "SELECTOR_NO_ELIGIBLE" in report["conclusions"]
