"""
Task 4 — Position monitor (runs hourly 10 AM - 4 PM ET weekdays).
Fetches live positions from Alpaca, calculates P&L,
checks circuit breakers, updates positions.json.
"""
from datetime import date, timedelta
from typing import Dict, Any, List, Optional

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
