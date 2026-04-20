"""
Tracks every copied trade and computes running P&L.
Logs are stored in data/trade_log.json.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _load_log() -> list[dict]:
    if os.path.exists(config.TRADE_LOG_FILE):
        try:
            with open(config.TRADE_LOG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_log(log: list[dict]):
    os.makedirs(os.path.dirname(config.TRADE_LOG_FILE), exist_ok=True)
    with open(config.TRADE_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def record_orders(orders: list[dict]):
    """Append newly placed orders to the trade log."""
    if not orders:
        return
    log = _load_log()
    for order in orders:
        entry = {**order, "recorded_at": datetime.now().isoformat()}
        log.append(entry)
        logger.info(f"Recorded order: {order.get('order_id')} {order.get('ticker')} {order.get('side')}")
    _save_log(log)


def print_summary(copier=None):
    """Print a summary of all copied trades and current portfolio value."""
    log = _load_log()
    print("\n" + "=" * 60)
    print("  POLITICIAN COPY TRADER — TRADE SUMMARY")
    print("=" * 60)

    if not log:
        print("  No trades copied yet.")
    else:
        by_politician: dict[str, list] = {}
        for entry in log:
            pol = entry.get("copied_from", "Unknown")
            by_politician.setdefault(pol, []).append(entry)

        for pol, trades in sorted(by_politician.items()):
            print(f"\n  {pol}  ({len(trades)} trades copied)")
            for t in trades[-5:]:  # show last 5 per politician
                ts = t.get("timestamp", "")[:10]
                side = t.get("side", "").upper()
                ticker = t.get("ticker", "")
                asset = t.get("asset_type", "stock")
                opt = f" [{t.get('option_symbol','')}]" if asset == "option" else ""
                print(f"    {ts}  {side:4s}  {ticker}{opt}  status={t.get('status')}")

    if copier:
        try:
            portfolio_value = copier.get_portfolio_value()
            acct = copier.client.get_account()
            print(f"\n  Portfolio value : ${portfolio_value:,.2f}")
            print(f"  Cash            : ${float(acct.cash):,.2f}")
            print(f"  Buying power    : ${float(acct.buying_power):,.2f}")

            positions = copier.get_positions()
            if positions:
                print(f"\n  Open positions ({len(positions)}):")
                for pos in positions:
                    pl = float(pos.unrealized_pl)
                    pl_pct = float(pos.unrealized_plpc) * 100
                    sign = "+" if pl >= 0 else ""
                    print(
                        f"    {pos.symbol:20s}  qty={pos.qty:6}  "
                        f"P&L={sign}${pl:,.2f} ({sign}{pl_pct:.1f}%)"
                    )
        except Exception as e:
            logger.warning(f"Could not fetch portfolio data: {e}")

    print("=" * 60 + "\n")


def get_stats() -> dict:
    """Return aggregate stats dict."""
    log = _load_log()
    politicians = {}
    for entry in log:
        pol = entry.get("copied_from", "Unknown")
        politicians[pol] = politicians.get(pol, 0) + 1

    return {
        "total_trades_copied": len(log),
        "trades_by_politician": politicians,
        "first_trade": log[0].get("timestamp") if log else None,
        "last_trade": log[-1].get("timestamp") if log else None,
    }
