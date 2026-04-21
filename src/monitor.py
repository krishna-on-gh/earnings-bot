"""
Task 4 — Position monitor (runs hourly 10 AM - 4 PM ET weekdays).
Fetches live positions from Alpaca, calculates P&L,
checks circuit breakers, updates positions.json.
"""
from datetime import date, timedelta
from typing import Dict, Any, List, Optional

import os, requests as _requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from utils import (
    get_logger, load_settings, load_json, save_json,
    get_trading_client, send_email, check_kill_switch,
    halt_trading, now_et, today_et, is_weekday,
)

log = get_logger("monitor")


def fetch_alpaca_positions(client: TradingClient) -> List[Dict[str, Any]]:
    positions = []
    try:
        raw = client.get_all_positions()
        for p in raw:
            positions.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "side": str(p.side),
            })
    except Exception as e:
        log.error(f"Failed to fetch positions from Alpaca: {e}")
    return positions


def fetch_alpaca_account(client: TradingClient) -> Dict[str, Any]:
    try:
        acct = client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "portfolio_value": float(acct.portfolio_value),
            "buying_power": float(acct.buying_power),
            "last_equity": float(acct.last_equity),
            "daytrade_count": int(acct.daytrade_count),
        }
    except Exception as e:
        log.error(f"Failed to fetch account from Alpaca: {e}")
        return {}


def get_closed_orders_today(client: TradingClient) -> List[Dict[str, Any]]:
    """Fetch orders that filled today to detect exits."""
    orders = []
    try:
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=today_et().isoformat(),
            limit=100,
        )
        raw = client.get_orders(req)
        for o in raw:
            orders.append({
                "id": str(o.id),
                "symbol": o.symbol,
                "side": str(o.side),
                "qty": float(o.qty or 0),
                "filled_price": float(o.filled_avg_price or 0),
                "status": str(o.status),
                "submitted_at": str(o.submitted_at),
                "filled_at": str(o.filled_at),
            })
    except Exception as e:
        log.error(f"Failed to fetch closed orders: {e}")
    return orders


def reconcile_trades_history(
    positions: List[Dict[str, Any]],
    closed_orders: List[Dict[str, Any]],
) -> None:
    """Update trades_history.json for any positions that have been closed."""
    trades = load_json("trades_history.json")
    if not isinstance(trades, list):
        return
    open_symbols = {p["symbol"] for p in positions}
    changed = False
    for trade in trades:
        if trade["status"] != "open":
            continue
        symbol = trade["symbol"]
        if symbol not in open_symbols:
            sell_order = next(
                (o for o in closed_orders
                 if o["symbol"] == symbol and "sell" in o["side"].lower()),
                None,
            )
            if sell_order and sell_order["filled_price"]:
                exit_price = sell_order["filled_price"]
                entry_price = trade["entry_price"]
                pnl = (exit_price - entry_price) * trade["qty"]
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trade["status"] = "closed"
                trade["exit_price"] = round(exit_price, 2)
                trade["exit_date"] = today_et().isoformat()
                trade["pnl"] = round(pnl, 2)
                trade["pnl_pct"] = round(pnl_pct, 2)
                changed = True
                outcome = "WIN" if pnl > 0 else "LOSS"
                log.info(
                    f"{symbol}: position closed — {outcome} | "
                    f"Entry: ${entry_price:.2f} Exit: ${exit_price:.2f} | "
                    f"P&L: ${pnl:.2f} ({pnl_pct:.1f}%)"
                )
    if changed:
        save_json("trades_history.json", trades)


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
    daily_threshold = -th["circuit_breaker_daily_loss_pct"] / 100
    weekly_threshold = -th["circuit_breaker_weekly_loss_pct"] / 100
    equity = account.get("equity", 0)
    last_equity = account.get("last_equity", equity)
    daily_pnl = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity else 0
    weekly_pnl = calculate_weekly_pnl()
    deployed = sum(t.get("position_size", 0) for t in load_json("trades_history.json") or [] if t.get("status") == "open")
    weekly_pnl_pct = (weekly_pnl / deployed * 100) if deployed else 0
    reason = None
    if daily_pnl_pct <= -th["circuit_breaker_daily_loss_pct"]:
        reason = f"Daily loss {daily_pnl_pct:.1f}% breached -{th['circuit_breaker_daily_loss_pct']}% limit"
    elif weekly_pnl_pct <= -th["circuit_breaker_weekly_loss_pct"]:
        reason = f"Weekly loss {weekly_pnl_pct:.1f}% breached -{th['circuit_breaker_weekly_loss_pct']}% limit"
    if reason:
        log.error(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        halt_trading(reason, log)
        try:
            all_positions = client.get_all_positions()
            for pos in all_positions:
                try:
                    client.close_position(pos.symbol)
                    log.info(f"Emergency close: {pos.symbol}")
                except Exception as ce:
                    log.error(f"Failed to close {pos.symbol}: {ce}")
        except Exception as e:
            log.error(f"Failed emergency liquidation: {e}")
        send_email(
            "CIRCUIT BREAKER TRIGGERED — All Positions Closed",
            f"Emergency circuit breaker activated.\nReason: {reason}\n\n"
            f"Daily P&L: ${daily_pnl:.2f} ({daily_pnl_pct:.1f}%)\n"
            f"Weekly P&L: ${weekly_pnl:.2f} ({weekly_pnl_pct:.1f}%)\n\n"
            f"All open positions have been closed. Trading halted.",
        )
    return reason


def exit_earnings_positions(client: TradingClient) -> None:
    """Close positions whose earnings exit day has arrived."""
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
        timing = trade.get("earnings_timing", "unknown")
        # Pre-market: sell same day as earnings
        # Post-market or unknown: sell the next business day
        if timing == "pre_market":
            exit_day = earnings_date
        else:
            # Next weekday after earnings
            exit_day = earnings_date + timedelta(days=1)
            while exit_day.weekday() >= 5:
                exit_day += timedelta(days=1)
        if today != exit_day:
            continue
        symbol = trade["symbol"]
        qty = trade.get("qty", 0)
        if qty <= 0:
            continue
        log.info(f"{symbol}: earnings exit triggered ({timing}, exit_day={exit_day}) — closing position")
        try:
            # Cancel all open orders for this symbol first (bracket legs)
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            open_orders = client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol]
            ))
            for o in open_orders:
                try:
                    client.cancel_order_by_id(o.id)
                    log.info(f"{symbol}: cancelled order {o.id}")
                except Exception as ce:
                    log.warning(f"{symbol}: could not cancel order {o.id} — {ce}")
            # Close the position at market
            client.close_position(symbol)
            log.info(f"{symbol}: market sell placed for earnings exit")
            send_email(
                f"Earnings Exit: {symbol}",
                f"{symbol} position closed for earnings exit.\n"
                f"Timing: {timing} | Earnings: {earnings_date} | Exit day: {exit_day}"
            )
        except Exception as e:
            log.error(f"{symbol}: earnings exit failed — {e}")


