"""
Task 3 — Trade executor (runs at 9:35 AM ET weekdays).
Places bracket orders for validated recommendations via Alpaca.
Enforces hard budget caps and safety checks.
"""
import time
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

from utils import (
    get_logger, load_settings, load_json, save_json,
    get_trading_client, send_email, check_kill_switch,
    check_circuit_breakers, check_budget, update_budget,
    halt_trading, now_et, today_et, is_weekday, is_market_open,
)

log = get_logger("executor")


def get_current_price(symbol: str, client: TradingClient) -> Optional[float]:
    # Use the shared data client (reads keys from .env via get_alpaca_keys)
    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        from utils import get_data_client
        data_client = get_data_client()
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = data_client.get_stock_latest_quote(req)
        if quote and symbol in quote:
            q = quote[symbol]
            ask = float(q.ask_price)
            bid = float(q.bid_price)
            # Use ask price — closer to what Alpaca uses as base_price for buys
            if ask > 0:
                return ask
            if bid > 0:
                return bid
    except Exception:
        pass
    try:
        info = yf.Ticker(symbol).fast_info or {}
        price = getattr(info, "last_price", None)
        if price:
            return float(price)
    except Exception:
        pass
    return None


def calculate_shares(position_size: float, price: float) -> int:
    if price <= 0:
        return 0
    shares = int(position_size / price)
    return max(shares, 1)


def place_bracket_order(
    symbol: str,
    shares: int,
    entry_price: float,
    position_size: float,
    client: TradingClient,
) -> Optional[Dict[str, Any]]:
    stop_price = round(entry_price * 0.50, 2)
    # Add a 1% buffer above entry so TP always clears Alpaca's live base_price
    # even if the market ticks up between our price fetch and order submission
    take_profit_price = round(entry_price * 2.02, 2)
    log.info(
        f"{symbol}: placing bracket order — {shares} shares @ ~${entry_price:.2f} | "
        f"TP: ${take_profit_price:.2f} (+100%) | SL: ${stop_price:.2f} (-50%)"
    )
    try:
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit_price),
            stop_loss=StopLossRequest(stop_price=stop_price),
        )
        order = client.submit_order(order_data)
        log.info(f"{symbol}: order submitted — ID: {order.id}, status: {order.status}")
        return {
            "order_id": str(order.id),
            "status": str(order.status),
            "symbol": symbol,
            "qty": shares,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
        }
    except Exception as e:
        log.error(f"{symbol}: order failed — {e}")
        send_email(f"Order Failed: {symbol}", f"Failed to place order for {symbol}.\nError: {e}")
        return None


def execute_trade(rec: Dict[str, Any], client: TradingClient) -> Optional[Dict[str, Any]]:
    symbol = rec["symbol"]
    position_size = rec["position_size"]
    can_trade, reason = check_budget(position_size, log)
    if not can_trade:
        log.warning(f"{symbol}: skipping — {reason}")
        return None
    price = get_current_price(symbol, client)
    if not price or price <= 0:
        log.error(f"{symbol}: could not get current price — skipping")
        return None
    shares = calculate_shares(position_size, price)
    if shares < 1:
        log.warning(f"{symbol}: position size ${position_size} too small for ${price:.2f} stock — skipping")
        return None
    actual_cost = shares * price
    can_trade, reason = check_budget(actual_cost, log)
    if not can_trade:
        log.warning(f"{symbol}: skipping — {reason}")
        return None
    order_info = place_bracket_order(symbol, shares, price, actual_cost, client)
    if not order_info:
        return None
    update_budget(actual_cost)
    trade_record = {
        "id": rec.get("id", str(uuid.uuid4())[:8]),
        "symbol": symbol,
        "company": rec.get("company", symbol),
        "entry_date": today_et().isoformat(),
        "entry_time": now_et().isoformat(),
        "entry_price": price,
        "qty": shares,
        "position_size": round(actual_cost, 2),
        "confidence": rec["confidence"],
        "earnings_date": rec["earnings_date"],
        "sector": rec.get("sector", "Unknown"),
        "order_id": order_info["order_id"],
        "order_status": order_info["status"],
        "stop_loss_price": order_info["stop_price"],
        "take_profit_price": order_info["take_profit_price"],
        "status": "open",
        "exit_price": None,
        "exit_date": None,
        "pnl": None,
        "pnl_pct": None,
        "beat_rate": rec.get("beat_rate"),
        "revenue_growth_yoy": rec.get("revenue_growth_yoy"),
        "momentum_30d": rec.get("momentum_30d"),
        "earnings_timing": rec.get("earnings_timing", "unknown"),
    }
    return trade_record


