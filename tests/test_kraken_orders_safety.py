import pytest

from execution.orders import OrderStateUnknown, placeLimit, waitFillOrCancel
from tests.test_orders_safety import FakeKraken


def test_limit_only_no_market_margin_or_leverage():
    bx = FakeKraken()
    placeLimit(bx, "BTCUSDC", "SELL", 0.01, 65000, 0.000001, 0.01)

    params = bx.posts[0][1]
    assert params["ordertype"] == "limit"
    assert params["timeinforce"] == "GTC"
    assert "leverage" not in params
    assert params["type"] == "sell"


def test_order_state_unknown_when_final_state_not_proven():
    with pytest.raises(OrderStateUnknown):
        waitFillOrCancel(FakeKraken(), "BTCUSDC", "O123", 0, 0, maxRestRetries=1, restBackoffSec=0)
