"""
Trade executor — Hitman v2 methodology.
Runs at 3:00 PM ET weekdays (1 hour before close — more time for limit fills).

For each validated Hitman v2 candidate:
  Always buy ATM naked call ($10K position).
  Spreads were removed — historical data showed 75% of winning moves exceed +8%,
  capping upside contradicts the model's thesis of finding big movers.

Entry:  close, 3 trading days before earnings  (this script runs at 3:50 PM)
Exit:   handled by monitor.py at close of day +1 after earnings
"""
import time
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple

import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetOptionContractsRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, ContractType
from alpaca.data.requests import OptionLatestQuoteRequest

from utils import (
    get_logger, load_settings, load_json, save_json,
    get_trading_client, get_option_data_client, send_email, check_kill_switch,
    check_circuit_breakers, check_budget, update_budget,
    now_et, today_et, is_weekday, is_market_open,
)

log = get_logger("executor")

POSITION_USD_DEFAULT = 10_000   # fallback if no confidence tier in recommendation

# Confidence-tier position sizes (set by build_universe.py / screener)
POSITION_TIERS = {
    "HIGH":   10_000,   # reliable sector, HV < 70%
    "MEDIUM":  5_000,   # biotech/financial OR HV > 70%
    "LOW":     1_000,   # biotech/financial AND HV > 70% (stacked risk)
}

# US market holidays 2026 (NYSE observed dates)
HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 5, 25), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}


def get_entry_date(earnings_date: date) -> date:
    """Return the intended entry date: 3 trading days before earnings."""
    d = earnings_date
    count = 0
    while count < 3:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d not in HOLIDAYS_2026:
            count += 1
    return d


# ── Live option quote ─────────────────────────────────────────────────────────
def get_option_ask_price(contract_symbol: str) -> Optional[float]:
    """
    Fetch live ask price for an option contract.
    We use ask (not mid) as the limit price to guarantee fills.
    Mid-price limits frequently go unfilled — market makers sit at the ask.
    Falls back to None if unavailable (caller uses stale close_price instead).
    """
    try:
        data_client = get_option_data_client()
        req = OptionLatestQuoteRequest(symbol_or_symbols=[contract_symbol])
        quotes = data_client.get_option_latest_quote(req)
        quote = quotes.get(contract_symbol)
        if not quote:
            return None
        bid = float(quote.bid_price or 0)
        ask = float(quote.ask_price or 0)
        if ask <= 0:
            return None
        mid = round((bid + ask) / 2, 2)
        log.info(f"{contract_symbol}: bid=${bid:.2f} ask=${ask:.2f} mid=${mid:.2f} — using ask as limit")
        return round(ask, 2)
    except Exception as e:
        log.warning(f"Could not fetch live quote for {contract_symbol}: {e}")
        return None


# ── Price fetching ────────────────────────────────────────────────────────────
def get_current_price(symbol: str) -> Optional[float]:
    """Get latest stock price via yfinance."""
    try:
        info = yf.Ticker(symbol).fast_info
        price = getattr(info, "last_price", None)
        if price and float(price) > 0:
            return float(price)
    except Exception:
        pass
    try:
        hist = yf.Ticker(symbol).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


# ── Option contract selection ─────────────────────────────────────────────────
def find_expiry_after_earnings(earnings_date: date) -> date:
    """
    Find the nearest options expiration date that falls AFTER earnings.
    Options typically expire on Fridays. We want the first Friday
    at least 1 day after earnings, ideally 3-7 days after.
    """
    # Start looking from the day after earnings
    target = earnings_date + timedelta(days=1)
    # Walk forward to find the next Friday (weekday 4)
    while target.weekday() != 4:
        target += timedelta(days=1)
    # If that Friday is less than 2 days after earnings, take the following Friday
    if (target - earnings_date).days < 2:
        target += timedelta(days=7)
    return target


