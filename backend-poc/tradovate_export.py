#!/usr/bin/env python3
"""Export the current session's trades from a DEMO Tradovate login to CSV + raw JSON (read-only).

Run right after `tradovate_oauth.py exchange`:

  python3 backend-poc/tradovate_export.py            # writes backend-poc/exports/<timestamp>/

Files (they contain your real trades, prices and account names; keep them local, never commit):
  trades.csv     one row per closed trade (fill pair): account, contract, side, qty, entry/exit time
                 and price, P&L in points and in currency (before fees), order types
  fills.csv      every fill with its order, account and contract
  orders.csv     every order with its latest version (type, qty, price, stop, time in force)
  raw/*.json     the untouched API responses, as fixtures for building the backend parser

Tradovate's list endpoints only return the CURRENT trading session (CME day: 18:00 New York to 17:00
New York, i.e. 23:00 to 22:00 Lisbon in winter). Older trades are not available here; run this each
session to keep them. Prints only counts, the date range and the output folder.
"""
import csv, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradovate_oauth import API, http, load_token  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parent / "exports"
RAW = ["account", "cashBalance", "position", "fillPair", "fill", "order", "orderVersion",
       "executionReport", "command", "orderStrategy"]


def get(h, path):
    st, body = http(API + path, headers=h)
    return body if st == 200 else None, st


def lookup(h, cache, entity, ident):
    """GET /<entity>/item?id=..., cached. Returns {} when it fails (column left blank)."""
    if ident is None:
        return {}
    key = (entity, ident)
    if key not in cache:
        body, _ = get(h, f"/{entity}/item?id={ident}")
        cache[key] = body if isinstance(body, dict) else {}
    return cache[key]


def contract_info(h, cache, contract_id):
    """contract -> contractMaturity -> product, for the symbol and the currency value of 1 point."""
    c = lookup(h, cache, "contract", contract_id)
    m = lookup(h, cache, "contractMaturity", c.get("contractMaturityId"))
    p = lookup(h, cache, "product", m.get("productId"))
    return {"symbol": c.get("name", ""), "product": p.get("name", ""), "value_per_point": p.get("valuePerPoint")}


def write_csv(path, rows, cols):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def fmt_date(d):
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}" if isinstance(d, dict) and "year" in d else ""


