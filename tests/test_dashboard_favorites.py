import base64
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("flask")

from services.favorites_analysis import (
    build_dashboard_favorite_detail,
    build_dashboard_favorites_analysis,
    build_favorite_detail,
    build_favorites_analysis,
)
from services.token_radar_store import add_favorite, insert_snapshots


class _FavoriteKrakenStub:
    """Public API fixture: no test reaches Kraken."""

    def __init__(self, *args, **kwargs):
        pass

    def get(self, path, params=None):
        if path == "/0/public/AssetPairs":
            return {
                "HNTUSD": {
                    "base": "HNT",
                    "quote": "ZUSD",
                    "altname": "HNTUSD",
                    "wsname": "HNT/USD",
                    "pair_decimals": 5,
                    "lot_decimals": 8,
                    "ordermin": "0.1",
                }
            }
        if path == "/0/public/Ticker":
            return {
                "HNTUSD": {
                    "a": ["0.73700"],
                    "b": ["0.73600"],
                    "c": ["0.73650"],
                    "v": ["1000", "2500000"],
                    "p": ["0.73", "0.72"],
                    "t": [100, 6500],
                    "l": ["0.60", "0.59"],
                    "h": ["0.75", "0.88"],
                }
            }
        raise AssertionError(f"Unexpected public endpoint: {path}")


@pytest.fixture(autouse=True)
def _reset_dashboard_module():
    """Keep environment-specific Flask imports isolated from sibling tests."""
    sys.modules.pop("dashboard.app", None)
    yield
    sys.modules.pop("dashboard.app", None)


