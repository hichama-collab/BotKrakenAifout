import importlib
import json
import sys


def test_dashboard_services_names(monkeypatch, tmp_path):
    monkeypatch.setenv("DASH_PASS", "secret")
    monkeypatch.setenv("FLASK_SECRET_KEY", "secret-key")
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    app = importlib.import_module("dashboard.app")

    assert "kraken-aifout-bot.service" in app.SERVICES
    assert "kraken-token-profile-selector.timer" in app.SERVICES


def test_dashboard_no_legacy_exchange_url():
    source = open("dashboard/app.py", encoding="utf-8").read()
    assert "api." + "bina" + "nce.com" not in source
    assert "bina" + "nce-aifout-bot" not in source


def test_dashboard_uses_clickable_kraken_trade_links():
    app_js = open("dashboard/static/app.js", encoding="utf-8").read()
    live_template = open("dashboard/templates/dashboard.html", encoding="utf-8").read()
    services_template = open("dashboard/templates/services.html", encoding="utf-8").read()
    radar_template = open("dashboard/templates/radar.html", encoding="utf-8").read()

    assert "https://pro.kraken.com/app/trade/" in app_js
    assert "/app/trade/spot/" not in app_js
    assert "pair.toLowerCase()" in app_js
    assert ':href="krakenUrl(activeToken)"' in live_template
    assert ':href="krakenUrl(control.symbol)"' in services_template
    assert radar_template.count(':href="krakenUrl(') >= 4


def test_snapshot_route_with_mocked_files(monkeypatch, tmp_path):
    monkeypatch.setenv("DASH_PASS", "secret")
    monkeypatch.setenv("FLASK_SECRET_KEY", "secret-key")
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    app = importlib.import_module("dashboard.app")
    client = app.app.test_client()
    res = client.get("/api/snapshot", headers={"Authorization": "Basic YWRtaW46c2VjcmV0"})
    assert res.status_code == 200


def test_snapshot_exposes_live_quote_balance_blocking_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("DASH_PASS", "secret")
    monkeypatch.setenv("FLASK_SECRET_KEY", "secret-key")
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path / "data" / "runtime"))
    monkeypatch.setenv("BOT_LOG_DIR", str(tmp_path / "data" / "logs"))
    monkeypatch.setenv("BOT_SERVICE_ENV", str(tmp_path / ".service.env"))
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    (tmp_path / ".service.env").write_text("SYMBOL=XRPUSDC\nPROFILE=strict\nDRY_RUN=0\n", encoding="utf-8")
    (tmp_path / "data" / "runtime" / "wallet.json").write_text(
        json.dumps({"balances": [{"asset": "USDC", "free": "0.00949213", "locked": "0"}]}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "runtime" / "bot_status.json").write_text(
        json.dumps({
            "symbol": "XRPUSDC",
            "state": "IDLE",
            "quote_asset": "USDC",
            "quote_free": 0.00949213,
            "min_notional": 5.0,
            "sizing_cap": 0.00949213,
            "can_buy": False,
            "blocking_reason": "LIVE_NO_QUOTE_BALANCE",
            "last_entry_gate_trace": {"final_hold_reason": "P_RISING_FALSE"},
        }),
        encoding="utf-8",
    )
    (tmp_path / "data" / "runtime" / "selector_state.json").write_text(
        json.dumps({"last_reason": "NO_ELIGIBLE_POSITIVE", "symbol": "XRPUSDC"}),
        encoding="utf-8",
    )
    sys.modules.pop("dashboard.app", None)
    app = importlib.import_module("dashboard.app")
    client = app.app.test_client()

    res = client.get("/api/snapshot", headers={"Authorization": "Basic YWRtaW46c2VjcmV0"})

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["monitor"]["metrics"]["blocking_reason"] == "LIVE_NO_QUOTE_BALANCE"
    assert payload["monitor"]["decision"]["reason"] == "LIVE_NO_QUOTE_BALANCE"
    assert payload["wallet"]["quote_free"] == 0.00949213
    assert payload["selector_state"]["last_reason"] == "NO_ELIGIBLE_POSITIVE"
