#!/usr/bin/env python3
"""Tradovate DEMO OAuth test helper (authorization code + PKCE). Read-only: places no orders.

Client ID, secret and redirect URI are asked for once and saved in
~/.config/copytrader/tradovate_oauth.json (mode 600). Env vars TV_OAUTH_CLIENT_ID,
TV_OAUTH_CLIENT_SECRET, TV_OAUTH_REDIRECT_URI override the saved values.

  python3 backend-poc/tradovate_oauth.py authorize          # prints the approval link
  python3 backend-poc/tradovate_oauth.py exchange           # then paste the full redirected URL when asked
  python3 backend-poc/tradovate_oauth.py accounts           # GET /v1/account/list with the saved token
  python3 backend-poc/tradovate_oauth.py me                 # GET /v1/auth/me
  python3 backend-poc/tradovate_oauth.py balances           # every account: name, type, cash, net liq, P&L
  python3 backend-poc/tradovate_oauth.py refresh            # refresh_token grant
  python3 backend-poc/tradovate_oauth.py forget             # delete the saved client ID/secret/redirect URI
  python3 backend-poc/tradovate_oauth.py history            # count + date range of fills/orders the token can read

State and tokens are kept in ~/.cache/copytrader/ (mode 600), never printed.
DEMO ONLY: the hosts below are hard-coded on purpose.
"""
import base64, getpass, hashlib, json, os, secrets, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

AUTHORIZE_URL = "https://web.ninjatrader.com/oauth?env=demo"
TOKEN_URL = "https://demo.tradovateapi.com/auth/oauthtoken"
API = "https://demo.tradovateapi.com/v1"

CACHE = Path.home() / ".cache" / "copytrader"
PKCE_FILE = CACHE / "tradovate_pkce.json"
TOKEN_FILE = CACHE / "tradovate_oauth_token.json"
SECRET_FIELDS = {"access_token", "refresh_token", "id_token", "accessToken", "mdAccessToken"}


CONFIG = Path.home() / ".config" / "copytrader" / "tradovate_oauth.json"
PROMPTS = {
    "TV_OAUTH_CLIENT_ID": "OAuth client ID",
    "TV_OAUTH_CLIENT_SECRET": "OAuth client secret (input hidden)",
    "TV_OAUTH_REDIRECT_URI": "Redirect URI",
}


def env(name):
    """An env var wins; otherwise the value saved in CONFIG; otherwise ask once and save it.

    CONFIG lives outside the repo (mode 600), so it survives branch switches and new clones.
    """
    v = os.environ.get(name)
    if v:
        return v
    saved = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if saved.get(name):
        return saved[name]
    if not sys.stdin.isatty():
        sys.exit(f"missing {name}: set the env var or run this once in a terminal to save it")
    ask = getpass.getpass if name == "TV_OAUTH_CLIENT_SECRET" else input
    v = ask(f"{PROMPTS.get(name, name)}: ").strip()
    if not v:
        sys.exit(f"missing {name}")
    saved[name] = v
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG.parent, 0o700)
    CONFIG.touch(mode=0o600, exist_ok=True)
    os.chmod(CONFIG, 0o600)
    CONFIG.write_text(json.dumps(saved))
    print(f"saved to {CONFIG} (mode 600); `forget` deletes it")
    return v


def save(path, data):
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in SECRET_FIELDS else redact(v)) for k, v in obj.items()}
    return obj


def http(url, *, data=None, headers=None, form=False):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    headers.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw or b"null")
    except json.JSONDecodeError:
        return status, raw.decode(errors="replace")[:300]


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def cmd_authorize():
    verifier = b64url(secrets.token_bytes(48))
    state = b64url(secrets.token_bytes(16))
    save(PKCE_FILE, {"verifier": verifier, "state": state, "created": time.time()})
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": env("TV_OAUTH_CLIENT_ID"),
        "redirect_uri": env("TV_OAUTH_REDIRECT_URI"),
        "state": state,
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    })
    print("Open this in your browser, log in with a DEMO login, and approve:\n")
    print(f"{AUTHORIZE_URL}&{q}\n")
    print("The browser will then land on your redirect URI (the page may fail to load; that's fine).")
    print("Copy the FULL address from the address bar, then run:  exchange   (it asks for the URL)")


