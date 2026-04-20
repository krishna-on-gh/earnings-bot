"""
Task 5 — Daily reporter (runs at 4:30 PM ET weekdays).
Compiles daily P&L, trade summary, and budget status.
Saves HTML report to reports/ and emails it.
Also updates earnings_calls_log.json and earnings_accuracy.json.
"""
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import yfinance as yf

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


def confidence_tier(conf: float) -> str:
    if conf >= 95:
        return "95-100%"
    elif conf >= 85:
        return "85-94%"
    else:
        return "70-84%"


def fetch_earnings_result(symbol: str, expected_date: str) -> Dict[str, Any]:
    """Try to get actual EPS beat/miss from yfinance for the given earnings date."""
    result = {"beat": None, "eps_estimate": None, "eps_reported": None, "beat_pct": None}
    try:
        ticker = yf.Ticker(symbol)
        edf = ticker.earnings_dates
        if edf is None or edf.empty:
            return result
        # Look within 5 days of the expected earnings date
        expected = date.fromisoformat(expected_date)
        edf.index = edf.index.tz_localize(None) if edf.index.tzinfo else edf.index
        for ts, row in edf.iterrows():
            row_date = ts.date() if hasattr(ts, 'date') else ts
            if abs((row_date - expected).days) <= 5:
                est = row.get("EPS Estimate")
                rep = row.get("Reported EPS")
                if est is not None and rep is not None and str(est) != 'nan' and str(rep) != 'nan':
                    est, rep = float(est), float(rep)
                    beat = rep > est
                    beat_pct = ((rep - est) / abs(est) * 100) if est != 0 else 0
                    result.update({"beat": beat, "eps_estimate": round(est, 3),
                                   "eps_reported": round(rep, 3), "beat_pct": round(beat_pct, 1)})
                    return result
    except Exception:
        pass
    return result


def generate_analysis(trade: Dict, earnings_result: Dict) -> str:
    """Generate a 2-3 sentence analysis of the earnings call."""
    symbol = trade["symbol"]
    conf = trade.get("confidence", 0)
    tier = confidence_tier(conf)
    pnl_pct = trade.get("pnl_pct") or 0
    outcome = "WIN" if (trade.get("pnl") or 0) > 0 else "LOSS"
    beat = earnings_result.get("beat")
    eps_est = earnings_result.get("eps_estimate")
    eps_rep = earnings_result.get("eps_reported")
    beat_pct = earnings_result.get("beat_pct")

    # Sentence 1: earnings result
    if beat is True and eps_est is not None:
        s1 = f"{symbol} beat EPS estimates by {beat_pct:+.1f}% (reported ${eps_rep:.2f} vs ${eps_est:.2f} expected)."
    elif beat is False and eps_est is not None:
        s1 = f"{symbol} missed EPS estimates by {beat_pct:.1f}% (reported ${eps_rep:.2f} vs ${eps_est:.2f} expected)."
    else:
        s1 = f"{symbol} earnings results not yet confirmed in data feed."

    # Sentence 2: trade outcome
    s2 = f"The position returned {pnl_pct:+.1f}% ({outcome}) against a {conf:.0f}% confidence call ({tier} tier)."

    # Sentence 3: call quality
    correct_call = (beat is True and outcome == "WIN") or (beat is False and outcome == "LOSS")
    if beat is None:
        s3 = "Accuracy against estimates could not be verified — trade result logged for manual review."
    elif correct_call:
        s3 = f"The algorithm's confidence was well-calibrated; the earnings reaction matched the {tier} tier prediction."
    else:
        s3 = f"The {tier} confidence call did not match the earnings reaction — flagged for scoring weight review."

    return f"{s1} {s2} {s3}"


def update_earnings_log(today_closed: List[Dict]) -> None:
    """Append closed trades to earnings_calls_log.json with analysis."""
    if not today_closed:
        return
    log_entries = load_json("earnings_calls_log.json")
    if not isinstance(log_entries, list):
        log_entries = []
    existing_ids = {e.get("trade_id") for e in log_entries}

    accuracy = load_json("earnings_accuracy.json") or {
        "70-84%":  {"calls": 0, "correct": 0},
        "85-94%":  {"calls": 0, "correct": 0},
        "95-100%": {"calls": 0, "correct": 0},
    }

    for trade in today_closed:
        trade_id = trade.get("id") or trade.get("trade_id")
        if trade_id in existing_ids:
            continue
        earnings_result = fetch_earnings_result(trade["symbol"], trade.get("earnings_date", ""))
        analysis = generate_analysis(trade, earnings_result)
        tier = confidence_tier(trade.get("confidence", 0))
        beat = earnings_result.get("beat")
        outcome = "WIN" if (trade.get("pnl") or 0) > 0 else "LOSS"
        correct_call = beat is not None and (
            (beat is True and outcome == "WIN") or (beat is False and outcome == "LOSS")
        )

        entry = {
            "trade_id":      trade_id,
            "symbol":        trade["symbol"],
            "company":       trade.get("company", trade["symbol"]),
            "earnings_date": trade.get("earnings_date"),
            "exit_date":     trade.get("exit_date"),
            "confidence":    trade.get("confidence"),
            "tier":          tier,
            "entry_price":   trade.get("entry_price"),
            "exit_price":    trade.get("exit_price"),
            "pnl":           trade.get("pnl"),
            "pnl_pct":       trade.get("pnl_pct"),
            "outcome":       outcome,
            "eps_estimate":  earnings_result.get("eps_estimate"),
            "eps_reported":  earnings_result.get("eps_reported"),
            "beat_pct":      earnings_result.get("beat_pct"),
            "beat":          beat,
            "correct_call":  correct_call if beat is not None else None,
            "analysis":      analysis,
            "logged_at":     now_et().isoformat(),
        }
        log_entries.append(entry)

        # Update accuracy stats
        if tier not in accuracy:
            accuracy[tier] = {"calls": 0, "correct": 0}
        accuracy[tier]["calls"] += 1
        if correct_call:
            accuracy[tier]["correct"] += 1

    save_json("earnings_calls_log.json", log_entries)
    save_json("earnings_accuracy.json", accuracy)
    log.info(f"Earnings log updated: {len(today_closed)} new entries")


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
    update_earnings_log(today_closed)
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
