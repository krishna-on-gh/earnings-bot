"""
Learner — DISABLED for Hitman v2.

Hitman v2 uses fixed filter thresholds (beat_rate >= 62%, pu5 >= 40%,
momentum +1-30%, IV < 80%) validated by a 4-year backtest. There are no
scoring weights to adjust dynamically.

This script is kept as a stub so the GitHub Actions / Railway workflow
doesn't break. It logs a note and exits cleanly.
"""
from utils import get_logger, send_email, today_et, load_json

log = get_logger("learner")


def run_learner():
    log.info("=" * 60)
    log.info("LEARNER: Disabled — Hitman v2 uses fixed thresholds")
    log.info("=" * 60)

    trades  = load_json("trades_history.json") or []
    closed  = [t for t in trades if t.get("status") == "closed"]
    wins    = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    total_pnl = sum(t.get("pnl") or 0 for t in closed)
    win_rate  = (wins / len(closed) * 100) if closed else 0

    log.info(f"All-time: {len(closed)} closed trades | Win rate: {win_rate:.0f}% | P&L: ${total_pnl:+,.2f}")
    log.info("No weight adjustments — Hitman v2 thresholds are fixed.")

    send_email(
        f"Weekly Summary: {len(closed)} trades | Win rate {win_rate:.0f}% | P&L ${total_pnl:+,.2f}",
        f"Hitman v2 Weekly Summary — {today_et().isoformat()}\n\n"
        f"Closed trades:  {len(closed)}\n"
        f"Win rate:       {win_rate:.0f}%\n"
        f"Total P&L:      ${total_pnl:+,.2f}\n\n"
        f"Note: Dynamic weight learning is disabled for Hitman v2.\n"
        f"Filter thresholds are fixed and backtest-validated.",
    )


if __name__ == "__main__":
    run_learner()
