# Backend POC

Tradovate DEMO OAuth (authorization code + PKCE) helper, moved here from the Copytrader repo.
Read-only: places no orders. See the docstring in `tradovate_oauth.py` for usage.

Env vars: `TV_OAUTH_CLIENT_ID`, `TV_OAUTH_CLIENT_SECRET`, `TV_OAUTH_REDIRECT_URI`.

## DEV-48: stable userId check

`dev48_userid_check.py` checks that the id_token `sub` is present, equals the live `/auth/me` userId and stays the same across logins. Run `check personal` or `check prop` right after each `exchange`, then `report`. See its docstring.

## Read probe (all permissions)

`tradovate_probe.py pull` calls every read endpoint the OAuth app's permissions cover (user, accounts, balances, risk, positions, orders, fills, contracts, alerts, chat) plus the DEV-48 identity check; `report` prints PASS/FAIL per permission. Places no orders.

## Trade export (journaling)

`tradovate_export.py` writes `backend-poc/exports/<timestamp>/` with `trades.csv` (one row per closed trade: account, symbol, side, qty, entry/exit time and price, P&L in points and currency before fees, order types), `fills.csv`, `orders.csv` and the raw API responses in `raw/`. Tradovate only returns the current trading session (23:00 to 22:00 Lisbon in winter), so run it each session. The folder holds real trade data and is git-ignored.
