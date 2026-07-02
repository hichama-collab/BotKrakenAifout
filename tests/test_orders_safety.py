from types import SimpleNamespace

import pytest

from execution.orders import OrderStateUnknown, order_fee_summary, placeLimit, waitFillOrCancel


class FakeKraken:
    def __init__(self):
        self.posts = []

    def resolve_pair(self, symbol):
        return SimpleNamespace(pair_id="XBTUSDC")

    def post(self, path, params):
        self.posts.append((path, params))
        if path == "/0/private/AddOrder":
            return {"txid": ["O123"]}
        if path == "/0/private/QueryOrders":
            raise RuntimeError("status unavailable")
        if path == "/0/private/OpenOrders":
            return {"open": {"O123": {"status": "open", "vol_exec": "0"}}}
        if path == "/0/private/CancelOrder":
            raise RuntimeError("cancel unavailable")
        raise AssertionError(path)


class FakeOpenAfterCancel(FakeKraken):
    def post(self, path, params):
        if path == "/0/private/QueryOrders":
            return {"123": {"status": "open", "vol_exec": "0"}}
        if path == "/0/private/OpenOrders":
            return {"open": {"123": {"status": "open", "vol_exec": "0"}}}
        if path == "/0/private/CancelOrder":
            return {"count": 1}
        raise AssertionError(path)


def test_place_limit_sets_client_order_id_and_limit_only():
    bx = FakeKraken()

    order = placeLimit(bx, "BTCUSDC", "BUY", 0.001, 65000.0, 0.000001, 0.01)

    params = bx.posts[0][1]
    assert order["clientOrderId"].startswith("aifout_buy_btcusdc_")
    assert params["cl_ord_id"] == order["clientOrderId"]
    assert params["ordertype"] == "limit"
    assert params["type"] == "buy"
    assert params["timeinforce"] == "GTC"
    assert "leverage" not in params
    assert "oflags" not in params


def test_place_limit_rejects_non_buy_sell():
    with pytest.raises(ValueError):
        placeLimit(FakeKraken(), "BTCUSDC", "MARKET", 0.001, 65000.0, 0.000001, 0.01)


def test_wait_fill_or_cancel_raises_on_unknown_final_state():
    bx = FakeKraken()

    with pytest.raises(OrderStateUnknown, match="ORDER_STATE_UNKNOWN"):
        waitFillOrCancel(
            bx,
            "BTCUSDC",
            "O123",
            ttl=0.0,
            poll=0.0,
            side="BUY",
            qty=0.001,
            price=65000.0,
            maxRestRetries=1,
            restBackoffSec=0.0,
        )


def test_wait_fill_or_cancel_raises_if_order_still_open_after_cancel():
    with pytest.raises(OrderStateUnknown, match="exchange_status=NEW"):
        waitFillOrCancel(
            FakeOpenAfterCancel(),
            "BTCUSDC",
            "123",
            ttl=0.0,
            poll=0.0,
            side="BUY",
            qty=0.001,
            price=65000.0,
            maxRestRetries=1,
            restBackoffSec=0.0,
        )


def test_order_fee_summary_uses_exchange_fills_when_available():
    order = {
        "executedQty": "2",
        "cummulativeQuoteQty": "20",
        "fills": [
            {"commission": "0.001", "commissionAsset": "BTC"},
            {"commission": "0.002", "commissionAsset": "BTC"},
        ],
    }

    fees = order_fee_summary(order)

    assert fees["fee_source"] == "exchange"
    assert fees["fee"] == pytest.approx(0.003)
    assert fees["commission_asset"] == "BTC"
    assert fees["executed_qty"] == 2.0
    assert fees["quote_qty"] == 20.0


def test_order_fee_summary_marks_estimated_without_fills():
    fees = order_fee_summary({"executedQty": "1", "cummulativeQuoteQty": "10"})

    assert fees["fee_source"] == "estimated"
    assert fees["commission_asset"] == ""
