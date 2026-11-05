#!/usr/bin/env python3
"""DEV-48 spike: is the id_token `sub` a stable Tradovate userId for a DEMO login?

Run this right after `tradovate_oauth.py exchange` (a refresh does not return an id_token):

  python3 backend-poc/dev48_userid_check.py check personal   # or: check prop
  python3 backend-poc/dev48_userid_check.py report           # verdict once all runs are recorded

What `check` does, using the token saved by tradovate_oauth.py:
  1. Decodes the id_token payload (no signature check) and reads `sub` plus the claim names.
  2. Calls https://live.tradovateapi.com/v1/auth/me ONCE with the demo access token (read-only;
     the only live call) to get the real userId and organizationName.
  3. Appends the result to ~/.cache/copytrader/dev48_results.json.

It prints only claim NAMES and the ids (sub, userId, organizationName). Never tokens, email or name.
Do 2 separate authorize+exchange runs per login type, so `report` can check that `sub` is stable.
"""
import base64, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradovate_oauth import CACHE, http, load_token, save  # noqa: E402

LIVE_ME = "https://live.tradovateapi.com/v1/auth/me"
RESULTS = CACHE / "dev48_results.json"
LABELS = ("personal", "prop")


def decode_payload(jwt):
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ValueError("id_token is not a JWT (expected 3 parts)")
    p = parts[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def load_results():
    return json.loads(RESULTS.read_text()) if RESULTS.exists() else []


def cmd_check(label):
    if label not in LABELS:
        sys.exit(f"label must be one of {LABELS}")
    tok = load_token()
    if tok.get("auth_style") and not tok.get("id_token"):
        sys.exit("saved token has no id_token. Run authorize + exchange again (refresh drops it), then re-run check.")

    claims = decode_payload(tok["id_token"]) if tok.get("id_token") else {}
    sub = claims.get("sub")
    print(f"id_token present: {bool(tok.get('id_token'))}")
    print(f"claim names: {sorted(claims)}")
    print(f"sub: {sub}")

    status, me = http(LIVE_ME, headers={"Authorization": f"Bearer {tok['access_token']}"})
    me = me if isinstance(me, dict) else {}
    user_id, org = me.get("userId"), me.get("organizationName")
    print(f"live /auth/me: HTTP {status}  userId={user_id}  organizationName={org}")
    if status != 200:
        print("  (live /auth/me failed; token may be expired. Fresh authorize + exchange, then check again)")

    match = sub is not None and user_id is not None and str(sub) == str(user_id)
    print(f"sub == userId: {match}")

    rows = load_results()
    rows.append({
        "label": label, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "obtained_at": tok.get("obtained_at"), "id_token": bool(tok.get("id_token")),
        "claims": sorted(claims), "sub": sub, "userId": user_id, "org": org,
        "live_status": status, "match": match,
    })
    save(RESULTS, rows)
    print(f"\nrecorded run #{sum(r['label'] == label for r in rows)} for '{label}' -> {RESULTS}")


def cmd_report():
    rows = load_results()
    if not rows:
        sys.exit("no runs recorded yet; use `check personal` / `check prop`")
    print(f"{'label':9} {'when':19} {'id_token':8} {'sub':>12} {'userId':>12} {'match':5} org")
    for r in rows:
        print(f"{r['label']:9} {r['at']:19} {str(r['id_token']):8} {str(r['sub']):>12} "
              f"{str(r['userId']):>12} {str(r['match']):5} {r['org']}")

    checks = {}
    for label in LABELS:
        runs = [r for r in rows if r["label"] == label]
        tokens = {r["obtained_at"] for r in runs}  # distinct exchanges, not re-checks of one token
        subs = {r["sub"] for r in runs}
        checks[f"{label}: has sub"] = bool(runs) and all(r["sub"] is not None for r in runs)
        checks[f"{label}: sub == live userId"] = bool(runs) and all(r["match"] for r in runs)
        checks[f"{label}: sub stable over 2+ exchanges"] = len(tokens) >= 2 and len(subs) == 1

    print()
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print("\nclaim names seen:", sorted({c for r in rows for c in r["claims"]}))
    if all(checks.values()):
        print("\nDECISION: option A. Use id_token `sub` as external_user_id (read once at connect).")
    else:
        print("\nDECISION: not A yet. Fix the FAIL lines (or fall back to option C: live /auth/me userId).")
    print("Paste this whole report into DEV-48 (no secrets in it).")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        cmd_check(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "report":
        cmd_report()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
