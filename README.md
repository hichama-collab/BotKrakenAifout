# BotKrakenAifout

Kraken Spot scalping bot, ported from the existing AiFout bot.

Core safety rules:

- Spot only
- LIMIT orders only
- One position max
- No margin, futures, market orders, DCA, multi-position, ML, or heavy backtest
- Strategies only decide entries
- Common exits handle TP, trailing stop, breakeven escape, stop loss, time stop, and session high drop
- All trading thresholds live in `config/risk.yaml`
- Live mode fails closed without `KRAKEN_API_KEY` and `KRAKEN_API_SECRET`

## Local Setup

```bash
cd /mnt/data/Trade/BotKrakenAifout
./scripts/setup-venv.sh
cp .env.example .env
cp .service.env.example .service.env
```

Edit `.env`:

```bash
KRAKEN_API_KEY=
KRAKEN_API_SECRET=
KRAKEN_BASE_URL=https://api.kraken.com
KRAKEN_WS_URL=wss://ws.kraken.com/v2
KRAKEN_WS_CHANNEL=ticker
KRAKEN_WS_TICKER_EVENT_TRIGGER=bbo
KRAKEN_WS_BOOK_DEPTH=10
KRAKEN_ENV=spot
```

Edit `.service.env`:

```bash
PROFILE=strict
STRATEGY=momentum
SYMBOL=BTC/USDC
QUOTE_ASSET=USDC
DRY_RUN=1
```

## Run

Always start with dry-run:

```bash
DRY_RUN=1 PROFILE=strict STRATEGY=momentum ./scripts/start.sh BTC/USDC
```

Live requires Kraken spot API keys:

```bash
DRY_RUN=0 PROFILE=strict STRATEGY=momentum ./scripts/start.sh BTC/USDC
```

## Dashboard

```bash
cd dashboard
DASH_PASS='change-me' FLASK_SECRET_KEY='long-random-secret' python app.py
```

Default URL: `http://127.0.0.1:8099`

## Radar

```bash
python3 tools/token_radar_scan.py
```

Radar stores snapshots in `data/token_radar.sqlite3` and the dashboard exposes `/radar`.

`/favorites` is a separate, read-only personal watchlist. Its pairs live in
`config/dashboard_favorites.yaml`; it does not read or change the bot's active
symbol, the Radar universe, strategies, orders, or risk settings. The page uses
the public Kraken ticker and stores a compact local history only for the listed
favorites in `data/runtime/favorites_history.sqlite3`. Pairs with no usable
spot ticker are shown as `no data`; incomplete time windows are never estimated.

## Tests

```bash
python3 -m pytest -q
```

No test calls real private Kraken endpoints. Public scanner checks are separate from pytest.

## VPS / Systemd

Examples:

- `systemd/kraken-aifout-bot.service.example`
- `systemd/kraken-token-profile-selector.service`
- `systemd/kraken-token-profile-selector.timer`
- `dashboard/systemd/botdash.service.example`

Install under `/opt/kraken-aifout-bot`, copy `.env` and `.service.env`, then enable the units you need.

## Logs And Runtime

Generated files are ignored by Git:

- `data/logs/`
- `data/runtime/`
- `data/token_radar.sqlite3`
- `data/runtime/trade_memory.sqlite3`

`trades.csv` remains the trading source of truth.

## Security

Use Kraken API keys without withdrawal permission. Prefer IP whitelisting in Kraken account settings when available. Start in `DRY_RUN=1`, then test live with a very small order size before increasing caps.
