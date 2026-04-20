"""
Main entry point for the Politician Copy Trader.

Modes:
  python main.py          — run once (fetch, score, copy)
  python main.py --run    — run the continuous APScheduler loop
  python main.py --status — print portfolio/trade summary and exit
"""

import argparse
import logging
import os
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "data", "bot.log"),
            mode="a",
        ),
    ],
)

# Make sure data dir exists before any module tries to write to it
os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

import config
import capitol_trades
import politician_scorer
import trade_copier
import tracker

logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Core job functions
# ---------------------------------------------------------------------------

def job_score_politicians():
    """Fetch recent trades and re-score politicians. Runs daily."""
    logger.info("=== [SCORE JOB] Fetching recent trades & scoring politicians ===")
    trades = capitol_trades.fetch_recent_trades(pages=5)
    if not trades:
        logger.warning("No trades fetched — Capitol Trades may be blocking or layout changed")
        return
    scores = politician_scorer.score_politicians(trades)
    logger.info(f"Scored {len(scores)} politicians. Top {config.TOP_N_POLITICIANS}:")
    for s in scores[:config.TOP_N_POLITICIANS]:
        logger.info(
            f"  {s['politician']:30s}  score={s['score']}  trades={s['trade_count']}  "
            f"options={s['options_trades']}  last={s['last_trade_date']}"
        )


def job_copy_trades(copier: trade_copier.TradeCopier):
    """Check for new trades from top politicians and copy them. Runs every 30 min."""
    logger.info("=== [COPY JOB] Checking for new politician trades ===")

    top_politicians = politician_scorer.get_top_politicians()
    if not top_politicians:
        logger.warning("No scored politicians yet — running score job first")
        job_score_politicians()
        top_politicians = politician_scorer.get_top_politicians()

    if not top_politicians:
        logger.error("Still no politicians to follow — skipping copy job")
        return

    logger.info(f"Following: {[p['politician'] for p in top_politicians]}")

    all_trades = capitol_trades.fetch_recent_trades(pages=3)
    top_names = {p["politician"] for p in top_politicians}
    filtered = [t for t in all_trades if t["politician"] in top_names]

    logger.info(f"Found {len(filtered)} trades from top politicians")

    orders = copier.process_trades(filtered)

    if orders:
        logger.info(f"Placed {len(orders)} new orders")
        tracker.record_orders(orders)
    else:
        logger.info("No new trades to copy")


def job_status(copier: trade_copier.TradeCopier):
    """Log a portfolio status summary."""
    tracker.print_summary(copier)


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def run_once():
    """Single pass: score then copy."""
    logger.info("Running single pass...")
    copier = trade_copier.TradeCopier()
    job_score_politicians()
    job_copy_trades(copier)
    job_status(copier)


def run_scheduler():
    """Start APScheduler for continuous operation."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    copier = trade_copier.TradeCopier()
    scheduler = BlockingScheduler(timezone="America/New_York")

    # Score politicians once at startup and then daily at 7 AM ET
    scheduler.add_job(
        job_score_politicians,
        CronTrigger(hour=7, minute=0, timezone="America/New_York"),
        id="score_politicians",
        name="Score Politicians",
        replace_existing=True,
    )

    # Copy trades every 30 minutes during market hours (9:00 AM – 4:30 PM ET, Mon-Fri)
    scheduler.add_job(
        lambda: job_copy_trades(copier),
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-16",
            minute=f"0/{config.POLL_INTERVAL_MINUTES}",
            timezone="America/New_York",
        ),
        id="copy_trades",
        name="Copy Trades",
        replace_existing=True,
    )

    # Status summary every day at 4:45 PM ET (after close)
    scheduler.add_job(
        lambda: job_status(copier),
        CronTrigger(hour=16, minute=45, timezone="America/New_York"),
        id="daily_status",
        name="Daily Status",
        replace_existing=True,
    )

    logger.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.name}: {job.trigger}")

    # Run an initial pass immediately
    logger.info("Running initial pass on startup...")
    job_score_politicians()
    job_copy_trades(copier)
    job_status(copier)

    logger.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Politician Copy Trader")
    parser.add_argument("--run", action="store_true", help="Start continuous scheduler")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--score", action="store_true", help="Re-score politicians and exit")
    args = parser.parse_args()

    if args.status:
        copier = trade_copier.TradeCopier()
        tracker.print_summary(copier)
    elif args.score:
        job_score_politicians()
    elif args.run:
        run_scheduler()
    else:
        run_once()


if __name__ == "__main__":
    main()
