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


def test_stream_subscribes_to_kraken_ticker_bbo_by_default():
    sent = []
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=FakeMapper())
    stream.on_open(SimpleNamespace(send=sent.append))

    assert json.loads(sent[0]) == {
        "method": "subscribe",
        "params": {"channel": "ticker", "symbol": ["BTC/USDC"], "event_trigger": "bbo"},
    }


def test_stream_ticker_snapshot_updates_best_bid_ask():
    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid"), "BTCUSDC", mapper=FakeMapper())
    stream.on_message(None, json.dumps({
        "channel": "ticker",
        "type": "snapshot",
        "data": [{"bid": 100.0, "ask": 101.0}],
    }))

    assert stream.snapshot()[:2] == (100.0, 101.0)


def test_stale_stream_requests_reconnect_without_using_live_rest_fallback():
    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid", dryRun=False, wsTransportStaleSec=0.01), "BTCUSDC", mapper=FakeMapper())
    fake_socket = FakeSocket()
    stream._ws = fake_socket
    stream.bestBid = 100.0
    stream.bestAsk = 101.0
    stream.lastUpdate = 1.0
    stream.lastTransportUpdate = 1.0

    assert stream.snapshot() == (0.0, 0.0, 0.0, 0)
    assert fake_socket.closed is True


def test_heartbeat_keeps_unchanged_bbo_usable_without_creating_a_tick():
    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    stream = Stream(SimpleNamespace(wsUrl="wss://example.invalid", dryRun=False, wsTransportStaleSec=0.01), "BTCUSDC", mapper=FakeMapper())
    fake_socket = FakeSocket()
    stream._ws = fake_socket
    stream.bestBid = 100.0
    stream.bestAsk = 101.0
    stream.lastUpdate = 1.0
    stream.tickSeq = 7

    stream.on_message(None, json.dumps({"channel": "heartbeat"}))

    bid, ask, tick_ts, tick_seq = stream.snapshot()
    assert (bid, ask, tick_ts, tick_seq) == (100.0, 101.0, 1.0, 7)
    assert fake_socket.closed is False


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
