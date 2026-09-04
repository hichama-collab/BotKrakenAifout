from types import SimpleNamespace

from exchange.kraken import Kraken
from state.wallet_sync import walletSyncEvery
from tests.test_wallet_sync import FakeKraken


def _cfg(**kwargs):
    values = {
        "walletMaxRetries": 0,
        "walletRetryBackoffSec": 0,
        "walletFlatCooldownSec": 600,
        "dustCooldownSec": 30,
        "dustStepFraction": 0.5,
        "quoteAsset": "USDC",
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_balance_ex_maps_available_and_trade_hold():
    client = Kraken("key", "c2VjcmV0")
    client.post = lambda path, params: {
        "USDC": {"balance": "20", "hold_trade": "17", "available": "3"},
        "XXBT": {"balance": "0.002", "hold_trade": "0.001", "available": "0.001"},
    }

    account = client.account_balances()
    balances = {row["asset"]: row for row in account["balances"]}

    assert account["source"] == "BalanceEx"
    assert account["available_exact"] is True
    assert balances["USDC"]["free"] == "3.0"
    assert balances["USDC"]["locked"] == "17.0"
    assert balances["BTC"]["free"] == "0.001"
    assert balances["BTC"]["locked"] == "0.001"


def test_external_holding_without_usdc_pair_is_visible_and_blocks_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "IDOS", "free": "1.7", "locked": "15928.3", "total": "15930"},
        {"asset": "USDC", "free": "20", "locked": "0", "total": "20"},
    ])

    pos, state, info = walletSyncEvery(
        bx,
        "BTCUSDC",
        None,
        _cfg(),
        step=0.00001,
        minNotional=5.0,
        syncState={"next": 0},
    )

    assert pos is None
    assert state["status"] == "EXTERNAL_HOLDING"
    assert info["reason"] == "external_symbol_found"
    assert info["external_symbol"] == "IDOSUSDC"
    assert info["valuation_status"] == "UNVALUED_NO_DIRECT_QUOTE"

    portfolio = (tmp_path / "portfolio.json").read_text(encoding="utf-8")
    assert "IDOS" in portfolio
    assert "UNVALUED_NO_DIRECT_QUOTE" in portfolio


def test_quote_free_excludes_amount_held_by_manual_order(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "USDC", "free": "3", "locked": "17", "total": "20"},
    ])

    pos, _state, info = walletSyncEvery(
        bx,
        "BTCUSDC",
        None,
        _cfg(),
        step=0.00001,
        minNotional=5.0,
        syncState={"next": 0},
    )

    assert pos is None
    assert info["usdc"] == 3.0