def build(data, h, cache):
    """Join the raw lists into trades / fills / orders rows."""
    accounts = {a["id"]: a for a in data["account"]}
    orders = {o["id"]: o for o in data["order"]}
    fills = {f["id"]: f for f in data["fill"]}
    latest_version = {}
    for v in sorted(data["orderVersion"], key=lambda v: v["id"]):
        latest_version[v["orderId"]] = v  # last one wins: the order's current terms

    def account_of(order_id):
        a = accounts.get(orders.get(order_id, {}).get("accountId"), {})
        return a.get("id", ""), a.get("name", "")

    order_rows = []
    for o in data["order"]:
        v = latest_version.get(o["id"], {})
        ci = contract_info(h, cache, o.get("contractId"))
        order_rows.append({
            "order_id": o["id"], "account_id": o.get("accountId"), "account": accounts.get(o.get("accountId"), {}).get("name", ""),
            "symbol": ci["symbol"], "contract_id": o.get("contractId"), "action": o.get("action"),
            "status": o.get("ordStatus"), "type": v.get("orderType", ""), "qty": v.get("orderQty", ""),
            "price": v.get("price", ""), "stop_price": v.get("stopPrice", ""), "time_in_force": v.get("timeInForce", ""),
            "oco_id": o.get("ocoId", ""), "created": o.get("timestamp"),
        })

    fill_rows = []
    for f in data["fill"]:
        acc_id, acc = account_of(f.get("orderId"))
        ci = contract_info(h, cache, f.get("contractId"))
        fill_rows.append({
            "fill_id": f["id"], "order_id": f.get("orderId"), "account_id": acc_id, "account": acc,
            "symbol": ci["symbol"], "contract_id": f.get("contractId"), "action": f.get("action"),
            "qty": f.get("qty"), "price": f.get("price"), "time": f.get("timestamp"), "trade_date": fmt_date(f.get("tradeDate")),
        })

    trade_rows = []
    for p in data["fillPair"]:
        buy, sell = fills.get(p.get("buyFillId"), {}), fills.get(p.get("sellFillId"), {})
        long_ = (buy.get("timestamp") or "") <= (sell.get("timestamp") or "~")
        entry, exit_ = (buy, sell) if long_ else (sell, buy)
        acc_id, acc = account_of(entry.get("orderId") or exit_.get("orderId"))
        ci = contract_info(h, cache, entry.get("contractId") or exit_.get("contractId"))
        qty = p.get("qty") or 0
        pts = (p["sellPrice"] - p["buyPrice"]) if p.get("sellPrice") is not None and p.get("buyPrice") is not None else None
        vpp = ci["value_per_point"]
        trade_rows.append({
            "trade_id": p["id"], "account_id": acc_id, "account": acc, "symbol": ci["symbol"], "product": ci["product"],
            "side": "Long" if long_ else "Short", "qty": qty,
            "entry_time": entry.get("timestamp", ""), "entry_price": entry.get("price", ""),
            "exit_time": exit_.get("timestamp", ""), "exit_price": exit_.get("price", ""),
            "pnl_points": round(pts, 6) if pts is not None else "",
            "pnl_currency": round(pts * qty * vpp, 2) if pts is not None and vpp else "",
            "entry_order_type": latest_version.get(entry.get("orderId"), {}).get("orderType", ""),
            "exit_order_type": latest_version.get(exit_.get("orderId"), {}).get("orderType", ""),
            "trade_date": fmt_date(exit_.get("tradeDate") or entry.get("tradeDate")),
            "buy_fill_id": p.get("buyFillId"), "sell_fill_id": p.get("sellFillId"), "position_id": p.get("positionId"),
        })
    trade_rows.sort(key=lambda r: r["entry_time"] or "")
    return trade_rows, fill_rows, order_rows


def main():
    tok = load_token()
    age = time.time() - tok.get("obtained_at", 0)
    if tok.get("expires_in") and age > tok["expires_in"]:
        sys.exit(f"token is {age/60:.0f} min old and expired; run authorize + exchange again")
    h = {"Authorization": f"Bearer {tok['access_token']}"}

    data = {}
    for entity in RAW:
        body, st = get(h, f"/{entity}/list")
        if not isinstance(body, list):
            sys.exit(f"/{entity}/list failed: HTTP {st}")
        data[entity] = body

    out = OUT_ROOT / time.strftime("%Y%m%d-%H%M%S")
    (out / "raw").mkdir(parents=True)
    for entity, body in data.items():
        (out / "raw" / f"{entity}.json").write_text(json.dumps(body, indent=2))

    cache = {}
    trades, fills, orders = build(data, h, cache)
    for c, body in cache.items():  # contract/product lookups, also useful as fixtures
        if body:
            (out / "raw" / f"{c[0]}-{c[1]}.json").write_text(json.dumps(body, indent=2))

    write_csv(out / "trades.csv", trades, list(trades[0]) if trades else ["trade_id"])
    write_csv(out / "fills.csv", fills, list(fills[0]) if fills else ["fill_id"])
    write_csv(out / "orders.csv", orders, list(orders[0]) if orders else ["order_id"])

    times = [t["entry_time"] for t in trades if t["entry_time"]]
    blank = sum(1 for t in trades if t["pnl_currency"] == "")
    print(f"{len(trades)} trade(s), {len(fills)} fill(s), {len(orders)} order(s), {len(data['account'])} account(s)")
    if times:
        print(f"trades from {min(times)} to {max(times)}")
    if blank:
        print(f"note: {blank} trade(s) without currency P&L (contract/product lookup failed; points are still there)")
    print(f"written to {out}  (contains real trade data: keep it local)")


if __name__ == "__main__":
    main()