def find_option_contract(
    client: TradingClient,
    symbol: str,
    strike: float,
    expiry: date,
    contract_type: ContractType = ContractType.CALL,
    tolerance_pct: float = 0.05,
) -> Optional[Any]:
    """
    Find the closest available option contract to the requested strike/expiry.
    Searches within ±tolerance_pct of strike (default ±5%).
    Returns the contract object or None if not found.
    """
    strike_low  = round(strike * (1 - tolerance_pct), 2)
    strike_high = round(strike * (1 + tolerance_pct), 2)

    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            contract_type=contract_type,
            expiration_date_gte=expiry - timedelta(days=3),
            expiration_date_lte=expiry + timedelta(days=3),
            strike_price_gte=str(strike_low),
            strike_price_lte=str(strike_high),
            limit=20,
        )
        contracts = client.get_option_contracts(req)
        if not contracts or not contracts.option_contracts:
            log.warning(f"{symbol}: no option contracts found near strike ${strike:.2f} expiry {expiry}")
            return None

        # Pick the contract with strike closest to target
        best = min(
            contracts.option_contracts,
            key=lambda c: abs(float(c.strike_price) - strike)
        )
        log.info(
            f"{symbol}: found contract {best.symbol} | "
            f"strike=${float(best.strike_price):.2f} | expiry={best.expiration_date}"
        )
        return best

    except Exception as e:
        log.error(f"{symbol}: contract lookup failed — {e}")
        return None


