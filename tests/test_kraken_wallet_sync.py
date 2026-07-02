from types import SimpleNamespace

from state.wallet_sync import walletSyncEvery
from tests.test_wallet_sync import FakeKraken


def _cfg(**kwargs):
    base = dict(
        walletMaxRetries=0,
        walletRetryBackoffSec=0,
        walletFlatCooldownSec=600,
        dustCooldownSec=30,
        dustStepFraction=0.5,
        quoteAsset="USDC",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_balance_quote_and_base_holding_unknown_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "BTC", "free": "0.001", "locked": "0"},
        {"asset": "USDC", "free": "42", "locked": "0"},
    ], trades={})

    pos, state, info = walletSyncEvery(
        bx, "BTCUSDC", None, _cfg(walletSyncCooldownSec=60), step=0.00001, minNotional=5, syncState={"next": 0}
    )

    assert pos is None
    assert state["status"] == "ENTRY_UNKNOWN"
    assert info["usdc"] == 42
    assert info["wallet_qty"] == 0.001


def test_qty_mismatch_resyncs_position(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "BTC", "free": "0.002", "locked": "0"},
        {"asset": "USDC", "free": "42", "locked": "0"},
    ])
    pos = SimpleNamespace(qty=0.001, entry=64000, high=64000, stop=63000, ts_entry=1)

    synced, _state, info = walletSyncEvery(
        bx, "BTCUSDC", pos, _cfg(), step=0.00001, minNotional=5, syncState={"next": 0}
    )

    assert synced.qty == 0.002
    assert info["reason"] == "qty_mismatch"
