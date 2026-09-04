from __future__ import annotations

import time

from indicators.basic import fmt


class OrderStateUnknown(RuntimeError):
    """Raised when Kraken order final state cannot be proven."""


def _client_order_id(side: str, symbol: str) -> str:
    return f"aifout_{str(side).lower()}_{str(symbol).lower().replace('/', '')}_{int(time.time() * 1000)}"


def _fake_order(symbol: str, side: str, qty: float, price: float, orderId: int):
    client_order_id = _client_order_id(side, symbol)
    return {
        "symbol": symbol,
        "side": side.upper(),
        "orderId": orderId,
        "clientOrderId": client_order_id,
        "status": "FILLED",
        "executedQty": str(qty),
        "cummulativeQuoteQty": str(qty * price),
        "fills": [],
    }


def _pair_id(bx, symbol: str) -> str:
    if hasattr(bx, "resolve_pair"):
        return bx.resolve_pair(symbol).pair_id
    return symbol.replace("/", "").upper()


def _internal_status(row: dict) -> str:
    status = str(row.get("status") or row.get("descr", {}).get("status") or "").lower()
    try:
        exec_qty = float(row.get("vol_exec", row.get("executedQty", 0.0)) or 0.0)
    except Exception:
        exec_qty = 0.0
    if status in ("pending", "open"):
        return "PARTIALLY_FILLED" if exec_qty > 0 else "NEW"
    if status == "closed":
        return "FILLED" if exec_qty > 0 else "REJECTED"
    if status == "canceled":
        return "CANCELED"
    if status == "expired":
        return "EXPIRED"
    if status in ("rejected", "error"):
        return "REJECTED"
    return str(row.get("status") or "UNKNOWN").upper()


def map_kraken_order(
    symbol: str,
    order_id: str,
    row: dict,
    client_order_id: str = "",
    fee_asset: str = "",
) -> dict:
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    side = str(descr.get("type") or row.get("type") or "").upper()
    try:
        exec_qty = float(row.get("vol_exec", 0.0) or 0.0)
    except Exception:
        exec_qty = 0.0
    try:
        avg_price = float(row.get("price", 0.0) or 0.0)
    except Exception:
        avg_price = 0.0
    quote_qty = exec_qty * avg_price
    fee = row.get("fee", 0.0)
    fills = []
    try:
        fee_f = float(fee or 0.0)
        if fee_f > 0:
            fills.append({"commission": str(fee_f), "commissionAsset": str(fee_asset or "")})
    except Exception:
        pass
    return {
        "symbol": symbol,
        "side": side,
        "orderId": str(order_id),
        "clientOrderId": str(row.get("cl_ord_id") or client_order_id or ""),
        "status": _internal_status(row),
        "executedQty": str(exec_qty),
        "cummulativeQuoteQty": str(quote_qty),
        "fills": fills,
        "raw": row,
    }


def _add_order_txid(result) -> str:
    txid = (result or {}).get("txid") if isinstance(result, dict) else None
    if isinstance(txid, list) and txid:
        return str(txid[0])
    if txid:
        return str(txid)
    return ""


def placeLimit(bx, symbol: str, side: str, qty: float, price: float, stepQ, tickQ, dryRun: bool = False):
    side_up = str(side or "").upper()
    if side_up not in ("BUY", "SELL"):
        raise ValueError("Only BUY/SELL LIMIT orders are allowed")
    client_order_id = _client_order_id(side_up, symbol)
    if dryRun:
        oid = int(time.time() * 1000) % 10_000_000_000
        return {
            "orderId": str(oid),
            "clientOrderId": client_order_id,
            "status": "NEW",
            "symbol": symbol,
            "side": side_up,
            "price": price,
            "origQty": qty,
            "fills": [],
        }

    params = {
        "pair": _pair_id(bx, symbol),
        "type": side_up.lower(),
        "ordertype": "limit",
        "volume": fmt(qty, stepQ),
        "price": fmt(price, tickQ),
        "timeinforce": "GTC",
        "cl_ord_id": client_order_id,
    }
    result = bx.post("/0/private/AddOrder", params)
    order_id = _add_order_txid(result)
    if not order_id:
        raise OrderStateUnknown(f"ORDER_STATE_UNKNOWN symbol={symbol} side={side_up} add_order_without_txid")
    return {
        "symbol": symbol,
        "side": side_up,
        "orderId": order_id,
        "clientOrderId": client_order_id,
        "status": "NEW",
        "executedQty": "0",
        "cummulativeQuoteQty": "0",
        "fills": [],
    }


def getOrder(bx, symbol: str, orderId: str):
    result = bx.post("/0/private/QueryOrders", {"txid": str(orderId), "trades": True})
    row = (result or {}).get(str(orderId))
    if not isinstance(row, dict):
        return {"symbol": symbol, "orderId": str(orderId), "status": "UNKNOWN", "executedQty": "0"}
    fee_asset = ""
    try:
        fee_asset = str(bx.resolve_pair(symbol).quote_asset or "")
    except Exception:
        pass
    return map_kraken_order(symbol, str(orderId), row, fee_asset=fee_asset)


def cancelOrder(bx, symbol: str, orderId: str):
    result = bx.post("/0/private/CancelOrder", {"txid": str(orderId)})
    return {"symbol": symbol, "orderId": str(orderId), "status": "CANCEL_SENT", "raw": result}


def _compact_pair(value: object) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _order_belongs_to_pair(bx, symbol: str, row: dict) -> bool:
    """Fail closed when an OpenOrders row cannot be tied to the active pair."""
    descr = row.get("descr") if isinstance(row.get("descr"), dict) else {}
    raw_pair = descr.get("pair") or row.get("pair")
    if not raw_pair:
        return False
    try:
        meta = bx.resolve_pair(symbol)
        expected = {
            _compact_pair(meta.pair_id),
            _compact_pair(meta.symbol),
            _compact_pair(meta.ws_symbol),
        }
    except Exception:
        expected = {_compact_pair(symbol)}
    return _compact_pair(raw_pair) in expected


