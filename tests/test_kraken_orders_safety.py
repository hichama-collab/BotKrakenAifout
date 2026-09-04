import pytest

from types import SimpleNamespace

from execution.orders import OrderStateUnknown, openOrders, placeLimit, waitFillOrCancel
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


def test_open_orders_returns_only_the_active_kraken_pair():
    class OpenOrdersClient:
        def resolve_pair(self, symbol):
            return SimpleNamespace(pair_id="ADAUSDC", symbol="ADAUSDC", ws_symbol="ADA/USDC", quote_asset="USDC")

        def post(self, path, params):
            assert path == "/0/private/OpenOrders"
            return {
                "open": {
                    "ADA": {"status": "open", "descr": {"pair": "ADA/USDC", "type": "buy"}},
                    "IDOS": {"status": "open", "descr": {"pair": "IDOS/USD", "type": "sell"}},
                }
            }

    rows = openOrders(OpenOrdersClient(), "ADAUSDC")

    assert [row["orderId"] for row in rows] == ["ADA"]
    assert rows[0]["symbol"] == "ADAUSDC"