def _auth_header(user="admin", password="test-password"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _snapshot(symbol: str, created_at: datetime, price: float, **overrides) -> dict:
    row = {
        "created_at": created_at.isoformat(),
        "symbol": symbol,
        "price": price,
        "bid": price * 0.9998,
        "ask": price * 1.0002,
        "spread_pct": 0.0004,
        "quote_volume_24h": 2_000_000,
        "change_5m_pct": 0.005,
        "change_15m_pct": 0.008,
        "change_1h_pct": 0.018,
        "change_4h_pct": 0.04,
        "change_24h_pct": 0.06,
        "change_7d_pct": 0.11,
        "high_24h": price * 1.03,
        "low_24h": price * 0.95,
        "distance_high_24h_pct": -0.03,
        "distance_low_24h_pct": 0.05,
        "volatility_pct": 0.02,
        "amplitude_pct": 0.02,
        "reliability_score": 82,
        "consistency_score": 82,
        "movement_risk_score": 18,
        "noise_score": 18,
        "risk_level": "LOW",
        "risk_label": "Fiable",
        "risk_reason": "mouvement regulier",
        "score": 78,
        "global_score": 78,
    }
    row.update(overrides)
    return row


def _seed_favorite(db_path, now: datetime, symbol="SOLUSDC"):
    points = [
        (now - timedelta(days=6, hours=23), 90.0),
        (now - timedelta(days=3), 94.0),
        (now - timedelta(hours=23), 97.0),
        (now - timedelta(hours=3), 99.0),
        (now - timedelta(minutes=50), 101.0),
        (now - timedelta(minutes=14), 103.0),
        (now - timedelta(minutes=4), 105.0),
        (now, 106.0),
    ]
    insert_snapshots([_snapshot(symbol, created_at, price) for created_at, price in points], db_path)
    add_favorite(symbol, note="suivi", db_path=db_path)


def _write_dashboard_watchlist(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """settings:
  snapshot_interval_sec: 60
  retention_days: 14
  request_timeout_sec: 1
  history_limit: 300
favorites:
  - HNT/USD
""",
        encoding="utf-8",
    )


def _import_dashboard_app(tmp_path, monkeypatch):
    db_path = tmp_path / "token_radar.sqlite3"
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("BOT_SERVICE_ENV", str(tmp_path / ".service.env"))
    monkeypatch.setenv("TOKEN_RADAR_DB", str(db_path))
    monkeypatch.setenv("DASH_USER", "admin")
    monkeypatch.setenv("DASH_PASS", "test-password")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    sys.modules.pop("dashboard.app", None)
    return importlib.import_module("dashboard.app"), db_path


def test_favorites_analysis_builds_ranges_and_personal_history(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db_path = tmp_path / "radar.sqlite3"
    _seed_favorite(db_path, now)
    trades = [
        {"symbol": "SOLUSDC", "pnl_usdc": 0.22, "reason": "TP", "ts_utc": (now - timedelta(days=2)).isoformat()},
        {"symbol": "SOL/USDC", "pnl_usdc": -0.08, "reason": "TRAIL", "ts_utc": (now - timedelta(days=1)).isoformat()},
        {"symbol": "SOLUSDC", "pnl_usdc": 0.15, "reason": "TP", "ts_utc": now.isoformat()},
    ]

    payload = build_favorites_analysis(db_path=db_path, trades=trades, now=now)
    item = payload["items"][0]

    assert payload["summary"]["followed_count"] == 1
    assert item["current_price"] == 106.0
    assert item["ranges"]["5m"]["available"] is True
    assert item["ranges"]["5m"]["min"] == 105.0
    assert item["ranges"]["7d"]["max"] == 106.0
    assert item["bot_history"]["complete_trades"] == 3
    assert item["bot_history"]["net_pnl_usdc"] == pytest.approx(0.29)
    assert item["bot_history"]["assessment"] == "historique positif"
    assert item["bot_history"]["frequent_exits"][0] == {"reason": "TP", "count": 2}

    detail = build_favorite_detail("SOL/USDC", db_path=db_path, trades=trades, now=now)
    assert detail["symbol"] == "SOLUSDC"
    assert len(detail["series"]) >= 2


def test_favorites_analysis_handles_missing_data_and_no_trade_history(tmp_path):
    db_path = tmp_path / "radar.sqlite3"
    add_favorite("ETH", note="sans snapshot", db_path=db_path)

    payload = build_favorites_analysis(db_path=db_path, trades=[])
    item = payload["items"][0]

    assert item["verdict"] == "données insuffisantes"
    assert item["ranges"]["1h"]["available"] is False
    assert item["bot_history"]["assessment"] == "no bot trade history"
    assert item["bot_history"]["available"] is False


def test_favorites_uses_ticker_24h_range_when_local_history_is_too_short(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db_path = tmp_path / "radar.sqlite3"
    insert_snapshots([
        _snapshot("XRPUSDC", now - timedelta(minutes=3), 0.50),
        _snapshot("XRPUSDC", now, 0.51, low_24h=0.45, high_24h=0.55),
    ], db_path)
    add_favorite("XRP", db_path=db_path)

    item = build_favorites_analysis(db_path=db_path, now=now)["items"][0]

    assert item["ranges"]["5m"]["available"] is False
    assert item["ranges"]["24h"]["available"] is True
    assert item["ranges"]["24h"]["source"] == "kraken_24h_ticker"
    assert item["ranges"]["7d"]["available"] is False


def test_dashboard_watchlist_is_independent_from_radar_and_uses_public_ticker(tmp_path, monkeypatch):
    import services.favorites_store as favorites_store

    monkeypatch.setattr(favorites_store, "Kraken", _FavoriteKrakenStub)
    watchlist_path = tmp_path / "config" / "dashboard_favorites.yaml"
    history_db_path = tmp_path / "runtime" / "favorites_history.sqlite3"
    _write_dashboard_watchlist(watchlist_path)

    payload = build_dashboard_favorites_analysis(
        watchlist_path=watchlist_path,
        history_db_path=history_db_path,
        trades=[],
    )

    assert payload["data_source"].startswith("dashboard favorites watchlist")
    assert payload["summary"]["followed_count"] == 1
    item = payload["items"][0]
    assert item["symbol"] == "HNTUSD"
    assert item["display_symbol"] == "HNT/USD"
    assert item["current_price"] == pytest.approx(0.7365)
    assert item["ranges"]["24h"]["available"] is True
    assert item["ranges"]["24h"]["source"] == "kraken_24h_ticker"
    assert history_db_path.exists()

    detail = build_dashboard_favorite_detail(
        "HNT/USD",
        watchlist_path=watchlist_path,
        history_db_path=history_db_path,
        trades=[],
    )
    assert detail is not None
    assert detail["symbol"] == "HNTUSD"


def test_dashboard_watchlist_handles_missing_config_without_network(tmp_path):
    payload = build_dashboard_favorites_analysis(
        watchlist_path=tmp_path / "missing.yaml",
        history_db_path=tmp_path / "runtime" / "favorites_history.sqlite3",
        trades=[],
    )

    assert payload["items"] == []
    assert payload["summary"]["followed_count"] == 0


def test_favorites_routes_render_and_return_safe_json(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setenv("KRAKEN_API_KEY", "favorite-test-api-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "favorite-test-api-secret")
    import services.favorites_store as favorites_store

    monkeypatch.setattr(favorites_store, "Kraken", _FavoriteKrakenStub)
    app_mod, db_path = _import_dashboard_app(tmp_path, monkeypatch)
    _write_dashboard_watchlist(tmp_path / "config" / "dashboard_favorites.yaml")
    app_mod._get_trades = lambda limit=500: [
        {"symbol": "HNTUSD", "pnl_usdc": 0.1, "reason": "TP", "ts_utc": now.isoformat()},
    ]
    client = app_mod.app.test_client()
    headers = _auth_header()

    page = client.get("/favorites", headers=headers)
    overview = client.get("/api/favorites", headers=headers)
    detail = client.get("/api/favorites/HNTUSD", headers=headers)

    assert page.status_code == 200
    assert b"Favoris" in page.data
    assert b'x-data="favoriteDashboard"' in page.data
    assert b"favorites.js?v=20260901b" in page.data
    assert overview.status_code == 200
    assert overview.get_json()["items"][0]["symbol"] == "HNTUSD"
    assert detail.status_code == 200
    assert detail.get_json()["item"]["bot_history"]["complete_trades"] == 1
    assert "test-secret-key" not in json.dumps(overview.get_json())
    assert b"favorite-test-api-key" not in page.data
    assert "favorite-test-api-secret" not in json.dumps(detail.get_json())


def test_favorites_routes_do_not_crash_without_favorites_or_trades(tmp_path, monkeypatch):
    app_mod, _ = _import_dashboard_app(tmp_path, monkeypatch)
    app_mod._get_trades = lambda limit=500: []
    client = app_mod.app.test_client()

    response = client.get("/api/favorites", headers=_auth_header())
    missing = client.get("/api/favorites/DOESNOTEXISTUSDC", headers=_auth_header())

    assert response.status_code == 200
    assert response.get_json()["items"] == []
    assert missing.status_code == 404
