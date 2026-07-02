from types import SimpleNamespace

from state.position import Position
from state.wallet_sync import loadWalletFlatGuard, walletSyncEvery


class FakeKraken:
    def __init__(self, balances, bid=64000.0, trades=None):
        self.balances = balances
        self.bid = bid
        self.trades = trades if trades is not None else []

    def account_balances(self):
        return {"balances": self.balances}

    def best_bid_ask(self, symbol):
        return self.bid, self.bid * 1.0001

    def ticker_bid_map(self):
        return {"BTCUSDC": self.bid}

    def base_asset(self, symbol):
        return "BTC"

    def resolve_pair(self, symbol):
        return SimpleNamespace(pair_id="XBTUSDC")

    def post(self, path, params=None):
        if path == "/0/private/TradesHistory":
            return {"trades": self.trades}
        raise AssertionError(path)


def test_wallet_dust_records_reentry_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "BTC", "free": "0.000008", "locked": "0"},
        {"asset": "USDC", "free": "50", "locked": "0"},
    ])
    cfg = SimpleNamespace(
        walletMaxRetries=0,
        walletRetryBackoffSec=0,
        walletFlatCooldownSec=600,
        dustCooldownSec=30,
        dustStepFraction=0.5,
    )
    pos = Position(qty=0.001, entry=64000, high=64000, stop=63000, ts_entry=1)

    synced, _, info = walletSyncEvery(
        bx,
        "BTCUSDC",
        pos,
        cfg,
        step=0.00001,
        minNotional=5.0,
        syncState={"next": 0.0},
        intervalSec=5,
    )

    assert synced is None
    assert info["reason"] == "wallet_dust"
    assert info["wallet_qty"] == 0.000008
    assert info["wallet_notional"] == 0.512
    assert info["entry_block_until"] > 0
    guard = loadWalletFlatGuard("BTCUSDC", now=info["entry_block_until"] - 1)
    assert guard["reason"] == "wallet_dust"


def test_existing_dust_without_position_does_not_extend_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    bx = FakeKraken([
        {"asset": "BTC", "free": "0.000008", "locked": "0"},
        {"asset": "USDC", "free": "50", "locked": "0"},
    ])
    cfg = SimpleNamespace(
        walletMaxRetries=0,
        walletRetryBackoffSec=0,
        walletFlatCooldownSec=600,
        dustCooldownSec=30,
        dustStepFraction=0.5,
    )

    synced, _, info = walletSyncEvery(
        bx,
        "BTCUSDC",
        None,
        cfg,
        step=0.00001,
        minNotional=5.0,
        syncState={"next": 0.0},
        intervalSec=5,
    )

    assert synced is None
    assert info["reason"] == "wallet_dust"
    assert "entry_block_until" not in info
    assert loadWalletFlatGuard("BTCUSDC") == {}


def test_wallet_position_without_known_entry_blocks_instead_of_using_bid(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path))
    events = []
    bx = FakeKraken([
        {"asset": "BTC", "free": "0.0002", "locked": "0"},
        {"asset": "USDC", "free": "50", "locked": "0"},
    ], bid=64000.0, trades=[])
    cfg = SimpleNamespace(
        walletMaxRetries=0,
        walletRetryBackoffSec=0,
        walletSyncCooldownSec=60,
        dustCooldownSec=30,
        dustStepFraction=0.5,
    )
    sync_state = {"next": 0.0}

    synced, sync_state, info = walletSyncEvery(
        bx,
        "BTCUSDC",
        None,
        cfg,
        step=0.00001,
        minNotional=5.0,
        syncState=sync_state,
        intervalSec=5,
        logTrade=events.append,
    )

    assert synced is None
    assert info["status"] == "ENTRY_UNKNOWN"
    assert info["reason"] == "wallet_position_without_known_entry"
    assert sync_state["status"] == "ENTRY_UNKNOWN"
    assert sync_state["next"] > 0
    assert any(isinstance(e, dict) and e.get("event") == "ENTRY_UNKNOWN" for e in events)
