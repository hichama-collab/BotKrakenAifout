import pytest

from exchange.symbols import SymbolMapper, SymbolNotFound


ASSET_PAIRS = {
    "XBTUSDC": {
        "altname": "XBTUSDC",
        "wsname": "XBT/USDC",
        "base": "XXBT",
        "quote": "USDC",
        "pair_decimals": 1,
        "lot_decimals": 8,
        "ordermin": "0.00005",
        "costmin": "5",
    },
    "ETHUSDC": {
        "altname": "ETHUSDC",
        "wsname": "ETH/USDC",
        "base": "XETH",
        "quote": "USDC",
        "pair_decimals": 2,
        "lot_decimals": 8,
        "ordermin": "0.001",
        "costmin": "5",
    },
}


class FakeClient:
    def get(self, path, params=None, signed=False):
        assert path == "/0/public/AssetPairs"
        return ASSET_PAIRS


@pytest.mark.parametrize("raw", ["BTCUSDC", "BTC/USDC", "XBT/USDC"])
def test_symbol_mapper_resolves_btc_aliases(raw):
    meta = SymbolMapper(FakeClient(), "USDC").resolve_pair(raw)

    assert meta.symbol == "BTCUSDC"
    assert meta.pair_id == "XBTUSDC"
    assert meta.ws_symbol == "XBT/USDC"
    assert str(meta.tick) == "0.1"


def test_symbol_mapper_resolves_eth():
    meta = SymbolMapper(FakeClient(), "USDC").resolve_pair("ETHUSDC")

    assert meta.symbol == "ETHUSDC"
    assert meta.base_asset == "ETH"


def test_symbol_mapper_pair_not_found_is_clear():
    with pytest.raises(SymbolNotFound, match="not found"):
        SymbolMapper(FakeClient(), "USDC").resolve_pair("NOPE/USDC")
