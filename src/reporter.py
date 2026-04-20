"""
Task 5 — Daily reporter (runs at 4:30 PM ET weekdays).
Compiles daily P&L, trade summary, and budget status.
Saves HTML report to reports/ and emails it.
"""
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Any, List

from utils import (
    get_logger, load_settings, load_json, save_json,
    get_trading_client, send_email, now_et, today_et, is_weekday,
    REPORTS_DIR,
)

log = get_logger("reporter")


def get_today_trades(trades_history: List[Dict]) -> List[Dict]:
    today = today_et().isoformat()
    return [t for t in trades_history if t.get("entry_date") == today]


def get_today_closed(trades_history: List[Dict]) -> List[Dict]:
    today = today_et().isoformat()
    return [
        t for t in trades_history
        if t.get("exit_date") == today and t.get("status") == "closed"
    ]


def get_all_closed(trades_history: List[Dict]) -> List[Dict]:
    return [t for t in trades_history if t.get("status") == "closed"]


def calc_win_rate(closed_trades: List[Dict]) -> float:
    if not closed_trades:
        return 0.0
    wins = sum(1 for t in closed_trades if (t.get("pnl") or 0) > 0)
    return wins / len(closed_trades) * 100


def build_text_report(
    today_trades: List[Dict],
    today_closed: List[Dict],
    all_closed: List[Dict],
    positions: Dict,
    budget: Dict,
) -> str:
    today = today_et().isoformat()
    daily_pnl = positions.get("daily_pnl", 0)
    daily_pnl_pct = positions.get("daily_pnl_pct", 0)
    open_positions = positions.get("positions", [])
    win_rate = calc_win_rate(all_closed)
    total_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed)
    lines = [
        f"DAILY PERFORMANCE REPORT — {today}",
        "=" * 60,
        f"Daily P&L:        ${daily_pnl:+.2f} ({daily_pnl_pct:+.1f}%)",
        f"Total P&L (all):  ${total_pnl:+.2f}",
        f"Win Rate:         {win_rate:.0f}% ({len(all_closed)} closed trades)",
        f"Open Positions:   {len(open_positions)}",
        "",
        "BUDGET STATUS",
        "-" * 40,
        f"  2-Week Budget: ${budget.get('two_week_deployed', 0):.0f} / ${budget.get('two_week_cap', 10000)} deployed",
        f"  Total Budget:  ${budget.get('total_deployed', 0):.0f} / ${budget.get('total_cap', 45000)} deployed",
        f"  2-Week Remaining: ${budget.get('available_two_week', 10000):.0f}",
        f"  Total Remaining:  ${budget.get('available_total', 45000):.0f}",
        "",
    ]
    if today_trades:
        lines.append("TRADES OPENED TODAY")
        lines.append("-" * 40)
        for t in today_trades:
            lines.append(
                f"  {t['symbol']:6s} | {t['qty']} sh @ ${t['entry_price']:.2f} | "
                f"Cost: ${t['position_size']:.0f} | Conf: {t['confidence']:.0f}%"
            )
        lines.append("")
    if today_closed:
        lines.append("TRADES CLOSED TODAY")
        lines.append("-" * 40)
        for t in today_closed:
            outcome = "WIN" if (t.get("pnl") or 0) > 0 else "LOSS"
            lines.append(
                f"  {t['symbol']:6s} | {outcome} | "
                f"Entry: ${t['entry_price']:.2f} → Exit: ${t.get('exit_price', 0):.2f} | "
                f"P&L: ${t.get('pnl', 0):+.2f} ({t.get('pnl_pct', 0):+.1f}%)"
            )
        lines.append("")
    if open_positions:
        lines.append("OPEN POSITIONS")
        lines.append("-" * 40)
        for p in open_positions:
            pnl_pct = p.get("unrealized_plpc", 0) * 100
            lines.append(
                f"  {p['symbol']:6s} | {p['qty']:.0f} sh @ ${p['avg_entry_price']:.2f} | "
                f"Now: ${p['current_price']:.2f} | Unrealized: ${p['unrealized_pl']:+.2f} ({pnl_pct:+.1f}%)"
            )
        lines.append("")
    cb = positions.get("circuit_breaker_triggered")
    halt = budget.get("halted")
    if cb or halt:
        lines.append("⚠️  ALERTS")
        lines.append("-" * 40)
        if cb:
            lines.append(f"  CIRCUIT BREAKER: {positions.get('circuit_breaker_reason')}")
        if halt:
            lines.append(f"  TRADING HALTED: {budget.get('halt_reason')}")
    return "\n".join(lines)


def build_html_report(text_report: str, today_closed: List[Dict]) -> str:
    wins = sum(1 for t in today_closed if (t.get("pnl") or 0) > 0)
    losses = len(today_closed) - wins
    return f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }}
h1 {{ color: #58a6ff; }}
.win {{ color: #3fb950; }}
.loss {{ color: #f85149; }}
pre {{ background: #161b22; padding: 15px; border-radius: 6px; overflow-x: auto; }}
</style>
</head>
<body>
<h1>Daily Performance Report — {today_et().isoformat()}</h1>
<p>Today: <span class="win">{wins} wins</span> / <span class="loss">{losses} losses</span></p>
<pre>{text_report}</pre>
</body>
</html>"""


def run_reporter():
    log.info("=" * 60)
    log.info("REPORTER: Generating daily performance report")
    log.info("=" * 60)
    if not is_weekday():
        log.info("Not a weekday — skipping report")
        return
    trades_history = load_json("trades_history.json") or []
    positions = load_json("positions.json") or {}
    budget = load_json("budget_tracker.json") or {}
    today_trades = get_today_trades(trades_history)
    today_closed = get_today_closed(trades_history)
    all_closed = get_all_closed(trades_history)
    text = build_text_report(today_trades, today_closed, all_closed, positions, budget)
    html = build_html_report(text, today_closed)
    log.info("\n" + text)
    REPORTS_DIR.mkdir(exist_ok=True)
    report_file = REPORTS_DIR / f"report_{today_et().isoformat()}.html"
    report_file.write_text(html, encoding="utf-8")
    log.info(f"Report saved: {report_file}")
    daily_pnl = positions.get("daily_pnl", 0)
    win_rate = calc_win_rate(all_closed)
    send_email(
        f"Daily Report: P&L ${daily_pnl:+.2f} | Win Rate {win_rate:.0f}%",
        text,
        html,
    )


if __name__ == "__main__":
    run_reporter()