def fetch_portfolio_history() -> Dict[str, Any]:
    """Fetch 1D/1W/1M portfolio equity history from Alpaca REST API."""
    key    = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    base   = "https://paper-api.alpaca.markets/v2/account/portfolio/history"
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    result = {}
    periods = [
        ("1D", "5Min"),
        ("1W", "1H"),
        ("1M", "1D"),
    ]
    for period, timeframe in periods:
        try:
            r = _requests.get(base, headers=headers,
                              params={"period": period, "timeframe": timeframe,
                                      "extended_hours": "true"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                points = []
                for ts, eq in zip(data.get("timestamp", []), data.get("equity", [])):
                    if eq is not None:
                        points.append({"t": ts, "v": round(float(eq), 2)})
                result[period] = {
                    "points":     points,
                    "base_value": data.get("base_value"),
                    "timeframe":  timeframe,
                }
            else:
                log.warning(f"Portfolio history {period} returned {r.status_code}")
        except Exception as e:
            log.warning(f"Portfolio history fetch failed ({period}): {e}")
    return result


def run_monitor():
    log.info(f"MONITOR: Checking positions at {now_et().strftime('%H:%M ET')}")
    if not is_weekday():
        return
    if check_kill_switch():
        log.warning("Kill switch active — monitor noting but not trading")
    budget = load_json("budget_tracker.json")
    if budget.get("halted"):
        log.info(f"Trading halted ({budget.get('halt_reason')}) — monitor still running")
    client = get_trading_client()
    exit_earnings_positions(client)
    positions = fetch_alpaca_positions(client)
    account = fetch_alpaca_account(client)
    closed_orders = get_closed_orders_today(client)
    reconcile_trades_history(positions, closed_orders)
    equity = account.get("equity", 0)
    last_equity = account.get("last_equity", equity)
    daily_pnl = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity else 0
    weekly_pnl = calculate_weekly_pnl()
    deployed = sum(t.get("position_size", 0) for t in load_json("trades_history.json") or [] if t.get("status") == "open")
    weekly_pnl_pct = (weekly_pnl / deployed * 100) if deployed else 0
    pos_data = {
        "positions": positions,
        "account": account,
        "last_updated": now_et().isoformat(),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "weekly_pnl": round(weekly_pnl, 2),
        "weekly_pnl_pct": round(weekly_pnl_pct, 2),
        "open_positions_count": len(positions),
        "circuit_breaker_triggered": False,
        "circuit_breaker_reason": None,
    }
    if not budget.get("halted"):
        cb_reason = check_and_enforce_circuit_breakers(positions, account, client)
        if cb_reason:
            pos_data["circuit_breaker_triggered"] = True
            pos_data["circuit_breaker_reason"] = cb_reason
    save_json("positions.json", pos_data)
    portfolio_history = fetch_portfolio_history()
    if portfolio_history:
        save_json("portfolio_history.json", portfolio_history)
        log.info("Portfolio history saved")
    total_unrealized = sum(p["unrealized_pl"] for p in positions)
    log.info(
        f"Positions: {len(positions)} open | "
        f"Unrealized P&L: ${total_unrealized:.2f} | "
        f"Daily: ${daily_pnl:.2f} ({daily_pnl_pct:.1f}%) | "
        f"Weekly: ${weekly_pnl:.2f} ({weekly_pnl_pct:.1f}%)"
    )
    for p in positions:
        pnl_pct = p["unrealized_plpc"] * 100
        flag = "🔴" if pnl_pct < -10 else "🟡" if pnl_pct < 0 else "🟢"
        log.info(
            f"  {flag} {p['symbol']:6s} | {p['qty']:.0f} shares @ ${p['avg_entry_price']:.2f} | "
            f"Now: ${p['current_price']:.2f} | P&L: ${p['unrealized_pl']:.2f} ({pnl_pct:.1f}%)"
        )


if __name__ == "__main__":
    run_monitor()
