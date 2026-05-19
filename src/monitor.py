"""
Position monitor — Hitman v2 methodology.
Runs hourly 10 AM - 4 PM ET weekdays.

Tracks open options positions, enforces circuit breakers,
and closes positions at close of day +1 after earnings.
"""
from datetime import date, timedelta
from typing import Dict, Any, List, Optional

import os
import requests as _requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from utils import (
    get_logger, load_settings, load_json, save_json,
    get_trading_client, send_email, check_kill_switch,
    halt_trading, now_et, today_et, is_weekday,
)

log = get_logger("monitor")


# ── Alpaca data fetchers ──────────────────────────────────────────────────────
def fetch_alpaca_positions(client: TradingClient) -> List[Dict[str, Any]]:
    """Fetch all open positions (stocks and options contracts)."""
    positions = []
    try:
        raw = client.get_all_positions()
        for p in raw:
            positions.append({
                "symbol":           p.symbol,
                "qty":              float(p.qty),
                "avg_entry_price":  float(p.avg_entry_price),
                "current_price":    float(p.current_price),
                "market_value":     float(p.market_value),
                "unrealized_pl":    float(p.unrealized_pl),
                "unrealized_plpc":  float(p.unrealized_plpc),
                "side":             str(p.side),
            })
    except Exception as e:
        log.error(f"Failed to fetch positions: {e}")
    return positions


def fetch_alpaca_account(client: TradingClient) -> Dict[str, Any]:
    try:
        acct = client.get_account()
        equity = float(acct.equity)
        try:
            last_equity = float(acct.last_equity) if acct.last_equity is not None else equity
        except (TypeError, ValueError):
            last_equity = equity
        return {
            "equity":         equity,
            "cash":           float(acct.cash),
            "portfolio_value": float(acct.portfolio_value),
            "buying_power":   float(acct.buying_power),
            "last_equity":    last_equity,
            "daytrade_count": int(acct.daytrade_count),
        }
    except Exception as e:
        log.error(f"Failed to fetch account: {e}")
        return {}


def get_closed_orders_today(client: TradingClient) -> List[Dict[str, Any]]:
    """Fetch all orders that filled today to detect exits."""
    orders = []
    try:
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=today_et().isoformat(),
            limit=200,
        )
        raw = client.get_orders(req)
        for o in raw:
            orders.append({
                "id":            str(o.id),
                "symbol":        o.symbol,
                "side":          str(o.side),
                "qty":           float(o.qty or 0),
                "filled_price":  float(o.filled_avg_price or 0),
                "status":        str(o.status),
                "filled_at":     str(o.filled_at),
            })
    except Exception as e:
        log.error(f"Failed to fetch closed orders: {e}")
    return orders


# ── P&L reconciliation ────────────────────────────────────────────────────────
def reconcile_trades_history(
    positions: List[Dict[str, Any]],
    closed_orders: List[Dict[str, Any]],
) -> None:
    """
    Detect closed options positions and record P&L.

    For options trades:
      - entry_cost  = position_size ($10K fixed)
      - exit_value  = filled_price * qty * 100  (options are per-share, 100 shares/contract)
      - pnl         = exit_value - entry_cost
      - pnl_pct     = pnl / entry_cost * 100

    For spreads, only the long leg close is used to approximate P&L.
    The short leg premium received reduces the true cost but is tracked separately.
    """
    trades = load_json("trades_history.json")
    if not isinstance(trades, list):
        return

    # Build a set of contract symbols currently held as open positions
    open_contracts = {p["symbol"] for p in positions}

    # Build a lookup of closed sell orders by contract symbol
    closed_sells: Dict[str, Dict] = {}
    for o in closed_orders:
        if "sell" in o["side"].lower() and o["filled_price"] > 0:
            closed_sells[o["symbol"]] = o

    changed = False
    for trade in trades:
        if trade.get("status") != "open":
            continue

        # Determine the primary tracking contract (long leg for spreads, single call for naked)
        long_contract = trade.get("long_contract")
        if not long_contract:
            continue

        # If the long contract is still in open positions, trade is still live
        if long_contract in open_contracts:
            continue

        # Check if there's a filled sell order for the long contract
        sell_order = closed_sells.get(long_contract)
        if not sell_order:
            continue

        exit_premium  = sell_order["filled_price"]   # per-share premium at exit
        qty           = trade.get("qty", 1)
        entry_cost    = trade.get("position_size", 10000)

        # Options P&L: (exit_premium * contracts * 100) - entry_cost
        exit_value = exit_premium * qty * 100
        pnl        = exit_value - entry_cost
        pnl_pct    = (pnl / entry_cost * 100) if entry_cost else 0

        trade["status"]     = "closed"
        trade["exit_date"]  = today_et().isoformat()
        trade["exit_price"] = round(exit_premium, 4)   # per-share premium
        trade["exit_value"] = round(exit_value, 2)     # total proceeds
        trade["pnl"]        = round(pnl, 2)
        trade["pnl_pct"]    = round(pnl_pct, 2)
        changed = True

        outcome = "WIN" if pnl > 0 else "LOSS"
        log.info(
            f"{trade['symbol']}: {outcome} | "
            f"Entry cost: ${entry_cost:,.0f} | Exit value: ${exit_value:,.0f} | "
            f"P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)"
        )

    if changed:
        save_json("trades_history.json", trades)


