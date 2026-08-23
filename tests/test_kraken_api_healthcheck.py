from types import SimpleNamespace

from tools.kraken_api_healthcheck import run_healthcheck


class FakeKraken:
    def get(self, path):
        assert path in {"/0/public/Time", "/0/public/AssetPairs"}
        return {"unixtime": 1000} if path.endswith("Time") else {}

    def account_balances(self):
        return {"balances": [{"asset": "USDC", "free": "12.5"}]}

    def post(self, path, params):
        assert path in {
            "/0/private/OpenOrders",
            "/0/private/TradesHistory",
            "/0/private/GetApiKeyInfo",
        }
        if path.endswith("GetApiKeyInfo"):
            return {"permissions": ["modify-trades"]}
        return {}

    def resolve_pair(self, symbol):
        assert symbol == "XRPUSDC"
        return SimpleNamespace(min_notional=0.5)


def test_healthcheck_uses_read_only_endpoints_and_reports_quote_balance(monkeypatch):
    monkeypatch.setenv("SYMBOL", "XRPUSDC")
    result = run_healthcheck(
        FakeKraken(),
        SimpleNamespace(quoteAsset="USDC", minOrderNotionalUsdc=0.0, maxUsdcPerTrade=50.0, feeBufPct=0.0015),
        now=lambda: 1002,
    )

    assert result["ok"] is True
    assert "API_OK" in result["lines"]
    assert "BALANCE_OK" in result["lines"]
    assert "OPEN_ORDERS_OK" in result["lines"]
    assert "TRADES_HISTORY_OK" in result["lines"]
    assert "API_KEY_INFO_OK" in result["lines"]
    assert "TRADING_PERMISSION_GRANTED" in result["lines"]
    assert "QUOTE_BALANCE_OK" in result["lines"]
    assert "NO_LIVE_ORDER_SUBMITTED" in result["lines"]
