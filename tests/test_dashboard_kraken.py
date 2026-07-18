import importlib


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
