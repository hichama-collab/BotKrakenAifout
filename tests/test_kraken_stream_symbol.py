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
