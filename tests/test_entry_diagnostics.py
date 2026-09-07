from types import SimpleNamespace

from core.entry_diagnostics import entry_gate_requirements, format_entry_gate_trace, p_tape_snapshot, quote_sizing_snapshot
from main import compute_entry_plan_viability, momentum_ok


def test_live_quote_balance_block_is_explicit():
    snapshot = quote_sizing_snapshot(
        quote_free=0.00949213,
        quote_asset="USDC",
        min_notional=5.0,
        cap=1000.0,
        fee_buffer_pct=0.0015,
        dry_run=False,
        has_position=False,
    )

    assert snapshot["can_buy"] is False
    assert snapshot["blocking_reason"] == "LIVE_NO_QUOTE_BALANCE"
    assert snapshot["sizing_cap"] < snapshot["min_notional"]


def test_p_tape_snapshot_matches_existing_non_decreasing_gate():
    blocked = p_tape_snapshot(100.0, 100.1, 100.05, 99.9)
    accepted = p_tape_snapshot(100.2, 100.1, 100.0, 99.9)

    assert blocked["has_p_window"] is True
    assert blocked["p_rising"] is False
    assert accepted["p_rising"] is True
    assert accepted["strict_up_moves"] == 3


def test_entry_gate_requirements_make_default_range_visible():
    requirements = entry_gate_requirements(
        spread=0.0004930966469427727,
        cfg=SimpleNamespace(
            momMaxPct=1.0,
            minRangeEntryPct=0.0035,
            minRangeVsSpread=4.0,
            entryMinTapeProgressPct=0.00025,
            entryMinTapeProgressVsSpread=1.0,
            entryMinStrictUps=2,
            entryHardMinUpRatio=0.55,
        ),
    )

    assert requirements["required_range_pct"] == 0.0035
    assert requirements["required_tape_progress_pct"] == 0.0004930966469427727
    trace = format_entry_gate_trace({
        "entry_stage": "RANGE",
        "required_range_pct": requirements["required_range_pct"] * 100.0,
        "rsi": 58.2,
    })
    assert "entry_stage=RANGE" in trace
    assert "required_range_pct=0.35" in trace
    assert "rsi=58.2" in trace


def test_adjacent_tick_p_path_can_reach_a_profitable_trade_plan():
    p_tape = p_tape_snapshot(100.03, 100.02, 100.01, 100.00)
    mom_ok, _, up_ratio, _ = momentum_ok(
        [(0.0, 99.93), (6.0, 100.00), (12.0, 100.03)],
        window_sec=12.0,
        min_pct=0.0008,
        min_up_ratio=0.55,
        range_min_pct=0.0,
        range_relax_pct=1.0,
        range_relax_up_ratio=1.0,
        allow_warmup_entry=False,
    )
    sizing = quote_sizing_snapshot(
        quote_free=20.0,
        quote_asset="USDC",
        min_notional=5.0,
        cap=1000.0,
        fee_buffer_pct=0.001,
        dry_run=False,
        has_position=False,
    )
    plan = compute_entry_plan_viability(
        0.0002,
        SimpleNamespace(
            riskPct=0.0065,
            tpPct=0.008,
            tpMinPct=0.004,
            defaultFeeRate=0.0035,
            minProfitBufferPct=0.001,
            tpNetMarginPct=0.003,
        ),
        fee_rate=0.0035,
    )

    assert p_tape["p_rising"] is True
    assert round(p_tape["tape_progress_pct"], 8) == 0.03
    assert (p_tape["tape_progress_pct"] / 100.0) >= max(0.00025, 0.0002)
    assert (p_tape["tape_progress_pct"] / 100.0) < ((2 * 0.0035) + 0.0002 + 0.001) * 1.25
    assert mom_ok is True
    assert up_ratio == 1.0
    assert sizing["can_buy"] is True
    assert plan["plan_viable"] is True
