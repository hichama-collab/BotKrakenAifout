import json
from types import SimpleNamespace

from exchange.stream import Stream


class FakeMapper:
    def resolve_pair(self, symbol):
        return SimpleNamespace(ws_symbol="XBT/USDC")


def test_stream_uses_kraken_ws_v2_btc_symbol():
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=FakeMapper())

    assert stream._ws_symbol() == "BTC/USDC"


def test_stream_fallback_uses_btc_not_xbt_for_ws_v2():
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=None)

    assert stream._ws_symbol() == "BTC/USDC"


class FakeXdgMapper:
    def resolve_pair(self, symbol):
        return SimpleNamespace(ws_symbol="XDG/USDC")


def test_stream_uses_kraken_ws_v2_doge_symbol():
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "XDGUSDC", mapper=FakeXdgMapper())

    assert stream._ws_symbol() == "DOGE/USDC"


def test_stream_subscribes_to_kraken_book_by_default():
    sent = []
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=FakeMapper())
    stream.on_open(SimpleNamespace(send=sent.append))

    assert json.loads(sent[0]) == {
        "method": "subscribe",
        "params": {"channel": "book", "symbol": ["BTC/USDC"], "depth": 10},
    }


def test_stream_book_snapshot_and_update_keep_best_bid_ask():
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=FakeMapper())
    stream.on_message(None, json.dumps({
        "channel": "book",
        "type": "snapshot",
        "data": [{
            "bids": [{"price": 100.0, "qty": 1.0}, {"price": 99.0, "qty": 2.0}],
            "asks": [{"price": 101.0, "qty": 1.0}, {"price": 102.0, "qty": 2.0}],
        }],
    }))
    assert stream.snapshot()[:2] == (100.0, 101.0)

    stream.on_message(None, json.dumps({
        "channel": "book",
        "type": "update",
        "data": [{
            "bids": [{"price": 100.0, "qty": 0.0}, {"price": 100.5, "qty": 3.0}],
            "asks": [{"price": 101.0, "qty": 0.0}, {"price": 100.8, "qty": 1.0}],
        }],
    }))
    bid, ask, _ts, seq = stream.snapshot()
    assert (bid, ask) == (100.5, 100.8)
    assert seq == 2
