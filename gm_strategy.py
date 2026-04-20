import os
import time
import requests

API_KEY = "PKO4M5DUHN66L3JOBLMF7TZDAU"
SECRET_KEY = "DwRya1msaYbzNz6CzTUFX5sGSsvbP2CHx4aCFtym9qNj"
BASE_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"
SYMBOL = "GM"
INITIAL_QTY = 10

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
    "Content-Type": "application/json",
}


def api(method, path, **kwargs):
    url = BASE_URL + path
    r = requests.request(method, url, headers=HEADERS, **kwargs)
    r.raise_for_status()
    return r.json() if r.text else {}


def get_latest_price(symbol):
    r = requests.get(
        f"{DATA_URL}/stocks/{symbol}/trades/latest",
        headers=HEADERS,
    )
    r.raise_for_status()
    return float(r.json()["trade"]["p"])


def place_order(symbol, qty, side, order_type, **kwargs):
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": kwargs.pop("time_in_force", "gtc"),
    }
    payload.update({k: str(v) for k, v in kwargs.items()})
    return api("POST", "/orders", json=payload)


def cancel_order(order_id):
    try:
        api("DELETE", f"/orders/{order_id}")
    except Exception:
        pass


def print_summary(order, label):
    sep = "=" * 52
    print(f"\n{sep}")
    print(f"  {label}")
    print(sep)
    print(f"  Symbol : {order.get('symbol')}")
    print(f"  Side   : {order.get('side').upper()}")
    print(f"  Qty    : {order.get('qty')}")
    print(f"  Type   : {order.get('type')}")
    print(f"  Status : {order.get('status')}")
    if order.get("stop_price"):
        print(f"  Stop   : ${float(order['stop_price']):.2f}")
    if order.get("limit_price"):
        print(f"  Limit  : ${float(order['limit_price']):.2f}")
    if order.get("trail_percent"):
        print(f"  Trail  : {order['trail_percent']}%")
    print(f"  ID     : {order.get('id')}")
    print(sep)


# ── Step 1: Initial market buy ──────────────────────────────────────────────
print(f"\nPlacing market buy for {INITIAL_QTY} shares of {SYMBOL}...")
buy_order = place_order(SYMBOL, INITIAL_QTY, "buy", "market", time_in_force="day")
print_summary(buy_order, f"INITIAL BUY — {INITIAL_QTY} shares {SYMBOL}")

# Wait for fill (up to 60 s)
order_id = buy_order["id"]
fill_price = None
print("\nWaiting for fill", end="", flush=True)
for _ in range(30):
    o = api("GET", f"/orders/{order_id}")
    if o["status"] == "filled":
        fill_price = float(o["filled_avg_price"])
        print(f" filled @ ${fill_price:.2f}")
        break
    print(".", end="", flush=True)
    time.sleep(2)

if not fill_price:
    fill_price = get_latest_price(SYMBOL)
    print(f"\nMarket likely closed — using latest trade price: ${fill_price:.2f}")

# ── Price levels ─────────────────────────────────────────────────────────────
stop_loss_price   = round(fill_price * 0.90, 2)   # hard floor   −10%
trail_trigger     = round(fill_price * 1.10, 2)   # trailing activates +10%
ladder_l1_price   = round(fill_price * 0.80, 2)   # L1 −20%  20 shares
ladder_l2_price   = round(fill_price * 0.75, 2)   # L2 −25%  30 shares
ladder_l3_price   = round(fill_price * 0.70, 2)   # L3 −30%  40 shares

print(f"""
Price Levels
────────────
  Purchase price          : ${fill_price:.2f}
  Hard stop-loss  (−10%)  : ${stop_loss_price:.2f}
  Trailing trigger (+10%) : ${trail_trigger:.2f}
  L1 ladder buy   (−20%)  : ${ladder_l1_price:.2f}  × 20 shares
  L2 ladder buy   (−25%)  : ${ladder_l2_price:.2f}  × 30 shares
  L3 ladder buy   (−30%)  : ${ladder_l3_price:.2f}  × 40 shares
""")

# ── Step 2: Hard stop-loss at −10% ──────────────────────────────────────────
stop_order = place_order(SYMBOL, INITIAL_QTY, "sell", "stop",
                         stop_price=stop_loss_price)
print_summary(stop_order, f"STOP-LOSS — sell {INITIAL_QTY} @ ${stop_loss_price:.2f} (−10%)")
stop_order_id = stop_order["id"]
current_stop = stop_loss_price

# ── Step 3: Ladder buys at −20%, −25%, −30% ─────────────────────────────────
for label, qty, price in [
    ("L1 −20%", 20, ladder_l1_price),
    ("L2 −25%", 30, ladder_l2_price),
    ("L3 −30%", 40, ladder_l3_price),
]:
    order = place_order(SYMBOL, qty, "buy", "limit", limit_price=price)
    print_summary(order, f"LADDER BUY {label} — {qty} shares @ ${price:.2f}")

# ── Step 4: Monitor & manage trailing stop ───────────────────────────────────
print("""
Monitoring started. Rules:
  • Price below stop → stop-loss triggers (handled by Alpaca)
  • Price reaches +10% → switch to trailing mode (5% below peak)
  • Trailing floor only moves UP, never down
  • Press Ctrl+C to stop monitoring
""")

peak_price = fill_price
trailing_active = False
CHECK_INTERVAL = 30  # seconds

try:
    while True:
        try:
            price = get_latest_price(SYMBOL)
        except Exception as e:
            print(f"  Price fetch error: {e}")
            time.sleep(CHECK_INTERVAL)
            continue

        pct_from_buy = (price - fill_price) / fill_price * 100
        print(f"  [{time.strftime('%H:%M:%S')}] {SYMBOL}: ${price:.2f}  "
              f"({pct_from_buy:+.2f}% from buy)  "
              f"Stop: ${current_stop:.2f}  "
              f"{'[TRAILING]' if trailing_active else '[HARD STOP]'}")

        # Activate trailing once price hits +10%
        if not trailing_active and price >= trail_trigger:
            trailing_active = True
            print(f"\n  *** Trailing mode activated at ${price:.2f} ***\n")

        if trailing_active:
            # Update peak
            if price > peak_price:
                peak_price = price

            # New stop = 5% below peak (only move up)
            new_stop = round(peak_price * 0.95, 2)
            if new_stop > current_stop:
                print(f"  ↑ Raising stop: ${current_stop:.2f} → ${new_stop:.2f} "
                      f"(5% below peak ${peak_price:.2f})")
                cancel_order(stop_order_id)
                new_stop_order = place_order(SYMBOL, INITIAL_QTY, "sell", "stop",
                                             stop_price=new_stop)
                print_summary(new_stop_order,
                              f"UPDATED STOP — sell {INITIAL_QTY} @ ${new_stop:.2f}")
                stop_order_id = new_stop_order["id"]
                current_stop = new_stop

        time.sleep(CHECK_INTERVAL)

except KeyboardInterrupt:
    print("\n\nMonitoring stopped by user.")
    print(f"Active stop-loss order ID: {stop_order_id} @ ${current_stop:.2f}")
    print("Orders remain active in your Alpaca account.")
