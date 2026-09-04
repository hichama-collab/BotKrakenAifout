import pytest
from execution.fee_model import FeeModel

def test_net_pnl_loss_after_fees():
    fm = FeeModel(fee_rate=0.001)
    # Buy 2.71 @ 3.651, Sell 2.70 @ 3.657
    result = fm.compute_net_pnl(3.651, 2.71, 3.657, 2.70)
    assert result["net_pnl"] < 0, "Trade with small move should be negative after fees"
    assert abs(result["net_pnl"] - (-0.0401)) < 0.005

def test_net_pnl_clear_winner():
    fm = FeeModel(fee_rate=0.001)
    # Buy 100 @ 1.0, Sell 100 @ 1.005 → gross = 0.5, fees = ~0.2005, net ≈ 0.30
    result = fm.compute_net_pnl(1.0, 100.0, 1.005, 100.0)
    assert result["net_pnl"] > 0.28
    assert result["gross_pnl"] == pytest.approx(0.5, abs=0.001)

def test_kraken_ignores_bnb_discount():
    fm = FeeModel(fee_rate=0.001, use_bnb=True)
    assert fm.fee_rate == pytest.approx(0.001, abs=1e-6)

def test_breakeven_pct():
    fm = FeeModel(fee_rate=0.001)
    be = fm.min_move_to_profit(spread_pct=0.0003)
    assert be == pytest.approx(0.0023, abs=1e-4)

def test_round_trip_cost():
    fm = FeeModel(fee_rate=0.001)
    cost = fm.estimate_round_trip_cost(10.0)
    assert cost == pytest.approx(0.02, abs=1e-6)


def test_uses_kraken_taker_rate_when_limit_order_is_not_post_only():
    fm = FeeModel(
        fee_rate=0.001,
        fee_rate_resolver=lambda symbol: {"taker": 0.0035, "maker": 0.002, "source": "kraken_trade_volume"},
    )

    info = fm.refresh_fee_rate("ADAUSDC")

    assert info["source"] == "kraken_trade_volume"
    assert fm.fee_rate == pytest.approx(0.0035)


def test_actual_fill_fees_override_the_schedule_estimate_for_pnl():
    fm = FeeModel(fee_rate=0.0035)

    result = fm.compute_net_pnl(
        1.0,
        10.0,
        1.1,
        10.0,
        actual_fees_buy=0.01,
        actual_fees_sell=0.02,
    )

    assert result["fees_buy"] == pytest.approx(0.01)
    assert result["fees_sell"] == pytest.approx(0.02)
    assert result["net_pnl"] == pytest.approx(0.97)
