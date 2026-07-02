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

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _ws_symbol(self) -> str:
        if self.mapper is not None:
            try:
                return self.mapper.resolve_pair(self.symbol).ws_symbol
            except Exception:
                pass
        s = str(self.symbol).upper().replace("-", "/").replace("_", "/")
        return s if "/" in s else s.replace("BTC", "XBT", 1).replace("USDC", "/USDC")

    def _make_ws(self):
        return websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )

    def on_open(self, ws):
        payload = {
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": [self._ws_symbol()]},
        }
        ws.send(json.dumps(payload))

    def on_message(self, ws, msg):
        try:
            data = json.loads(msg)
            if not isinstance(data, dict) or data.get("channel") != "ticker":
                return
            rows = data.get("data") or []
            if not rows:
                return
            row = rows[0]
            bid_raw = row.get("bid")
            ask_raw = row.get("ask")
            bid = float(bid_raw[0] if isinstance(bid_raw, list) else bid_raw)
            ask = float(ask_raw[0] if isinstance(ask_raw, list) else ask_raw)
            now = time.time()
            if bid <= 0 or ask <= 0:
                return
            with self._lock:
                self.bestBid = bid
                self.bestAsk = ask
                self.lastUpdate = now
                self.tickSeq += 1
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

    def bestBidAsk(self):
        stale_sec = float(getattr(self.cfg, "wsStaleSec", 3.0))
        now = time.time()
        with self._lock:
            b = self.bestBid
            a = self.bestAsk
            lu = self.lastUpdate
        if b <= 0 or a <= 0 or lu <= 0 or (now - lu) > stale_sec:
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
            return 0.0, 0.0, 0.0, 0
        return b, a, lu, seq
