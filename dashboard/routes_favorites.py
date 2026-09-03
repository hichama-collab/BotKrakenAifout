"""Read-only routes for the personal favorites analysis view."""

from __future__ import annotations

import os
from pathlib import Path

from flask import jsonify, render_template, request

from services.favorites_analysis import (
    build_dashboard_favorites_analysis,
)
from services.favorites_series import get_favorite_series


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

    def _payload():
        return build_dashboard_favorites_analysis(
            watchlist_path=watchlist_path,
            history_db_path=history_db_path,
            risk_yaml_path=risk_path,
            trades=_trades(),
            kraken_base_url=kraken_base_url,
        )

    def _selected(payload, raw_symbol: str = ""):
        target = _normalize_symbol(raw_symbol)
        items = list(payload.get("items") or [])
        if target:
            selected = next((item for item in items if _normalize_symbol(item.get("symbol")) == target), None)
            if selected is not None:
                return selected
        return next((item for item in items if item.get("current_price") is not None), items[0] if items else None)

    @app.route("/favorites")
    @require_basic_auth
    def favorites():
        payload = _payload()
        selected = _selected(payload, request.args.get("symbol", ""))
        initial_series = (
            get_favorite_series(
                selected.get("symbol", ""),
                period="24h",
                history_db_path=history_db_path,
                base_url=kraken_base_url,
            )
            if selected is not None
            else None
        )
        return render_template(
            "favorites.html",
            payload=payload,
            selected=selected,
            initial_series=initial_series,
        )

    @app.route("/api/favorites")
    @require_basic_auth
    def api_favorites():
        return jsonify({"ok": True, **_payload()})

    @app.route("/api/favorites/<symbol>/series")
    @require_basic_auth
    def api_favorite_series(symbol):
        payload = _payload()
        selected = _selected(payload, symbol)
        if selected is None or _normalize_symbol(selected.get("symbol")) != _normalize_symbol(symbol):
            return jsonify({"ok": False, "error": "not_found", "symbol": _normalize_symbol(symbol)}), 404
        series = get_favorite_series(
            selected.get("symbol", ""),
            period=str(request.args.get("period") or "24h"),
            history_db_path=history_db_path,
            base_url=kraken_base_url,
        )
        return jsonify({"ok": True, "symbol": selected.get("symbol"), "series": series})

    @app.route("/api/favorites/<symbol>")
    @require_basic_auth
    def api_favorite_detail(symbol):
        payload = _payload()
        detail = _selected(payload, symbol)
        if detail is None or _normalize_symbol(detail.get("symbol")) != _normalize_symbol(symbol):
            return jsonify({"ok": False, "error": "not_found", "symbol": _normalize_symbol(symbol)}), 404
        return jsonify({"ok": True, "item": detail})
