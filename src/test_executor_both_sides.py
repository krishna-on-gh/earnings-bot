"""
test_executor_both_sides.py — end-to-end smoke test of the executor for BOTH
call and put paths, without placing real orders.

For each side (call, put) it exercises the EXACT functions the live executor uses:
  1. get_current_price()                  — yfinance underlying price
  2. find_expiry_after_earnings()         — date helper
  3. find_option_contract()               — Alpaca option contract lookup
  4. get_option_ask_price()               — Alpaca live option quote
  5. Construct a LimitOrderRequest        — same builder the executor calls
  6. STOP before client.submit_order()    — does NOT send to broker

If any step fails, the bug is caught here instead of on a real candidate.

USAGE
-----
    python src/test_executor_both_sides.py
    python src/test_executor_both_sides.py AAPL    # use specific underlying
"""

import os
import sys
from datetime import date, timedelta

SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)

# Load .env manually (no python-dotenv)
_env_path = os.path.join(SRC, "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, ContractType

from executor import (
    get_current_price,
    find_expiry_after_earnings,
    find_option_contract,
    get_option_ask_price,
)
from utils import get_trading_client


def test_side(symbol: str, side: str, fake_earnings_date: date) -> dict:
    """Exercise full executor path for one side. Returns a result dict."""
    print(f"\n{'='*70}")
    print(f"  TESTING {side.upper()} PATH | underlying={symbol}")
    print(f"{'='*70}")

    result = {"side": side, "symbol": symbol, "passed": False, "steps": {}}
    client = get_trading_client()

    # Step 1: underlying price
    print(f"  [1/5] Fetching current price for {symbol}...")
    price = get_current_price(symbol)
    if not price or price <= 0:
        print(f"    FAIL: could not fetch price")
        result["steps"]["price"] = {"ok": False, "error": "could not fetch price"}
        return result
    print(f"    OK: ${price:.2f}")
    result["steps"]["price"] = {"ok": True, "value": price}

    # Step 2: expiry
    print(f"  [2/5] Computing expiry after earnings {fake_earnings_date}...")
    expiry = find_expiry_after_earnings(fake_earnings_date)
    print(f"    OK: {expiry}")
    result["steps"]["expiry"] = {"ok": True, "value": str(expiry)}

    # Step 3: contract lookup
    print(f"  [3/5] Looking up {side.upper()} contract at strike ~${round(price)} for expiry {expiry}...")
    contract_type = ContractType.PUT if side == "put" else ContractType.CALL
    contract = find_option_contract(client, symbol, round(price), expiry, contract_type)
    if not contract:
        print(f"    FAIL: no {side} contract found near ATM. This is a real problem.")
        result["steps"]["contract"] = {"ok": False, "error": f"no {side} contract found"}
        return result
    print(f"    OK: {contract.symbol} (strike=${float(contract.strike_price):.2f}, expiry={contract.expiration_date})")
    result["steps"]["contract"] = {
        "ok": True, "symbol": contract.symbol,
        "strike": float(contract.strike_price), "expiry": str(contract.expiration_date),
    }

    # Step 4: option ask price
    print(f"  [4/5] Fetching live ask for {contract.symbol}...")
    ask = get_option_ask_price(contract.symbol)
    if ask is None or ask <= 0:
        print(f"    WARN: no live ask available — executor would fall back to stale close_price")
        result["steps"]["ask"] = {"ok": False, "warning": "no live ask (executor will fall back)"}
        # This is a soft fail; the executor handles it via stale price fallback. Continue.
        ask = 1.00  # placeholder for downstream test
    else:
        print(f"    OK: ${ask:.2f}")
        result["steps"]["ask"] = {"ok": True, "value": ask}

    # Step 5: construct order request (DO NOT SUBMIT)
    print(f"  [5/5] Constructing LimitOrderRequest (NOT submitting)...")
    try:
        num_contracts = max(1, int(10000 / (ask * 100)))
        req = LimitOrderRequest(
            symbol=contract.symbol,
            qty=num_contracts,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=ask,
        )
        print(f"    OK: would BUY {num_contracts} {contract.symbol} @ ${ask:.2f} limit")
        result["steps"]["order_request"] = {
            "ok": True, "qty": num_contracts, "limit_price": ask,
            "total_cost": round(num_contracts * ask * 100, 2),
        }
    except Exception as e:
        print(f"    FAIL: order request construction errored: {e}")
        result["steps"]["order_request"] = {"ok": False, "error": str(e)}
        return result

    result["passed"] = True
    print(f"\n  [OK] {side.upper()} path fully functional.")
    return result


def main():
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

    # Use an earnings date ~2 weeks out so the expiry-after-earnings logic
    # finds a real, currently-trading weekly. Doesn't have to be a real earnings.
    fake_earnings = date.today() + timedelta(days=14)

    print(f"\n{'#'*70}")
    print(f"  EXECUTOR BOTH-SIDES SMOKE TEST")
    print(f"  Underlying: {symbol}")
    print(f"  Pseudo-earnings date: {fake_earnings} (used only to derive expiry)")
    print(f"  WILL NOT submit any orders. Read-only API calls + dry construction.")
    print(f"{'#'*70}")

    results = []
    for side in ("call", "put"):
        results.append(test_side(symbol, side, fake_earnings))

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  {r['side'].upper():<5}  {status}")
        for step, info in r["steps"].items():
            if not info.get("ok"):
                msg = info.get("error") or info.get("warning") or "?"
                print(f"     -> {step}: {msg}")

    all_passed = all(r["passed"] for r in results)
    print()
    if all_passed:
        print("  RESULT: both call and put paths verified end-to-end.")
        print("  The executor is ready to fire real trades when validated candidates arrive.")
    else:
        print("  RESULT: at least one side failed. See above for the specific step.")
        sys.exit(1)


if __name__ == "__main__":
    main()