# ── Order placement ───────────────────────────────────────────────────────────
def place_call_order(
    symbol: str,
    contract: Any,
    num_contracts: int,
    limit_price: Optional[float],
    client: TradingClient,
) -> Optional[Dict[str, Any]]:
    """
    Place a naked call buy order.
    Uses limit order at mid-price if available, falls back to market order.
    """
    try:
        if limit_price:
            order_data = LimitOrderRequest(
                symbol=contract.symbol,
                qty=num_contracts,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
            )
            order_type = f"LIMIT @ ${limit_price:.2f}"
        else:
            order_data = MarketOrderRequest(
                symbol=contract.symbol,
                qty=num_contracts,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order_type = "MARKET"

        order = client.submit_order(order_data)
        log.info(
            f"{symbol}: CALL {order_type} submitted — {num_contracts} contracts "
            f"of {contract.symbol} | order_id={order.id}"
        )
        return {
            "order_id":     str(order.id),
            "order_status": str(order.status),
            "contract":     contract.symbol,
            "strike":       float(contract.strike_price),
            "expiry":       str(contract.expiration_date),
            "qty":          num_contracts,
            "limit_price":  limit_price,
            "leg":          "call",
        }
    except Exception as e:
        log.error(f"{symbol}: call order failed — {e}")
        send_email(f"Order Failed: {symbol} CALL", f"Failed to place call order.\nError: {e}")
        return None


def place_spread_order(
    symbol: str,
    long_contract: Any,
    short_contract: Any,
    num_contracts: int,
    client: TradingClient,
) -> Optional[Dict[str, Any]]:
    """
    Place a bull call spread: buy ATM call + sell +8% OTM call.
    Two separate orders placed back-to-back.
    """
    # Leg 1: Buy the long (ATM) call
    try:
        long_order_data = MarketOrderRequest(
            symbol=long_contract.symbol,
            qty=num_contracts,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        long_order = client.submit_order(long_order_data)
        log.info(f"{symbol}: SPREAD long leg submitted — {long_contract.symbol} | order_id={long_order.id}")
    except Exception as e:
        log.error(f"{symbol}: spread long leg failed — {e}")
        send_email(f"Order Failed: {symbol} SPREAD long leg", f"Error: {e}")
        return None

    time.sleep(0.5)

    # Leg 2: Sell the short (OTM) call
    try:
        short_order_data = MarketOrderRequest(
            symbol=short_contract.symbol,
            qty=num_contracts,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        short_order = client.submit_order(short_order_data)
        log.info(f"{symbol}: SPREAD short leg submitted — {short_contract.symbol} | order_id={short_order.id}")
    except Exception as e:
        log.error(f"{symbol}: spread short leg failed — {e}")
        send_email(f"Order Failed: {symbol} SPREAD short leg", f"Long leg was placed but short leg failed.\nError: {e}")
        # Return partial info so monitor knows long leg exists
        return {
            "order_id":         str(long_order.id),
            "order_status":     str(long_order.status),
            "long_contract":    long_contract.symbol,
            "short_contract":   None,
            "long_strike":      float(long_contract.strike_price),
            "short_strike":     None,
            "expiry":           str(long_contract.expiration_date),
            "qty":              num_contracts,
            "leg":              "spread_partial",
            "error":            f"short leg failed: {e}",
        }

    return {
        "order_id":         str(long_order.id),
        "short_order_id":   str(short_order.id),
        "order_status":     str(long_order.status),
        "long_contract":    long_contract.symbol,
        "short_contract":   short_contract.symbol,
        "long_strike":      float(long_contract.strike_price),
        "short_strike":     float(short_contract.strike_price),
        "expiry":           str(long_contract.expiration_date),
        "qty":              num_contracts,
        "leg":              "spread",
    }


# ── Trade execution ───────────────────────────────────────────────────────────
def execute_trade(rec: Dict[str, Any], client: TradingClient) -> Optional[Dict[str, Any]]:
    symbol     = rec["symbol"]
    trade_type = rec.get("trade_type", "call")   # "call" or "spread"
    iv         = rec.get("iv_proxy", 0.50)
    earnings_date = date.fromisoformat(rec["earnings_date"])

    # Confidence-tier position size — read from recommendation, fall back to default
    confidence  = rec.get("confidence", "HIGH")
    position_usd = rec.get("position_size") or POSITION_TIERS.get(confidence, POSITION_USD_DEFAULT)
    log.info(f"{symbol}: confidence={confidence} position=${position_usd:,}")

    # Budget check
    can_trade, reason = check_budget(position_usd, log)
    if not can_trade:
        log.warning(f"{symbol}: skipping — {reason}")
        return None

    # Get current stock price
    price = get_current_price(symbol)
    if not price or price <= 0:
        log.error(f"{symbol}: could not get price — skipping")
        return None

    # Always naked ATM call — no spread cap, let winners run
    expiry = find_expiry_after_earnings(earnings_date)
    log.info(f"{symbol}: price=${price:.2f} | IV={iv:.0%} | expiry={expiry}")

    atm_strike = round(price)

    contract = find_option_contract(client, symbol, atm_strike, expiry, ContractType.CALL)
    if not contract:
        log.warning(f"{symbol}: no ATM call contract found — skipping")
        return None

    # Fetch live ask price — used as limit to guarantee fill (mid-price limits go unfilled)
    live_ask = get_option_ask_price(contract.symbol)
    if live_ask:
        premium_est = live_ask
        log.info(f"{symbol}: using live ask ${live_ask:.2f} as limit price")
    else:
        try:
            premium_est = float(contract.close_price or contract.last_price or 1.0)
        except Exception:
            premium_est = 1.0
        log.warning(f"{symbol}: live quote unavailable, using stale price ${premium_est:.2f}")

    num_contracts = max(1, int(position_usd / (premium_est * 100)))
    log.info(f"{symbol}: {num_contracts} contracts × ${premium_est:.2f} × 100 = ~${num_contracts * premium_est * 100:,.0f}")

    order_info = place_call_order(symbol, contract, num_contracts, live_ask, client)
    if not order_info:
        return None

    update_budget(position_usd)

    trade_record = {
        "id":               rec.get("id", str(uuid.uuid4())[:8]),
        "symbol":           symbol,
        "company":          rec.get("company", symbol),
        "sector":           rec.get("sector", "Unknown"),
        "trade_type":       trade_type,
        "entry_date":       today_et().isoformat(),
        "entry_time":       now_et().isoformat(),
        "entry_price":      price,           # underlying stock price at entry
        "earnings_date":    rec["earnings_date"],
        "earnings_timing":  rec.get("earnings_timing", "unknown"),
        "expiry_date":      str(expiry),
        "qty":              order_info["qty"],
        "position_size":    position_usd,
        "confidence":       confidence,
        "order_id":         order_info["order_id"],
        "order_status":     order_info["order_status"],
        # Options-specific fields
        "long_contract":    order_info.get("long_contract") or order_info.get("contract"),
        "short_contract":   order_info.get("short_contract"),
        "long_strike":      order_info.get("long_strike") or order_info.get("strike"),
        "short_strike":     order_info.get("short_strike"),
        # Hitman v2 filter values (for reporting)
        "beat_rate":        rec.get("beat_rate"),
        "pu5":              rec.get("pu5"),
        "momentum_30d":     rec.get("momentum_30d"),
        "iv_proxy":         rec.get("iv_proxy"),
        # Exit fields (populated by monitor)
        "status":           "open",
        "exit_date":        None,
        "exit_price":       None,
        "pnl":              None,
        "pnl_pct":          None,
    }
    return trade_record


# ── Main executor ─────────────────────────────────────────────────────────────
def run_executor():
    log.info("=" * 60)
    log.info("EXECUTOR: Hitman v2 end-of-day entry (3:50 PM ET)")
    log.info("=" * 60)

    if not is_weekday():
        log.info("Not a weekday — skipping")
        return
    if check_kill_switch():
        log.warning("Kill switch active — executor aborted")
        send_email("Executor Aborted", "Kill switch (STOP_TRADING file) is active.")
        return
    cb_reason = check_circuit_breakers(log)
    if cb_reason:
        log.warning(f"Circuit breaker: {cb_reason} — no trades today")
        send_email("Executor Blocked: Circuit Breaker", f"No trades placed.\nReason: {cb_reason}")
        return
    budget = load_json("budget_tracker.json")
    if budget.get("halted"):
        log.error(f"Trading halted: {budget.get('halt_reason')}")
        return
    if not is_market_open():
        log.warning("Market not open — executor aborted")
        return

    recommendations = load_json("recommendations.json")
    if not isinstance(recommendations, list):
        recommendations = []

    today_str  = today_et().isoformat()
    tomorrow   = (today_et() + timedelta(days=1)).isoformat()
    client     = get_trading_client()

    # Fetch currently held option positions to avoid duplicates
    try:
        owned_symbols = {p.symbol.split(" ")[0] for p in client.get_all_positions()}
    except Exception as e:
        log.warning(f"Could not fetch positions: {e}")
        owned_symbols = set()

    # Candidates: validated, unexecuted, entry date reached, earnings still ahead
    candidates = []
    for r in recommendations:
        if not r.get("validated") or r.get("executed") or r.get("disqualified"):
            continue
        if r["symbol"] in owned_symbols:
            continue
        earnings_dt = date.fromisoformat(r["earnings_date"])
        entry_dt    = get_entry_date(earnings_dt)
        # Only execute on the exact intended entry date (3 trading days before earnings)
        # AND earnings must still be at least 2 days away
        # TEMP (revert after 2026-05-21): allow 1 day late for GLNG missed entry
        days_late = (today_et() - entry_dt).days
        max_late = 1 if r.get("symbol") == "GLNG" else 0
        if days_late <= max_late and r.get("earnings_date", "") > tomorrow:
            candidates.append(r)
        else:
            log.info(
                f"{r['symbol']}: skipping — entry_date={entry_dt} "
                f"({'not yet' if days_late < 0 else f'{days_late}d late, window closed'})"
            )

    log.info(f"Found {len(candidates)} validated Hitman v2 candidates for execution")
    if not candidates:
        log.info("No candidates to execute today")
        send_email("Executor: No Trades Today", "No validated Hitman v2 candidates found.")
        return

    trades_history = load_json("trades_history.json")
    if not isinstance(trades_history, list):
        trades_history = []

    executed_trades = []
    skipped = []

    for rec in candidates:
        log.info(f"Executing: {rec['symbol']} | {rec.get('trade_type','?')} | IV={rec.get('iv_proxy',0):.0%}")
        trade = execute_trade(rec, client)
        if trade:
            executed_trades.append(trade)
            trades_history.append(trade)
            rec["executed"]       = True
            rec["execution_date"] = today_str
            rec["trade_id"]       = trade["id"]
            log.info(f"{rec['symbol']}: EXECUTED — {trade['trade_type']} | {trade['qty']} contracts")
            time.sleep(0.5)
        else:
            skipped.append(rec["symbol"])

    save_json("trades_history.json", trades_history)

    # Update executed flags in recommendations.json
    rec_map = {r["symbol"]: r for r in candidates}
    for i, r in enumerate(recommendations):
        if r["symbol"] in rec_map:
            recommendations[i] = rec_map[r["symbol"]]
    save_json("recommendations.json", recommendations)

    # Email summary
    lines = []
    for t in executed_trades:
        contract_info = t.get("long_contract") or t.get("contract", "N/A")
        lines.append(
            f"  {t['symbol']:6s} | {t['trade_type']:6s} | {t['qty']} contracts | "
            f"contract={contract_info} | earnings={t['earnings_date']}"
        )
    summary = "\n".join(lines) if lines else "  No trades executed."

    log.info(f"EXECUTOR COMPLETE: {len(executed_trades)} trades | skipped: {skipped}")
    send_email(
        f"Hitman v2: {len(executed_trades)} options trades placed",
        f"Trade Execution Report — {today_str}\n\n"
        f"Trades placed: {len(executed_trades)}\n"
        f"Skipped: {', '.join(skipped) if skipped else 'none'}\n\n"
        f"Positions:\n{summary}\n\n"
        f"Capital deployed this run: ${sum(t['position_size'] for t in executed_trades):,.0f}",
    )


if __name__ == "__main__":
    run_executor()
