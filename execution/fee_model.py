"""
Calcul des frais Kraken estimés et PnL net.
"""
from __future__ import annotations
import time
from typing import Callable, Optional

DEFAULT_FEE_RATE = 0.001  # configured fallback when Kraken fee lookup is unavailable

class FeeModel:
    def __init__(
        self,
        fee_rate: float = DEFAULT_FEE_RATE,
        use_bnb: bool = False,
        fee_rate_resolver: Optional[Callable[[str], dict]] = None,
    ):
        self.base_fee_rate = fee_rate
        self.use_bnb = False
        self.fee_rate = fee_rate
        self.fee_rate_source = "config"
        self._fee_rate_resolver = fee_rate_resolver
        self._cache: dict = {}
        self._cache_ttl = 3600.0

    def get_fee_rate(self, symbol: str = "") -> float:
        """Return the active conservative fee rate for the current pair."""
        return self.fee_rate

    def refresh_fee_rate(self, symbol: str = "") -> dict:
        """Refresh from Kraken's account fee schedule, with an explicit fallback."""
        key = str(symbol or "").upper()
        now = time.time()
        cached = self._cache.get(key)
        if cached and (now - cached["ts"]) < self._cache_ttl:
            self.fee_rate = cached["rate"]
            self.fee_rate_source = cached["source"]
            return {"rate": self.fee_rate, "source": self.fee_rate_source, "cached": True}

        if self._fee_rate_resolver is None:
            self.fee_rate = self.base_fee_rate
            self.fee_rate_source = "config"
            return {"rate": self.fee_rate, "source": self.fee_rate_source, "cached": False}

        try:
            payload = self._fee_rate_resolver(symbol)
            # A regular LIMIT order can take liquidity.  Use taker fees in the
            # common risk calculations unless the order is explicitly post-only.
            rate = float((payload or {}).get("taker"))
            if rate < 0:
                raise ValueError("negative fee rate")
            self.fee_rate = rate
            self.fee_rate_source = str((payload or {}).get("source") or "exchange")
            self._cache[key] = {"ts": now, "rate": rate, "source": self.fee_rate_source}
            return {"rate": rate, "source": self.fee_rate_source, "cached": False}
        except Exception as exc:
            self.fee_rate = self.base_fee_rate
            self.fee_rate_source = "config_fallback"
            return {
                "rate": self.fee_rate,
                "source": self.fee_rate_source,
                "cached": False,
                "error": type(exc).__name__,
            }

    def estimate_round_trip_cost(self, notional: float, symbol: str = "") -> float:
        """Coût total estimé d'un round-trip BUY+SELL en USDC."""
        rate = self.get_fee_rate(symbol)
        return notional * rate * 2.0

    def compute_net_pnl(
        self,
        buy_price: float,
        buy_qty: float,
        sell_price: float,
        sell_qty: float,
        symbol: str = "",
        actual_fees_buy: Optional[float] = None,
        actual_fees_sell: Optional[float] = None,
    ) -> dict:
        """
        Calcule le PnL NET réel d'un round-trip.
        Kraken peut prélever les frais dans différents actifs selon le compte.
        Le bot conserve un modèle configurable: fees_buy = buy_notional * fee_rate,
        fees_sell = sell_notional * fee_rate.
        """
        rate = self.get_fee_rate(symbol)
        buy_notional = buy_price * buy_qty
        sell_notional = sell_price * sell_qty
        fees_buy = buy_notional * rate if actual_fees_buy is None else max(0.0, float(actual_fees_buy))
        fees_sell = sell_notional * rate if actual_fees_sell is None else max(0.0, float(actual_fees_sell))
        gross_pnl = sell_notional - buy_notional
        net_pnl = gross_pnl - fees_buy - fees_sell
        net_pnl_pct = (net_pnl / buy_notional * 100.0) if buy_notional > 0 else 0.0
        qty_lost = (fees_buy / buy_price) if buy_price > 0 else 0.0
        return {
            "gross_pnl": round(gross_pnl, 6),
            "fees_buy": round(fees_buy, 6),
            "fees_sell": round(fees_sell, 6),
            "total_fees": round(fees_buy + fees_sell, 6),
            "net_pnl": round(net_pnl, 6),
            "net_pnl_pct": round(net_pnl_pct, 4),
            "qty_lost_to_fees": round(qty_lost, 8),
            "breakeven_pct": round((fees_buy + fees_sell) / buy_notional * 100.0, 4) if buy_notional > 0 else 0.0,
        }

    def min_move_to_profit(self, spread_pct: float = 0.0) -> float:
        """Mouvement de prix minimum (%) pour être profitable net."""
        return self.fee_rate * 2.0 + spread_pct

_default_model: Optional[FeeModel] = None

def get_default_fee_model(use_bnb: bool = False) -> FeeModel:
    global _default_model
    if _default_model is None:
        _default_model = FeeModel(use_bnb=use_bnb)
    return _default_model
