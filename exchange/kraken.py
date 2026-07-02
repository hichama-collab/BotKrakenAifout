from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests
from requests.exceptions import ConnectionError, HTTPError, ReadTimeout

from exchange.symbols import SymbolMapper, _asset_display


class KrakenApiError(RuntimeError):
    def __init__(self, errors, endpoint: str, status_http: int = 0, body=None):
        self.errors = list(errors or [])
        self.endpoint = endpoint
        self.status_http = status_http
        self.body = body
        super().__init__(
            f"KrakenApiError endpoint={endpoint} status={status_http} errors={self.errors} body={body}"
        )


class OrderStateUnknown(RuntimeError):
    pass


class Kraken:
    def __init__(
        self,
        apiKey: str = "",
        apiSecret: str = "",
        baseUrl: str = "https://api.kraken.com",
        httpTimeout: int = 12,
        httpRetries: int = 3,
        httpBackoff: float = 0.6,
        quoteAsset: str = "USDC",
    ):
        self.apiKey = apiKey or ""
        self.apiSecret = apiSecret or ""
        self.baseUrl = (baseUrl or "https://api.kraken.com").rstrip("/")
        self.httpTimeout = httpTimeout
        self.httpRetries = httpRetries
        self.httpBackoff = httpBackoff
        self.session = requests.Session()
        self.symbol_mapper = SymbolMapper(self, quoteAsset)
        self._last_nonce = 0

    def syncTime(self) -> None:
        self.get("/0/public/Time")

    def _nonce(self) -> str:
        current = int(time.time_ns())
        if current <= self._last_nonce:
            current = self._last_nonce + 1
        self._last_nonce = current
        return str(current)

    def sign(self, uri_path: str, data: dict) -> str:
        postdata = urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = uri_path.encode() + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.apiSecret)
        return base64.b64encode(hmac.new(secret, message, hashlib.sha512).digest()).decode()

    def backoff(self, attempt: int) -> None:
        time.sleep(self.httpBackoff * (2 ** max(0, attempt - 1)))

    @staticmethod
    def _retryable_errors(errors) -> bool:
        joined = " ".join(str(e) for e in (errors or [])).lower()
        return any(token in joined for token in ("rate limit", "temporarily unavailable", "service", "timeout"))

    def _handle_response(self, response: requests.Response, endpoint: str):
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}
        errors = data.get("error") if isinstance(data, dict) else None
        if errors:
            raise KrakenApiError(errors, endpoint, response.status_code, self._safe_body(data))
        if isinstance(data, dict) and "result" in data:
            return data.get("result")
        return data

    @staticmethod
    def _safe_body(data):
        try:
            text = json.dumps(data, ensure_ascii=True)
        except Exception:
            text = str(data)
        return text[:1500]

    def get(self, path: str, params=None, signed: bool = False):
        if signed:
            return self.post(path, params or {})
        params = dict(params or {})
        for attempt in range(1, self.httpRetries + 1):
            try:
                response = self.session.get(
                    f"{self.baseUrl}{path}",
                    params=params,
                    timeout=self.httpTimeout,
                )
                response.raise_for_status()
                return self._handle_response(response, path)
            except KrakenApiError as exc:
                if self._retryable_errors(exc.errors) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                raise
            except HTTPError as exc:
                if attempt < self.httpRetries and getattr(exc.response, "status_code", 0) in (429, 500, 502, 503, 504):
                    self.backoff(attempt)
                    continue
                body = getattr(exc.response, "text", "")[:1000] if getattr(exc, "response", None) is not None else ""
                raise KrakenApiError([str(exc)], path, getattr(exc.response, "status_code", 0), body) from exc
            except (ReadTimeout, ConnectionError):
                if attempt >= self.httpRetries:
                    raise
                self.backoff(attempt)

    def post(self, path: str, params: dict | None = None):
        if not self.apiKey or not self.apiSecret:
            raise KrakenApiError(["EGeneral:Missing API key/secret"], path, 0, "")
        payload = dict(params or {})
        payload["nonce"] = self._nonce()
        headers = {"API-Key": self.apiKey, "API-Sign": self.sign(path, payload)}
        max_attempts = 1 if path.endswith("/AddOrder") else self.httpRetries
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.post(
                    f"{self.baseUrl}{path}",
                    data=payload,
                    headers=headers,
                    timeout=self.httpTimeout,
                )
                response.raise_for_status()
                return self._handle_response(response, path)
            except KrakenApiError as exc:
                if self._retryable_errors(exc.errors) and attempt < max_attempts:
                    self.backoff(attempt)
                    continue
                raise
            except HTTPError as exc:
                if attempt < max_attempts and getattr(exc.response, "status_code", 0) in (429, 500, 502, 503, 504):
                    self.backoff(attempt)
                    continue
                body = getattr(exc.response, "text", "")[:1000] if getattr(exc, "response", None) is not None else ""
                raise KrakenApiError([str(exc)], path, getattr(exc.response, "status_code", 0), body) from exc
            except (ReadTimeout, ConnectionError) as exc:
                if path.endswith("/AddOrder"):
                    raise OrderStateUnknown(f"AddOrder transport state unknown endpoint={path}") from exc
                if attempt >= max_attempts:
                    raise
                self.backoff(attempt)

    def delete(self, path: str, params: dict):
        return self.post(path, params)

    def resolve_pair(self, symbol: str):
        return self.symbol_mapper.resolve_pair(symbol)

    def normalize_symbol(self, symbol: str) -> str:
        return self.resolve_pair(symbol).symbol

    def base_asset(self, symbol: str) -> str:
        return self.resolve_pair(symbol).base_asset

    def account_balances(self) -> dict:
        result = self.post("/0/private/Balance", {})
        balances = []
        for asset, value in (result or {}).items():
            try:
                qty = float(value or 0.0)
            except Exception:
                qty = 0.0
            balances.append({"asset": _asset_display(asset), "free": str(qty), "locked": "0"})
        return {"balances": balances, "raw": result or {}}

    def ticker(self, symbol: str | None = None):
        params = {}
        if symbol:
            params["pair"] = self.resolve_pair(symbol).pair_id
        return self.get("/0/public/Ticker", params)

    def best_bid_ask(self, symbol: str) -> tuple[float, float]:
        meta = self.resolve_pair(symbol)
        data = self.ticker(symbol)
        row = (data or {}).get(meta.pair_id) or next(iter((data or {}).values()), {})
        try:
            bid = float((row.get("b") or [0])[0])
            ask = float((row.get("a") or [0])[0])
            return bid, ask
        except Exception:
            return 0.0, 0.0

    def ticker_bid_map(self, quote_asset: str | None = None) -> dict[str, float]:
        quote = str(quote_asset or self.symbol_mapper.quote_asset).upper()
        try:
            data = self.get("/0/public/Ticker")
        except Exception:
            return {}
        out: dict[str, float] = {}
        pairs = self.symbol_mapper.load_asset_pairs()
        for pair_id, row in pairs.items():
            if not isinstance(row, dict) or _asset_display(row.get("quote", "")) != quote:
                continue
            ticker = (data or {}).get(pair_id) or {}
            try:
                bid = float((ticker.get("b") or [0])[0])
            except Exception:
                bid = 0.0
            if bid > 0:
                base = _asset_display(row.get("base", ""))
                out[f"{base}{quote}"] = bid
        return out

    def klines(self, symbol: str, interval: str = "1m", limit: int = 50) -> list:
        interval_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
        meta = self.resolve_pair(symbol)
        data = self.get("/0/public/OHLC", {"pair": meta.pair_id, "interval": interval_map.get(interval, interval)})
        rows = (data or {}).get(meta.pair_id) or next((v for k, v in (data or {}).items() if k != "last"), [])
        out = []
        for row in list(rows or [])[-int(limit):]:
            try:
                ts, open_, high, low, close, _vwap, volume = row[:7]
                out.append([int(float(ts) * 1000), open_, high, low, close, volume])
            except Exception:
                continue
        return out
