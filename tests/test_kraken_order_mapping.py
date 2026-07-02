from execution.orders import map_kraken_order


def test_maps_open_to_new():
    row = {"status": "open", "vol_exec": "0", "descr": {"type": "buy"}}
    assert map_kraken_order("BTCUSDC", "O1", row)["status"] == "NEW"


def test_maps_closed_filled():
    row = {"status": "closed", "vol_exec": "0.1", "price": "10", "descr": {"type": "buy"}}
    mapped = map_kraken_order("BTCUSDC", "O1", row)
    assert mapped["status"] == "FILLED"
    assert mapped["executedQty"] == "0.1"
    assert mapped["cummulativeQuoteQty"] == "1.0"


def test_maps_canceled_partial():
    row = {"status": "canceled", "vol_exec": "0.1", "price": "10", "descr": {"type": "sell"}}
    mapped = map_kraken_order("BTCUSDC", "O1", row)
    assert mapped["status"] == "CANCELED"
    assert mapped["executedQty"] == "0.1"


def test_maps_expired():
    assert map_kraken_order("BTCUSDC", "O1", {"status": "expired"})["status"] == "EXPIRED"


def test_maps_error_rejected():
    assert map_kraken_order("BTCUSDC", "O1", {"status": "error"})["status"] == "REJECTED"
