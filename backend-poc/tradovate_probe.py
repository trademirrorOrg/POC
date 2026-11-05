#!/usr/bin/env python3
"""Read-only probe: does every read permission of the OAuth app work on a DEMO login?

Run right after `tradovate_oauth.py exchange` (a fresh token with an id_token):

  python3 backend-poc/tradovate_probe.py pull      # calls every read endpoint, saves the results
  python3 backend-poc/tradovate_probe.py report    # PASS/FAIL per permission + the DEV-48 userId decision

Places NO orders and changes nothing. The only live-host call is GET /auth/me (DEV-48 option C check).
Saved and printed: HTTP status, item count, field NAMES and oldest/newest timestamps. Never values,
tokens, emails or names. Results file: ~/.cache/copytrader/probe_results.json
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradovate_oauth import API, CACHE, http, load_token, save  # noqa: E402
from dev48_userid_check import LIVE_ME, decode_payload  # noqa: E402

RESULTS = CACHE / "probe_results.json"

# (permission shown on the OAuth app screen, label, method, path)
ENDPOINTS = [
    ("Your User Information", "user list", "GET", "/user/list"),
    ("Account Information", "accounts", "GET", "/account/list"),
    ("Account Information", "cash balances", "GET", "/cashBalance/list"),
    ("Account Information", "balance snapshot (1st account)", "SNAPSHOT", "/cashBalance/getcashbalancesnapshot"),
    ("Account Information", "margin snapshots", "GET", "/marginSnapshot/list"),
    ("Account Risk Settings", "risk status", "GET", "/accountRiskStatus/list"),
    ("Account Risk Settings", "auto-liquidation settings", "GET", "/userAccountAutoLiq/list"),
    ("Account Risk Settings", "risk parameters", "GET", "/userAccountRiskParameter/list"),
    ("Positions", "positions", "GET", "/position/list"),
    ("Positions", "fill pairs (closed trades)", "GET", "/fillPair/list"),
    ("Orders (read)", "orders", "GET", "/order/list"),
    ("Orders (read)", "fills", "GET", "/fill/list"),
    ("Orders (read)", "execution reports", "GET", "/executionReport/list"),
    ("Orders (read)", "order strategies", "GET", "/orderStrategy/list"),
    ("Orders (read)", "commands", "GET", "/command/list"),
    ("Contract Library", "product search 'ES'", "GET", "/product/suggest?t=ES&l=5"),
    ("Contract Library", "contract search 'ES'", "GET", "/contract/suggest?t=ES&l=5"),
    ("Alerts", "alerts", "GET", "/alert/list"),
    ("Chat", "chats", "GET", "/chat/list"),
]
TIME_FIELDS = ("timestamp", "fillTime", "tradeDate", "timeStamp")


def summarize(status, body):
    out = {"status": status}
    if status != 200:
        out["error"] = str(body)[:160]
        return out
    rows = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    out["count"] = len(rows) if isinstance(body, list) else None
    out["fields"] = sorted(rows[0]) if rows and isinstance(rows[0], dict) else []
    times = sorted(str(r[f]) for r in rows if isinstance(r, dict) for f in TIME_FIELDS if r.get(f))
    if times:
        out["oldest"], out["newest"] = times[0], times[-1]
    return out


def cmd_pull():
    tok = load_token()
    age = time.time() - tok.get("obtained_at", 0)
    if tok.get("expires_in") and age > tok["expires_in"]:
        sys.exit(f"token is {age/60:.0f} min old and expired; run authorize + exchange again")
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    results = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "endpoints": [], "identity": {}}

    first_account = None
    for perm, label, method, path in ENDPOINTS:
        if method == "SNAPSHOT":
            if first_account is None:
                res = {"status": None, "error": "skipped: no account to query"}
            else:
                res = summarize(*http(API + path, data={"accountId": first_account}, headers=h))
        else:
            status, body = http(API + path, headers=h)
            res = summarize(status, body)
            if path == "/account/list" and status == 200 and isinstance(body, list) and body:
                first_account = body[0].get("id")
        res.update(permission=perm, label=label, path=path)
        results["endpoints"].append(res)
        extra = (f"{res['count']} item(s)" if res.get("count") is not None else "1 object") if res["status"] == 200 else res.get("error", "")
        print(f"[{res['status']}] {perm:22} {label:32} {extra}")

    # Identity (DEV-48): id_token sub vs live /auth/me userId. Only ids are kept.
    claims = decode_payload(tok["id_token"]) if tok.get("id_token") else {}
    st, me = http(LIVE_ME, headers=h)
    me = me if isinstance(me, dict) else {}
    results["identity"] = {
        "id_token": bool(tok.get("id_token")), "claims": sorted(claims), "sub": claims.get("sub"),
        "live_status": st, "userId": me.get("userId"), "org": me.get("organizationName"),
    }
    print(f"[{st}] identity: id_token={bool(tok.get('id_token'))} sub={claims.get('sub')} "
          f"live userId={me.get('userId')} org={me.get('organizationName')}")

    save(RESULTS, results)
    print(f"\nsaved -> {RESULTS}\nnow run: python3 backend-poc/tradovate_probe.py report")


def cmd_report():
    if not RESULTS.exists():
        sys.exit("nothing pulled yet; run `pull` first")
    r = json.loads(RESULTS.read_text())
    print(f"Tradovate DEMO read probe, pulled {r['at']}  (no orders placed)\n")

    perms = {}
    for e in r["endpoints"]:
        ok = e["status"] == 200
        perms.setdefault(e["permission"], []).append(ok)
        detail = ((f"{e['count']} item(s)" if e.get("count") is not None else "1 object") + (f", {e['oldest']} .. {e['newest']}" if e.get("oldest") else "")
                  if ok else f"HTTP {e['status']} {e.get('error', '')}")
        print(f"  [{'PASS' if ok else 'FAIL'}] {e['permission']:22} {e['label']:32} {detail}")
        if ok and e.get("fields"):
            print(f"         fields: {', '.join(e['fields'])}")

    i = r["identity"]
    match = i.get("sub") is not None and str(i.get("sub")) == str(i.get("userId"))
    print(f"\n  [{'PASS' if i.get('sub') else 'FAIL'}] identity: id_token has sub          sub={i.get('sub')}")
    print(f"  [{'PASS' if i.get('live_status') == 200 else 'FAIL'}] identity: live /auth/me            "
          f"userId={i.get('userId')} org={i.get('org')}")
    print(f"  [{'PASS' if match else 'FAIL'}] identity: sub == live userId")
    print(f"         id_token claims: {', '.join(i.get('claims') or [])}")

    print("\nBy permission:")
    for perm, oks in perms.items():
        print(f"  {'PASS' if all(oks) else 'PARTIAL' if any(oks) else 'FAIL':7} {perm} ({sum(oks)}/{len(oks)})")
    print("  SKIP    Market Data (WebSocket only, not probed)")
    print("  SKIP    Orders (write): placing trades is deliberately not tested")

    all_ok = all(all(o) for o in perms.values()) and match
    print("\nRESULT:", "everything readable works." if all_ok else "some checks failed; see FAIL lines above.")
    print("DEV-48:", "option A, use id_token `sub` as external_user_id." if match
          else ("option C, use live /auth/me userId." if i.get("live_status") == 200 else "undecided."))
    print("Notes: 0 items is fine (empty demo account). 401/403 = permission missing. 404 = endpoint name wrong.")


def main():
    cmds = {"pull": cmd_pull, "report": cmd_report}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()


if __name__ == "__main__":
    main()
