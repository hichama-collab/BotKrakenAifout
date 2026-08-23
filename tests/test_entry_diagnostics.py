from core.entry_diagnostics import p_tape_snapshot, quote_sizing_snapshot


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