# ── Earnings exit ─────────────────────────────────────────────────────────────
def exit_earnings_positions(client: TradingClient) -> None:
    """
    Close options positions at close of day +1 after earnings.

    Hitman v2 always exits the next trading day after earnings regardless
    of whether earnings were pre or post market. This simplifies exit logic
    and matches the backtest assumption.
    """
    trades = load_json("trades_history.json")
    if not isinstance(trades, list):
        return

    today = today_et()

    for trade in trades:
        if trade.get("status") != "open":
            continue
        earnings_date_str = trade.get("earnings_date")
        if not earnings_date_str:
            continue

        earnings_date = date.fromisoformat(earnings_date_str)

        # Always exit on next business day after earnings
        exit_day = earnings_date + timedelta(days=1)
        while exit_day.weekday() >= 5:   # skip weekends
            exit_day += timedelta(days=1)

        if today != exit_day:
            continue

        symbol        = trade["symbol"]
        long_contract = trade.get("long_contract")
        short_contract = trade.get("short_contract")
        qty           = trade.get("qty", 0)

        if not long_contract or qty <= 0:
            log.warning(f"{symbol}: no contract info in trade record — skipping exit")
            continue

        log.info(f"{symbol}: earnings exit — closing options position (exit_day={exit_day})")

        # Close long contract position
        try:
            client.close_position(long_contract)
            log.info(f"{symbol}: closed long contract {long_contract}")
        except Exception as e:
            log.error(f"{symbol}: failed to close long contract {long_contract} — {e}")

        # For spreads: buy back the short contract to close it
        if short_contract:
            try:
                from alpaca.trading.requests import MarketOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                buyback = MarketOrderRequest(
                    symbol=short_contract,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
                client.submit_order(buyback)
                log.info(f"{symbol}: bought back short contract {short_contract}")
            except Exception as e:
                log.error(f"{symbol}: failed to close short contract {short_contract} — {e}")

        send_email(
            f"Earnings Exit: {symbol}",
            f"{symbol} options position closed.\n"
            f"Trade type: {trade.get('trade_type','?')} | "
            f"Earnings: {earnings_date} | Exit day: {exit_day}\n"
            f"Contracts: {long_contract}" +
            (f" / {short_contract}" if short_contract else "")
        )


# ── Circuit breakers ──────────────────────────────────────────────────────────
def calculate_weekly_pnl() -> float:
    """Sum P&L for trades closed this week."""
    trades = load_json("trades_history.json")
    if not isinstance(trades, list):
        return 0.0
    week_start = today_et() - timedelta(days=today_et().weekday())
    total = 0.0
    for t in trades:
        if t.get("status") == "closed" and t.get("exit_date"):
            try:
                exit_d = date.fromisoformat(t["exit_date"])
                if exit_d >= week_start:
                    total += t.get("pnl", 0) or 0
            except Exception:
                pass
    return total


def check_and_enforce_circuit_breakers(
    positions: List[Dict[str, Any]],
    account: Dict[str, Any],
    client: TradingClient,
) -> Optional[str]:
    settings = load_settings()
    th = settings["thresholds"]
    equity      = account.get("equity", 0)
    last_equity = account.get("last_equity", equity)
    daily_pnl   = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity else 0

    weekly_pnl = calculate_weekly_pnl()
    deployed   = sum(
        t.get("position_size", 0)
        for t in (load_json("trades_history.json") or [])
        if t.get("status") == "open"
    )
    weekly_pnl_pct = (weekly_pnl / deployed * 100) if deployed else 0

    reason = None
    if daily_pnl_pct <= -th["circuit_breaker_daily_loss_pct"]:
        reason = f"Daily loss {daily_pnl_pct:.1f}% breached -{th['circuit_breaker_daily_loss_pct']}% limit"
    elif weekly_pnl_pct <= -th["circuit_breaker_weekly_loss_pct"]:
        reason = f"Weekly loss {weekly_pnl_pct:.1f}% breached -{th['circuit_breaker_weekly_loss_pct']}% limit"

    if reason:
        log.error(f"CIRCUIT BREAKER: {reason}")
        halt_trading(reason, log)
        try:
            for pos in client.get_all_positions():
                try:
                    client.close_position(pos.symbol)
                    log.info(f"Emergency close: {pos.symbol}")
                except Exception as ce:
                    log.error(f"Failed to close {pos.symbol}: {ce}")
        except Exception as e:
            log.error(f"Emergency liquidation failed: {e}")
        send_email(
            "CIRCUIT BREAKER — All Positions Closed",
            f"Circuit breaker activated.\nReason: {reason}\n\n"
            f"Daily P&L:  ${daily_pnl:.2f} ({daily_pnl_pct:.1f}%)\n"
            f"Weekly P&L: ${weekly_pnl:.2f} ({weekly_pnl_pct:.1f}%)\n\n"
            "All open positions closed. Trading halted.",
        )
    return reason


# ── Portfolio history ─────────────────────────────────────────────────────────
def fetch_portfolio_history() -> Dict[str, Any]:
    """Fetch 1D/1W/1M portfolio equity history from Alpaca REST API."""
    key    = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    base   = "https://paper-api.alpaca.markets/v2/account/portfolio/history"
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    result = {}
    for period, timeframe in [("1D", "5Min"), ("1W", "1H"), ("1M", "1D")]:
        try:
            r = _requests.get(base, headers=headers,
                              params={"period": period, "timeframe": timeframe}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                points = [
                    {"t": ts, "v": round(float(eq), 2)}
                    for ts, eq in zip(data.get("timestamp", []), data.get("equity", []))
                    if eq is not None
                ]
                result[period] = {
                    "points":     points,
                    "base_value": data.get("base_value"),
                    "timeframe":  timeframe,
                }
        except Exception as e:
            log.warning(f"Portfolio history {period} failed: {e}")
    return result


# ── Main monitor ──────────────────────────────────────────────────────────────
def run_monitor():
    log.info(f"MONITOR: {now_et().strftime('%H:%M ET')}")
    if not is_weekday():
        return
    if check_kill_switch():
        log.warning("Kill switch active — monitoring only, no trades")

    budget = load_json("budget_tracker.json")
    if budget.get("halted"):
        log.info(f"Trading halted ({budget.get('halt_reason')}) — monitor still running")

    client = get_trading_client()

    # 1. Trigger any earnings exits due today
    exit_earnings_positions(client)

    # 2. Fetch live state
    positions    = fetch_alpaca_positions(client)
    account      = fetch_alpaca_account(client)
    closed_orders = get_closed_orders_today(client)

    # 3. Reconcile closed positions
    reconcile_trades_history(positions, closed_orders)

    # 4. Calculate P&L metrics
    equity      = account.get("equity", 0)
    last_equity = account.get("last_equity", equity)
    daily_pnl   = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity else 0
    weekly_pnl  = calculate_weekly_pnl()
    deployed    = sum(
        t.get("position_size", 0)
        for t in (load_json("trades_history.json") or [])
        if t.get("status") == "open"
    )
    weekly_pnl_pct = (weekly_pnl / deployed * 100) if deployed else 0

    pos_data = {
        "positions":                positions,
        "account":                  account,
        "last_updated":             now_et().isoformat(),
        "daily_pnl":                round(daily_pnl, 2),
        "daily_pnl_pct":            round(daily_pnl_pct, 2),
        "weekly_pnl":               round(weekly_pnl, 2),
        "weekly_pnl_pct":           round(weekly_pnl_pct, 2),
        "open_positions_count":     len(positions),
        "circuit_breaker_triggered": False,
        "circuit_breaker_reason":   None,
    }

    # 5. Check circuit breakers (only if not already halted)
    if not budget.get("halted"):
        cb_reason = check_and_enforce_circuit_breakers(positions, account, client)
        if cb_reason:
            pos_data["circuit_breaker_triggered"] = True
            pos_data["circuit_breaker_reason"]    = cb_reason

    save_json("positions.json", pos_data)

    # 6. Save portfolio history
    portfolio_history = fetch_portfolio_history()
    if portfolio_history:
        save_json("portfolio_history.json", portfolio_history)

    # 7. Log summary
    total_unrealized = sum(p["unrealized_pl"] for p in positions)
    log.info(
        f"Positions: {len(positions)} open | "
        f"Unrealized P&L: ${total_unrealized:+,.2f} | "
        f"Daily: ${daily_pnl:+,.2f} ({daily_pnl_pct:+.1f}%) | "
        f"Weekly: ${weekly_pnl:+,.2f}"
    )
    for p in positions:
        pnl_pct = p["unrealized_plpc"] * 100
        flag = "DOWN" if pnl_pct < -10 else "FLAT" if pnl_pct < 0 else "UP  "
        log.info(
            f"  [{flag}] {p['symbol']:30s} | qty={p['qty']:.0f} | "
            f"entry=${p['avg_entry_price']:.4f} | now=${p['current_price']:.4f} | "
            f"P&L: ${p['unrealized_pl']:+,.2f} ({pnl_pct:+.1f}%)"
        )


if __name__ == "__main__":
    run_monitor()
