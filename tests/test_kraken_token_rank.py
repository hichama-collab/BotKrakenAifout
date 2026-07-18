import importlib
import time


def test_token_rank_uses_closed_kraken_1h_candles(monkeypatch):
    rank = importlib.import_module("TokenRank1h")
    now = int(time.time())
    pair_id = "ADAUSDC"

    def fake_get(path, params=None, timeout=10.0, retries=3):
        assert path == "/0/public/OHLC"
        assert params["pair"] == pair_id
        return {
            pair_id: [
                [now - 10800, "1.00", "1.00", "1.00", "1.00", "0", "0"],
                [now - 7200, "1.00", "1.00", "1.00", "1.10", "0", "0"],
                [now - 3600, "1.10", "1.10", "1.10", "1.20", "0", "0"],
                [now, "1.20", "1.20", "1.20", "9.99", "0", "0"],
            ],
            "last": 123,
        }

    monkeypatch.setattr(rank, "http_get_json", fake_get)

    assert round(rank.get_1h_change_pct(pair_id), 4) == round((1.20 / 1.10 - 1.0) * 100.0, 4)


def test_token_rank_symbol_mapping_from_asset_pairs(monkeypatch):
    rank = importlib.import_module("TokenRank1h")

    def fake_get(path, params=None, timeout=10.0, retries=3):
        assert path == "/0/public/AssetPairs"
        return {
            "XXBTZUSD": {"base": "XXBT", "quote": "ZUSD", "status": "online"},
            "XETHUSDC": {"base": "XETH", "quote": "USDC", "status": "online"},
            "ADAUSDC": {"base": "ADA", "quote": "USDC", "status": "online"},
            "OLDUSDC": {"base": "OLD", "quote": "USDC", "status": "cancel_only"},
        }

    monkeypatch.setattr(rank, "http_get_json", fake_get)

    assert rank.get_spot_symbols("USDC") == [("ETHUSDC", "XETHUSDC"), ("ADAUSDC", "ADAUSDC")]
