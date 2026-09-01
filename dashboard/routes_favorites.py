"""Read-only routes for the personal favorites analysis view."""

from __future__ import annotations

import os
from pathlib import Path

from flask import jsonify, render_template

from services.favorites_analysis import (
    build_dashboard_favorite_detail,
    build_dashboard_favorites_analysis,
)


def _normalize_symbol(raw: str) -> str:
    return str(raw or "").strip().upper().replace("/", "").replace("-", "")


def register_favorites_routes(app, require_basic_auth, base_dir, logs_trades_loader):
    base_dir = Path(base_dir)
    risk_path = base_dir / "config" / "risk.yaml"
    watchlist_path = base_dir / "config" / "dashboard_favorites.yaml"
    history_db_path = base_dir / "data" / "runtime" / "favorites_history.sqlite3"
    kraken_base_url = os.environ.get("KRAKEN_BASE_URL", "https://api.kraken.com")

    def _trades():
        try:
            return logs_trades_loader() or []
        except Exception:
            return []

    @app.route("/favorites")
    @require_basic_auth
    def favorites():
        return render_template("favorites.html")

    @app.route("/api/favorites")
    @require_basic_auth
    def api_favorites():
        payload = build_dashboard_favorites_analysis(
            watchlist_path=watchlist_path,
            history_db_path=history_db_path,
            risk_yaml_path=risk_path,
            trades=_trades(),
            kraken_base_url=kraken_base_url,
        )
        return jsonify({"ok": True, **payload})

    @app.route("/api/favorites/<symbol>")
    @require_basic_auth
    def api_favorite_detail(symbol):
        detail = build_dashboard_favorite_detail(
            _normalize_symbol(symbol),
            watchlist_path=watchlist_path,
            history_db_path=history_db_path,
            risk_yaml_path=risk_path,
            trades=_trades(),
        )
        if detail is None:
            return jsonify({"ok": False, "error": "not_found", "symbol": _normalize_symbol(symbol)}), 404
        return jsonify({"ok": True, "item": detail})