def run_executor():
    log.info("=" * 60)
    log.info("EXECUTOR: Trade execution starting at 9:35 AM ET")
    log.info("=" * 60)
    if not is_weekday():
        log.info("Not a weekday — skipping execution")
        return
    if check_kill_switch():
        log.warning("Kill switch active — executor aborted")
        send_email("Executor Aborted", "Kill switch (STOP_TRADING file) is active.")
        return
    cb_reason = check_circuit_breakers(log)
    if cb_reason:
        log.warning(f"Circuit breaker active: {cb_reason} — no trades today")
        send_email("Executor Blocked: Circuit Breaker", f"No trades placed.\nReason: {cb_reason}")
        return
    budget = load_json("budget_tracker.json")
    if budget.get("halted"):
        log.error(f"Trading halted: {budget.get('halt_reason')}")
        return
    if not is_market_open():
        log.warning("Market is not open — executor aborted")
        return
    recommendations = load_json("recommendations.json")
    if not isinstance(recommendations, list):
        recommendations = []
    today_str = today_et().isoformat()
    to_execute = [
        r for r in recommendations
        if r.get("validated")
        and not r.get("executed")
        and not r.get("disqualified")
        and r.get("earnings_date") == today_str
    ]
    if not to_execute:
        to_execute = [
            r for r in recommendations
            if r.get("validated")
            and not r.get("executed")
            and not r.get("disqualified")
        ]
    client = get_trading_client()
    # Skip any symbol we already hold in Alpaca — don't double up on earnings bets
    try:
        owned = {p.symbol for p in client.get_all_positions()}
    except Exception as e:
        log.warning(f"Could not fetch Alpaca positions, skipping overlap check: {e}")
        owned = set()
    before = len(to_execute)
    to_execute = [r for r in to_execute if r["symbol"] not in owned]
    skipped_owned = before - len(to_execute)
    if skipped_owned:
        log.info(f"Skipping {skipped_owned} recommendations already held as Alpaca positions")
    log.info(f"Found {len(to_execute)} validated plays ready for execution")
    if not to_execute:
        log.info("No validated plays to execute today")
        send_email("Executor: No Trades Today", "No validated plays found for execution today.")
        return
    trades_history = load_json("trades_history.json")
    if not isinstance(trades_history, list):
        trades_history = []
    executed_trades = []
    skipped = []
    for rec in to_execute:
        log.info(f"Attempting to execute: {rec['symbol']} (confidence: {rec['confidence']}%)")
        trade = execute_trade(rec, client)
        if trade:
            executed_trades.append(trade)
            trades_history.append(trade)
            rec["executed"] = True
            rec["execution_date"] = today_str
            rec["trade_id"] = trade["id"]
            log.info(f"{rec['symbol']}: EXECUTED — {trade['qty']} shares @ ${trade['entry_price']:.2f}")
            time.sleep(0.5)
        else:
            skipped.append(rec["symbol"])
    save_json("trades_history.json", trades_history)
    for i, r in enumerate(recommendations):
        symbol = r["symbol"]
        executed_rec = next((e for e in to_execute if e["symbol"] == symbol), None)
        if executed_rec:
            recommendations[i] = executed_rec
    save_json("recommendations.json", recommendations)
    budget = load_json("budget_tracker.json")
    total_deployed = sum(t["position_size"] for t in executed_trades)
    summary_lines = []
    for t in executed_trades:
        summary_lines.append(
            f"  {t['symbol']:6s} | {t['qty']} shares @ ${t['entry_price']:.2f} | "
            f"Cost: ${t['position_size']:.0f} | TP: ${t['take_profit_price']:.2f} | SL: ${t['stop_loss_price']:.2f}"
        )
    summary = "\n".join(summary_lines) if summary_lines else "  No trades executed."
    log.info(
        f"EXECUTOR COMPLETE: {len(executed_trades)} trades placed, "
        f"${total_deployed:.0f} deployed. Skipped: {skipped}"
    )
    budget_msg = (
        f"\nBudget Status:\n"
        f"  2-Week: ${budget['two_week_deployed']:.0f} / ${budget['two_week_cap']} used\n"
        f"  Total:  ${budget['total_deployed']:.0f} / ${budget['total_cap']} used"
    )
    send_email(
        f"Trades Executed: {len(executed_trades)} positions opened",
        f"Trade Execution Report — {today_str}\n\n"
        f"Trades placed: {len(executed_trades)}\n"
        f"Skipped: {', '.join(skipped) if skipped else 'none'}\n\n"
        f"Positions:\n{summary}\n{budget_msg}",
    )


if __name__ == "__main__":
    run_executor()
