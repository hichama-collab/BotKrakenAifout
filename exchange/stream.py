from __future__ import annotations

import json
import threading
import time

import websocket

from exchange.symbols import SymbolMapper


class Stream:
    def __init__(self, cfg, symbol: str, mapper: SymbolMapper | None = None):
        self.cfg = cfg
        self.symbol = symbol
        self.mapper = mapper

        self.bestBid = 0.0
        self.bestAsk = 0.0
        self.lastUpdate = 0.0
        self.tickSeq = 0
        self.url = str(getattr(cfg, "wsUrl", "wss://ws.kraken.com/v2") or "wss://ws.kraken.com/v2")
        self.channel = str(getattr(cfg, "wsChannel", "book") or "book").strip().lower()
        if self.channel not in {"book", "ticker"}:
            self.channel = "book"
        try:
            self.bookDepth = int(getattr(cfg, "wsBookDepth", 10) or 10)
        except (TypeError, ValueError):
            self.bookDepth = 10
        if self.bookDepth not in {10, 25, 100, 500, 1000}:
            self.bookDepth = 10

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._bookBids: dict[float, float] = {}
        self._bookAsks: dict[float, float] = {}

    def _ws_symbol(self) -> str:
        if self.mapper is not None:
            try:
                return self._v2_symbol(self.mapper.resolve_pair(self.symbol).ws_symbol)
            except Exception:
                pass
        s = str(self.symbol).upper().replace("-", "/").replace("_", "/")
        return self._v2_symbol(s if "/" in s else s.replace("USDC", "/USDC"))

    @staticmethod
    def _v2_symbol(symbol: str) -> str:
        """Kraken REST/AssetPairs may expose legacy assets while WS v2 uses modern names."""
        s = str(symbol or "").upper().replace("-", "/").replace("_", "/")
        if s.startswith("XBT/"):
            return "BTC/" + s.split("/", 1)[1]
        if s.startswith("XDG/"):
            return "DOGE/" + s.split("/", 1)[1]
        return s

    def _make_ws(self):
        return websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )

    def on_open(self, ws):
        params = {"channel": self.channel, "symbol": [self._ws_symbol()]}
        if self.channel == "book":
            params["depth"] = self.bookDepth
        payload = {
            "method": "subscribe",
            "params": params,
        }
        ws.send(json.dumps(payload))

    def _accept_bid_ask(self, bid: float, ask: float) -> None:
        if bid <= 0 or ask <= 0 or bid >= ask:
            return
        with self._lock:
            self.bestBid = bid
            self.bestAsk = ask
            self.lastUpdate = time.time()
            self.tickSeq += 1

    @staticmethod
    def _apply_levels(book: dict[float, float], levels) -> None:
        for level in levels or []:
            try:
                price = float(level.get("price"))
                qty = float(level.get("qty"))
            except (AttributeError, TypeError, ValueError):
                continue
            if price <= 0:
                continue
            if qty <= 0:
                book.pop(price, None)
            else:
                book[price] = qty

    def _handle_book_message(self, data: dict) -> None:
        message_type = str(data.get("type") or "").lower()
        rows = data.get("data") or []
        if message_type not in {"snapshot", "update"} or not rows:
            return

        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if message_type == "snapshot":
                    self._bookBids.clear()
                    self._bookAsks.clear()
                self._apply_levels(self._bookBids, row.get("bids"))
                self._apply_levels(self._bookAsks, row.get("asks"))

            if not self._bookBids or not self._bookAsks:
                return
            bid = max(self._bookBids)
            ask = min(self._bookAsks)
            if bid <= 0 or ask <= 0 or bid >= ask:
                return
            self.bestBid = bid
            self.bestAsk = ask
            self.lastUpdate = time.time()
            self.tickSeq += 1

    def on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            if not isinstance(data, dict):
                return
            if data.get("channel") == "book":
                self._handle_book_message(data)
                return
            if data.get("channel") != "ticker":
                return
            rows = data.get("data") or []
            if not rows:
                return
            row = rows[0]
            bid_raw = row.get("bid")
            ask_raw = row.get("ask")
            bid = float(bid_raw[0] if isinstance(bid_raw, list) else bid_raw)
            ask = float(ask_raw[0] if isinstance(ask_raw, list) else ask_raw)
            self._accept_bid_ask(bid, ask)
        except Exception:
            return

    def on_error(self, ws, err):
        print("WS_ERROR", err)

    def on_close(self, ws, *args):
        print("WS_CLOSED")

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def runner():
            backoff = float(getattr(self.cfg, "wsReconnectBackoffSec", 1.0))
            while not self._stop.is_set():
                ws = None
                try:
                    ws = self._make_ws()
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    print("WS_ERROR", type(e).__name__, str(e))
                if self._stop.is_set():
                    break
                print("WS_RECONNECT", self.symbol)
                time.sleep(backoff)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

        for _ in range(50):
            b, a = self.bestBidAsk()
            if b > 0 and a > 0:
                return
            time.sleep(0.1)

    def stop(self):
        self._stop.set()

    def _rest_fallback(self) -> tuple[float, float, float, int]:
        if not bool(getattr(self.cfg, "dryRun", False)):
            return 0.0, 0.0, 0.0, 0
        client = getattr(self.mapper, "client", None)
        if client is None or not hasattr(client, "best_bid_ask"):
            return 0.0, 0.0, 0.0, 0
        try:
            bid, ask = client.best_bid_ask(self.symbol)
            bid = float(bid)
            ask = float(ask)
        except Exception as exc:
            print("WS_REST_FALLBACK_FAIL", type(exc).__name__, str(exc))
            return 0.0, 0.0, 0.0, 0
        if bid <= 0 or ask <= 0:
            return 0.0, 0.0, 0.0, 0
        now = time.time()
        with self._lock:
            self.bestBid = bid
            self.bestAsk = ask
            self.lastUpdate = now
            self.tickSeq += 1
            seq = self.tickSeq
        return bid, ask, now, seq

    def bestBidAsk(self):
        stale_sec = float(getattr(self.cfg, "wsStaleSec", 3.0))
        now = time.time()
        with self._lock:
            b = self.bestBid
            a = self.bestAsk
            lu = self.lastUpdate
        if b <= 0 or a <= 0 or lu <= 0 or (now - lu) > stale_sec:
            fb, fa, _fl, _fs = self._rest_fallback()
            if fb > 0 and fa > 0:
                return fb, fa
            return 0.0, 0.0
        return b, a

    def snapshot(self):
        stale_sec = float(getattr(self.cfg, "wsStaleSec", 3.0))
        now = time.time()
        with self._lock:
            b = self.bestBid
            a = self.bestAsk
            lu = self.lastUpdate
            seq = self.tickSeq
        if b <= 0 or a <= 0 or lu <= 0 or (now - lu) > stale_sec:
            fb, fa, flu, fseq = self._rest_fallback()
            if fb > 0 and fa > 0:
                return fb, fa, flu, fseq
            return 0.0, 0.0, 0.0, 0
        return b, a, lu, seq
