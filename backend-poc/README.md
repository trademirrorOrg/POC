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

## Token refresh with an open WebSocket (T1, T4)

`tradovate_ws_token_test.py keep|reauth` opens the demo WebSocket with the saved token, refreshes it, and runs until 15 min past the old token's expiry (about 95 min after a fresh `exchange`). `keep` stays on the old token; `reauth` sends the new token on the same socket. It logs whether the socket survives, whether `account/list` over the socket still answers, whether a refresh revokes the old token over REST, and `refresh_token_expires_in` after the refresh. Needs `pip install websocket-client==1.8.0`. Places no orders and never prints tokens or account names; the log goes to `~/.cache/copytrader/`.
