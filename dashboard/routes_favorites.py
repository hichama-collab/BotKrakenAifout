"""Read-only routes for the personal favorites analysis view."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, render_template

from services.favorites_analysis import build_favorite_detail, build_favorites_analysis
from services.token_radar_store import resolve_db_path


def _normalize_symbol(raw: str) -> str:
    return str(raw or "").strip().upper().replace("/", "").replace("-", "")


def register_favorites_routes(app, require_basic_auth, base_dir, logs_trades_loader):
    base_dir = Path(base_dir)
    db_path = resolve_db_path(base_dir=base_dir)
    risk_path = base_dir / "config" / "risk.yaml"

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
        payload = build_favorites_analysis(
            db_path=db_path,
            risk_yaml_path=risk_path,
            trades=_trades(),
        )
        return jsonify({"ok": True, **payload})

    @app.route("/api/favorites/<symbol>")
    @require_basic_auth
    def api_favorite_detail(symbol):
        detail = build_favorite_detail(
            _normalize_symbol(symbol),
            db_path=db_path,
            risk_yaml_path=risk_path,
            trades=_trades(),
        )
        if detail is None:
            return jsonify({"ok": False, "error": "not_found", "symbol": _normalize_symbol(symbol)}), 404
        return jsonify({"ok": True, "item": detail})
