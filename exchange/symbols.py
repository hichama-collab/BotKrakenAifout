from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class SymbolNotFound(RuntimeError):
    pass


def _asset_display(asset: str) -> str:
    raw = str(asset or "").upper()
    aliases = {
        "XXBT": "BTC",
        "XBT": "BTC",
        "XETH": "ETH",
        "ZUSD": "USD",
        "ZEUR": "EUR",
    }
    return aliases.get(raw, raw.removeprefix("X").removeprefix("Z") if len(raw) > 3 else raw)


def _compact(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()


@dataclass(frozen=True)
class PairMetadata:
    symbol: str
    pair_id: str
    ws_symbol: str
    base_asset: str
    quote_asset: str
    price_decimals: int
    quantity_decimals: int
    min_order_volume: Decimal
    min_notional: Decimal
    tick: Decimal
    step: Decimal


class SymbolMapper:
    def __init__(self, client: Any | None = None, quote_asset: str = "USDC"):
        self.client = client
        self.quote_asset = str(quote_asset or "USDC").upper()
        self._pairs: dict[str, Any] | None = None
        self._cache: dict[str, PairMetadata] = {}

    def normalize_user_symbol(self, raw: str, quote_asset: str | None = None) -> str:
        quote = str(quote_asset or self.quote_asset or "USDC").upper()
        s = _compact(raw)
        if not s:
            raise SymbolNotFound("Symbol is empty")
        if "/" in str(raw or ""):
            left, right = str(raw).upper().replace("-", "/").replace("_", "/").split("/", 1)
            base = "BTC" if left == "XBT" else left
            return f"{base}{right}"
        if s.startswith("XBT"):
            s = "BTC" + s[3:]
        if not s.endswith(quote):
            s = f"{s}{quote}"
        return s

    def load_asset_pairs(self) -> dict[str, Any]:
        if self._pairs is not None:
            return self._pairs
        if self.client is None:
            self._pairs = {}
            return self._pairs
        data = self.client.get("/0/public/AssetPairs")
        if isinstance(data, dict) and "result" in data:
            data = data.get("result") or {}
        self._pairs = data if isinstance(data, dict) else {}
        return self._pairs

    def resolve_pair(self, symbol: str) -> PairMetadata:
        normalized = self.normalize_user_symbol(symbol)
        if normalized in self._cache:
            return self._cache[normalized]
        pairs = self.load_asset_pairs()
        wanted_quote = self.quote_asset
        aliases = {normalized, normalized.replace("BTC", "XBT", 1)}
        best: PairMetadata | None = None
        for pair_id, row in pairs.items():
            if not isinstance(row, dict):
                continue
            base = _asset_display(row.get("base", ""))
            quote = _asset_display(row.get("quote", ""))
            altname = _compact(row.get("altname", ""))
            wsname = str(row.get("wsname") or "").upper()
            candidates = {
                f"{base}{quote}",
                _compact(wsname),
                altname,
                altname.replace("XBT", "BTC", 1),
                _compact(pair_id),
                _compact(pair_id).replace("XBT", "BTC", 1),
            }
            if quote != wanted_quote or not (aliases & candidates or normalized in candidates):
                continue
            pair_decimals = int(row.get("pair_decimals", 8) or 8)
            lot_decimals = int(row.get("lot_decimals", 8) or 8)
            tick_size = row.get("tick_size")
            tick = Decimal(str(tick_size)) if tick_size not in (None, "") else Decimal(1).scaleb(-pair_decimals)
            step = Decimal(1).scaleb(-lot_decimals)
            ordermin = Decimal(str(row.get("ordermin", "0") or "0"))
            costmin = Decimal(str(row.get("costmin", "0") or "0"))
            ws_symbol = str(row.get("wsname") or f"{base}/{quote}")
            best = PairMetadata(
                symbol=f"{base}{quote}",
                pair_id=str(pair_id),
                ws_symbol=ws_symbol,
                base_asset=base,
                quote_asset=quote,
                price_decimals=pair_decimals,
                quantity_decimals=lot_decimals,
                min_order_volume=ordermin,
                min_notional=costmin,
                tick=tick,
                step=step,
            )
            break
        if best is None:
            raise SymbolNotFound(f"Kraken spot pair not found for {symbol!r} with quote {wanted_quote}")
        self._cache[normalized] = best
        self._cache[best.symbol] = best
        return best

    def get_pair_metadata(self, symbol: str) -> dict[str, Any]:
        meta = self.resolve_pair(symbol)
        return {
            "symbol": meta.symbol,
            "pair_id": meta.pair_id,
            "ws_symbol": meta.ws_symbol,
            "base_asset": meta.base_asset,
            "quote_asset": meta.quote_asset,
            "price_decimals": meta.price_decimals,
            "quantity_decimals": meta.quantity_decimals,
            "min_order_volume": meta.min_order_volume,
            "min_notional": meta.min_notional,
            "tick": meta.tick,
            "step": meta.step,
        }


def initSymbol(bx, symbol: str):
    mapper = getattr(bx, "symbol_mapper", SymbolMapper(bx))
    meta = mapper.resolve_pair(symbol)
    # ``ordermin * tick`` is a price, not an order cost.  A quote minimum can
    # only be represented statically when Kraken supplies ``costmin``.
    min_notional = float(meta.min_notional or 0)
    return float(meta.tick), float(meta.step), meta.tick, meta.step, min_notional
