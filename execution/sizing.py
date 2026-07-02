# auto-extracted from main.py
from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from typing import Any, Tuple

def quantDown(x: float, q: Decimal) -> float:
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_DOWN))

def quoteFree(bx, quote_asset: str = "USDC"):
    if hasattr(bx, "account_balances"):
        acc = bx.account_balances()
    else:
        acc = bx.get("/0/private/Balance", signed=True)
    quote = str(quote_asset or "USDC").upper()
    bal = next((b for b in acc.get("balances", []) if b.get("asset") == quote), None)
    return float(bal["free"]) if bal else 0.0

def usdcFree(bx):
    return quoteFree(bx, "USDC")
