#!/usr/bin/env python3
"""Does a Tradovate DEMO WebSocket survive an access-token refresh? (open question T1)

Read-only: places no orders. DEMO ONLY. Needs: pip install websocket-client==1.8.0
Uses the token saved by tradovate_oauth.py (run authorize + exchange first).

  python3 backend-poc/tradovate_ws_token_test.py keep     # refresh, keep the socket on the OLD token
  python3 backend-poc/tradovate_ws_token_test.py reauth   # refresh, send the NEW token on the same socket

Both modes:
  1. connect, authorize with the saved token T0, user/syncrequest
  2. refresh (T0 -> T1, saved like `tradovate_oauth.py refresh`), then check with one REST call
     whether T0 still works (does a refresh revoke the old access token?)
  3. reauth mode only: send `authorize` with T1 on the open socket
  4. heartbeat every 2.5 s; every 2 min an `account/list` request over the socket
  5. run until 15 min after T0 would have expired, or until the socket dies

During the run you can place and close a 1-lot demo trade (in the Tradovate app) after T0's
expiry to see whether fills still arrive. To test the 2-session limit (T4), open the
Tradovate web trader and the phone app while it runs and note who gets kicked.

Never prints tokens or account names: only statuses, counts and entity types.
The log is written to ~/.cache/copytrader/ws_token_test_<mode>_<time>.log.
"""
import base64, json, sys, time
from pathlib import Path

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("pip install websocket-client==1.8.0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradovate_oauth import API, CACHE, http, load_token, store_token, token_request  # noqa: E402

WS_URL = "wss://demo.tradovateapi.com/v1/websocket"  # demo only, on purpose
HEARTBEAT_S = 2.5
DEAD_AFTER_S = 10
PROBE_EVERY_S = 120
RUN_PAST_EXPIRY_S = 15 * 60

LOG = None
T_START = time.monotonic()


def log(msg):
    wall = time.strftime("%H:%M:%S")
    line = f"{wall} +{(time.monotonic() - T_START) / 60:5.1f}m  {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def user_id(tok):
    """Tradovate userId = the id_token `sub` claim (see dev48_userid_check.py)."""
    payload = tok["id_token"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return int(json.loads(base64.urlsafe_b64decode(payload))["sub"])


def expires_at(tok):
    return tok["obtained_at"] + tok["expires_in"]


def mins(seconds):
    return f"{seconds / 60:+.1f} min"


class Socket:
    def __init__(self):
        self.ws = websocket.create_connection(WS_URL, timeout=10, enable_multithread=False)
        self.ws.settimeout(0.5)
        self.next_id = 1
        self.pending = {}  # request id -> label
        self.last_rx = time.monotonic()
        self.last_hb = 0.0
        self.props = {}  # "entityType/eventType" -> count

    def request(self, endpoint, body="", label=None):
        i = self.next_id
        self.next_id += 1
        self.pending[i] = label or endpoint
        self.ws.send(f"{endpoint}\n{i}\n\n{body}")
        return i

    def heartbeat(self):
        if time.monotonic() - self.last_hb >= HEARTBEAT_S:
            self.ws.send("[]")
            self.last_hb = time.monotonic()

    def poll(self):
        """Read at most one frame. Returns a list of (kind, info) tuples."""
        try:
            frame = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return []
        self.last_rx = time.monotonic()
        if not frame:
            return [("closed", "empty frame")]
        kind = frame[0]
        if kind in "oh":
            return [("open" if kind == "o" else "heartbeat", "")]
        if kind == "c":
            return [("closed", frame[1:200])]  # close frame: [code, "reason"], no secrets
        if kind != "a":
            return [("unknown", frame[:1])]
        out = []
        for msg in json.loads(frame[1:]):
            if "i" in msg:
                label = self.pending.pop(msg["i"], "?")
                d = msg.get("d")
                size = len(d) if isinstance(d, (list, dict, str)) else "-"
                out.append(("response", f"{label}: s={msg.get('s')} size={size}"))
            elif msg.get("e") == "props":
                d = msg.get("d", {})
                key = f"{d.get('entityType')}/{d.get('eventType')}"
                self.props[key] = self.props.get(key, 0) + 1
                out.append(("props", key))
            else:
                out.append(("event", str(msg.get("e"))))
        return out


def refresh():
    """refresh_token grant, saved like `tradovate_oauth.py refresh`. Returns the new token dict."""
    old = load_token()
    style, status, res = token_request({"grant_type": "refresh_token", "refresh_token": old["refresh_token"]})
    if not (isinstance(res, dict) and res.get("access_token")):
        log(f"REFRESH FAILED: HTTP {status}")
        return None
    if not res.get("refresh_token"):
        res["refresh_token"] = old["refresh_token"]
    store_token(style, res)
    new = load_token()
    log(f"refreshed: expires_in={new.get('expires_in')} "
        f"refresh_token_expires_in={new.get('refresh_token_expires_in')} "
        f"refresh_token_rotated={new['refresh_token'] != old['refresh_token']}")
    return new


def rest_status(token):
    status, _ = http(API + "/account/list", headers={"Authorization": f"Bearer {token}"})
    return status


def run(mode):
    t0 = load_token()
    left = expires_at(t0) - time.time()
    if left < 5 * 60:
        sys.exit(f"saved token has {left / 60:.1f} min left; run `tradovate_oauth.py refresh` first")
    log(f"mode={mode}; T0 expires in {mins(left)}; the run ends {RUN_PAST_EXPIRY_S // 60} min after that")

    s = Socket()
    first = s.poll()
    while not first and time.monotonic() - s.last_rx < DEAD_AFTER_S:
        first = s.poll()
    if first[:1] != [("open", "")]:
        return summary(s, t0, f"no open frame from server: {first[:1]}")
    log("connected")
    s.request("authorize", t0["access_token"], label="authorize(T0)")
    s.request("user/syncrequest", json.dumps({"users": [user_id(t0)]}), label="user/syncrequest")

    refreshed = False
    last_probe = time.monotonic()
    end_at = expires_at(t0) + RUN_PAST_EXPIRY_S
    while time.time() < end_at:
        s.heartbeat()
        for kind, info in s.poll():
            if kind == "heartbeat":
                continue
            when = mins(time.time() - expires_at(t0))
            log(f"{kind:9} {info}   (T0 expiry {when})")
            if kind == "closed":
                return summary(s, t0, f"socket closed by server: {info}")
            if kind == "response" and info.startswith("user/syncrequest") and not refreshed:
                refreshed = True
                t1 = refresh()
                if t1 is None:
                    return summary(s, t0, "refresh failed")
                log(f"REST with old T0 after refresh: HTTP {rest_status(t0['access_token'])}")
                s.last_rx = time.monotonic()  # the HTTP calls above paused reading
                if mode == "reauth":
                    s.request("authorize", t1["access_token"], label="authorize(T1)")
        if time.monotonic() - s.last_rx > DEAD_AFTER_S:
            return summary(s, t0, f"no frame for {DEAD_AFTER_S} s: socket dead")
        if time.monotonic() - last_probe >= PROBE_EVERY_S:
            last_probe = time.monotonic()
            s.request("account/list", label="probe account/list")
    return summary(s, t0, "survived 15 min past T0 expiry")


def summary(s, t0, outcome):
    log(f"RESULT: {outcome}; T0 expiry {mins(time.time() - expires_at(t0))}")
    log(f"props received: {json.dumps(s.props, sort_keys=True)}")
    try:
        s.ws.close()
    except Exception:  # noqa: BLE001 - closing a dead socket is best-effort
        pass


def main():
    global LOG
    if len(sys.argv) != 2 or sys.argv[1] not in ("keep", "reauth"):
        sys.exit(__doc__)
    mode = sys.argv[1]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"ws_token_test_{mode}_{time.strftime('%Y%m%d-%H%M%S')}.log"
    with path.open("w") as LOG:
        try:
            run(mode)
        except (websocket.WebSocketException, OSError) as e:
            log(f"RESULT: socket error {type(e).__name__}")
        except KeyboardInterrupt:
            log("stopped by hand")
    print(f"\nlog: {path}")


if __name__ == "__main__":
    main()