def openOrders(bx, symbol: str):
    result = bx.post("/0/private/OpenOrders", {"trades": True})
    rows = ((result or {}).get("open") or {}) if isinstance(result, dict) else {}
    fee_asset = ""
    try:
        fee_asset = str(bx.resolve_pair(symbol).quote_asset or "")
    except Exception:
        pass
    return [
        map_kraken_order(symbol, oid, row, fee_asset=fee_asset)
        for oid, row in rows.items()
        if isinstance(row, dict) and _order_belongs_to_pair(bx, symbol, row)
    ]


def order_fee_summary(order: dict, fallback_qty: float = 0.0, fallback_quote: float = 0.0) -> dict:
    fills = order.get("fills") if isinstance(order, dict) else None
    fee_total = 0.0
    assets = []
    if isinstance(fills, list):
        for fill in fills:
            try:
                fee = float(fill.get("commission", 0.0) or 0.0)
            except Exception:
                fee = 0.0
            asset = str(fill.get("commissionAsset", "") or "").strip()
            fee_total += fee
            if asset and asset not in assets:
                assets.append(asset)

    try:
        executed_qty = float(order.get("executedQty", fallback_qty) or 0.0)
    except Exception:
        executed_qty = float(fallback_qty or 0.0)
    try:
        quote_qty = float(order.get("cummulativeQuoteQty", fallback_quote) or 0.0)
    except Exception:
        quote_qty = float(fallback_quote or 0.0)

    return {
        "fee_source": "exchange" if fills else "estimated",
        "fee": fee_total,
        "commission_asset": ",".join(assets),
        "executed_qty": executed_qty,
        "quote_qty": quote_qty,
    }


def waitFillOrCancel(
    bx,
    symbol: str,
    orderId: str,
    ttl: float,
    poll: float,
    *,
    dryRun: bool = False,
    side: str = "",
    qty: float = 0.0,
    price: float = 0.0,
    maxRestRetries: int = 3,
    restBackoffSec: float = 0.2,
):
    if dryRun:
        return True, _fake_order(symbol, side, qty, price, orderId)

    def _executed_qty(order: dict) -> float:
        try:
            return float(order.get("executedQty", 0.0) or 0.0)
        except Exception:
            return 0.0

    t0 = time.time()
    nextLog = t0
    poll_failures = 0
    while time.time() - t0 < ttl:
        try:
            o = getOrder(bx, symbol, orderId)
            poll_failures = 0
        except Exception as e:
            poll_failures += 1
            print("ORDER_POLL_FAIL", symbol, orderId, type(e).__name__, str(e), "retry", poll_failures)
            if poll_failures >= maxRestRetries:
                print("ORDER_POLL_GIVEUP", symbol, orderId, "retries", poll_failures)
                break
            time.sleep(max(poll, restBackoffSec))
            continue

        st = o.get("status")
        if st == "FILLED":
            return True, o
        if st in ("CANCELED", "REJECTED", "EXPIRED"):
            return _executed_qty(o) > 0.0, o

        now = time.time()
        if now >= nextLog:
            print("ORDER_WAIT", symbol, orderId, st, "t", round(now - t0, 2))
            nextLog = now + 1.0
        time.sleep(poll)

    cancel_status = ""
    for attempt in range(1, maxRestRetries + 1):
        try:
            cancel_result = cancelOrder(bx, symbol, orderId)
            cancel_status = str(cancel_result.get("status", "CANCEL_SENT")) if isinstance(cancel_result, dict) else "CANCEL_SENT"
            break
        except Exception as e:
            cancel_status = f"ERROR:{type(e).__name__}"
            print("ORDER_CANCEL_FAIL", symbol, orderId, type(e).__name__, str(e), "retry", attempt)
            time.sleep(restBackoffSec)

    try:
        o = getOrder(bx, symbol, orderId)
    except Exception as e:
        print("ORDER_FINAL_POLL_FAIL", symbol, orderId, type(e).__name__, str(e))
        try:
            opens = openOrders(bx, symbol)
            open_count = len(opens) if isinstance(opens, list) else -1
        except Exception as open_exc:
            open_count = -1
            print("ORDER_OPEN_ORDERS_CHECK_FAIL", symbol, orderId, type(open_exc).__name__, str(open_exc))
        raise OrderStateUnknown(
            f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
            f"cancel_status={cancel_status or 'UNKNOWN'} open_orders={open_count}"
        ) from e

    st = str(o.get("status", "") or "")
    exec_qty = _executed_qty(o)
    if st == "UNKNOWN":
        raise OrderStateUnknown(
            f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
            f"cancel_status={cancel_status or 'UNKNOWN'}"
        )
    if st not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
        try:
            opens = openOrders(bx, symbol)
            open_count = len(opens) if isinstance(opens, list) else -1
        except Exception as open_exc:
            open_count = -1
            print("ORDER_OPEN_ORDERS_CHECK_FAIL", symbol, orderId, type(open_exc).__name__, str(open_exc))
        if open_count != 0:
            raise OrderStateUnknown(
                f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
                f"exchange_status={st} cancel_status={cancel_status or 'UNKNOWN'} open_orders={open_count}"
            )
        if exec_qty <= 0.0:
            raise OrderStateUnknown(
                f"ORDER_STATE_UNKNOWN symbol={symbol} orderId={orderId} side={side} "
                f"exchange_status={st} cancel_status={cancel_status or 'UNKNOWN'} open_orders=0"
            )
    return (st == "FILLED") or (exec_qty > 0.0), o