def token_request(grant):
    """Try the auth styles Tradovate advertises, in order of preference. Returns (style, status, body)."""
    cid, sec = env("TV_OAUTH_CLIENT_ID"), env("TV_OAUTH_CLIENT_SECRET")
    basic = {"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()}
    attempts = [
        # Tested 2026-11-02 on demo: basic+form works. JSON bodies get
        # 400 "missing required field grant_type" despite the spec saying JSON.
        ("basic+form", dict(grant), basic, True),
        ("basic+json", dict(grant), basic, False),
        ("body+json", {**grant, "client_id": cid, "client_secret": sec}, {}, False),
    ]
    last = None
    for style, data, headers, form in attempts:
        status, res = http(TOKEN_URL, data=data, headers=headers, form=form)
        print(f"  [{style}] HTTP {status}: {redact(res)}")
        if isinstance(res, dict) and res.get("access_token"):
            return style, status, res
        last = (style, status, res)
        if isinstance(res, dict) and res.get("error") == "invalid_grant":
            break  # code is spent or expired; retrying other styles won't help
    return last


def store_token(style, res):
    res = dict(res)
    res["obtained_at"] = time.time()
    res["auth_style"] = style
    save(TOKEN_FILE, res)
    print(f"\nOK via {style}. Fields returned: {sorted(k for k in res if k not in ('obtained_at', 'auth_style'))}")
    print(f"expires_in={res.get('expires_in')} refresh_token={'yes' if res.get('refresh_token') else 'no'} "
          f"refresh_token_expires_in={res.get('refresh_token_expires_in')}")


def cmd_exchange(arg):
    if not PKCE_FILE.exists():
        sys.exit("run 'authorize' first")
    pk = json.loads(PKCE_FILE.read_text())
    if arg.startswith("http"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        if "error" in qs:
            sys.exit(f"authorization failed: {qs}")
        if qs.get("state", [None])[0] != pk["state"]:
            sys.exit("state mismatch: this URL is not from the last 'authorize' run")
        code = qs["code"][0]
    else:
        code = arg
    grant = {"grant_type": "authorization_code", "code": code,
             "redirect_uri": env("TV_OAUTH_REDIRECT_URI"), "code_verifier": pk["verifier"]}
    style, status, res = token_request(grant)
    PKCE_FILE.unlink(missing_ok=True)  # one-time
    if not (isinstance(res, dict) and res.get("access_token")):
        sys.exit("\ntoken exchange failed (see responses above)")
    store_token(style, res)


def load_token():
    if not TOKEN_FILE.exists():
        sys.exit("no token saved; run authorize + exchange first")
    return json.loads(TOKEN_FILE.read_text())


def cmd_refresh():
    tok = load_token()
    if not tok.get("refresh_token"):
        sys.exit("no refresh_token was issued")
    style, status, res = token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    if not (isinstance(res, dict) and res.get("access_token")):
        sys.exit("\nrefresh failed")
    if not res.get("refresh_token"):
        res["refresh_token"] = tok["refresh_token"]
    store_token(style, res)


def cmd_get(path):
    tok = load_token()
    age = time.time() - tok["obtained_at"]
    if tok.get("expires_in") and age > tok["expires_in"]:
        print(f"warning: token is {age/60:.0f} min old and probably expired; run 'refresh'")
    status, res = http(API + path, headers={"Authorization": f"Bearer {tok['access_token']}"})
    print(f"HTTP {status}")
    print(json.dumps(redact(res), indent=2)[:4000])


def cmd_balances():
    tok = load_token()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    st, accts = http(API + "/account/list", headers=h)
    if st != 200 or not isinstance(accts, list):
        sys.exit(f"account/list failed: HTTP {st} {accts}")
    print(f"{len(accts)} account(s)")
    for a in accts:
        st, snap = http(API + "/cashBalance/getcashbalancesnapshot", data={"accountId": a["id"]}, headers=h)
        snap = snap if isinstance(snap, dict) else {}
        print(f"- {a.get('name')} (id {a.get('id')}, {a.get('accountType')}, active={a.get('active')})")
        print(f"    cash={snap.get('totalCashValue')} netLiq={snap.get('netLiq')} "
              f"realizedPnL={snap.get('realizedPnL')} openPnL={snap.get('openPnL')} "
              f"initialMargin={snap.get('initialMargin')}" + ("" if st == 200 else f"  (snapshot HTTP {st})"))


def cmd_history():
    """Read-only: how much trade history can this token see? Prints counts and date ranges only."""
    tok = load_token()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    for path, ts_field in [("/fill/list", "timestamp"), ("/fillPair/list", None),
                           ("/order/list", "timestamp"), ("/executionReport/list", "timestamp")]:
        st, rows = http(API + path, headers=h)
        if st != 200 or not isinstance(rows, list):
            print(f"{path}: HTTP {st} {str(rows)[:200]}")
            continue
        times = sorted(r[ts_field] for r in rows if ts_field and r.get(ts_field))
        span = f"  oldest={times[0]}  newest={times[-1]}" if times else ""
        print(f"{path}: HTTP 200, {len(rows)} item(s){span}")
        if rows:
            print(f"    fields: {sorted(rows[0])}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = sys.argv[1]
    if c == "authorize":
        cmd_authorize()
    elif c == "exchange" and len(sys.argv) == 3:
        cmd_exchange(sys.argv[2])
    elif c == "exchange" and len(sys.argv) == 2:
        # No argument: prompt for the URL, so the shell never splits it on '&'.
        cmd_exchange(input("Paste the full redirected URL and press Enter: ").strip().strip("'\""))
    elif c == "refresh":
        cmd_refresh()
    elif c == "forget":
        CONFIG.unlink(missing_ok=True)
        print(f"deleted {CONFIG}")
    elif c == "accounts":
        cmd_get("/account/list")
    elif c == "balances":
        cmd_balances()
    elif c == "history":
        cmd_history()
    elif c == "me":
        cmd_get("/auth/me")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
