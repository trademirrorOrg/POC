# Backend POC

Tradovate DEMO OAuth (authorization code + PKCE) helper, moved here from the Copytrader repo.
Read-only: places no orders. See the docstring in `tradovate_oauth.py` for usage.

Env vars: `TV_OAUTH_CLIENT_ID`, `TV_OAUTH_CLIENT_SECRET`, `TV_OAUTH_REDIRECT_URI`.

## DEV-48: stable userId check

`dev48_userid_check.py` checks that the id_token `sub` is present, equals the live `/auth/me` userId and stays the same across logins. Run `check personal` or `check prop` right after each `exchange`, then `report`. See its docstring.
