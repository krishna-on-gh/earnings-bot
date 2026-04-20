"""
Scores and ranks politicians by recent trading activity.

Scoring factors:
  - Activity: number of trades in the scoring window
  - Recency: how recently they last traded (exponential decay)
  - Diversity: trading multiple different tickers (vs. one-offs)
  - Options preference: bonus if they trade options (higher signal)

Scores are persisted to disk so each run builds on history.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from collections import defaultdict

import config

logger = logging.getLogger(__name__)


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def score_politicians(trades: list[dict]) -> list[dict]:
    """
    Given a flat list of trade dicts (from capitol_trades.fetch_recent_trades),
    compute a score for each politician and return a ranked list.
    """
    cutoff = datetime.now() - timedelta(days=config.SCORING_WINDOW_DAYS)
    now = datetime.now()

    by_politician: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_politician[t["politician"]].append(t)

    scores = []
    for politician, ptrades in by_politician.items():
        # Filter to scoring window
        window_trades = []
        for t in ptrades:
            date_str = t.get("filed_date") or t.get("trade_date")
            if date_str:
                try:
                    if datetime.strptime(date_str, "%Y-%m-%d") >= cutoff:
                        window_trades.append(t)
                except ValueError:
                    pass

        if len(window_trades) < config.MIN_TRADES_FOR_CONSIDERATION:
            continue

        # Activity score: more trades = higher score (log-scaled)
        import math
        activity = math.log1p(len(window_trades)) * 10

        # Recency score: days since last trade, exponential decay
        most_recent = None
        for t in window_trades:
            date_str = t.get("filed_date") or t.get("trade_date")
            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    if most_recent is None or dt > most_recent:
                        most_recent = dt
                except ValueError:
                    pass

        if most_recent:
            days_ago = (now - most_recent).days
            recency = max(0, 20 - days_ago)  # 0-20 points, decaying daily
        else:
            recency = 0

        # Diversity: unique tickers traded
        unique_tickers = len({t["ticker"] for t in window_trades})
        diversity = min(unique_tickers * 2, 10)  # cap at 10

        # Options bonus: politicians trading options are worth following more closely
        options_count = sum(1 for t in window_trades if t.get("asset_type") == "option")
        options_bonus = min(options_count * 3, 15)

        total = activity + recency + diversity + options_bonus

        politician_id = window_trades[0].get("politician_id", "")
        scores.append({
            "politician": politician,
            "politician_id": politician_id,
            "score": round(total, 2),
            "trade_count": len(window_trades),
            "unique_tickers": unique_tickers,
            "options_trades": options_count,
            "last_trade_date": most_recent.strftime("%Y-%m-%d") if most_recent else None,
            "score_breakdown": {
                "activity": round(activity, 2),
                "recency": round(recency, 2),
                "diversity": round(diversity, 2),
                "options_bonus": round(options_bonus, 2),
            },
        })

    scores.sort(key=lambda x: x["score"], reverse=True)

    _save_json(config.POLITICIAN_SCORES_FILE, {
        "updated_at": now.isoformat(),
        "scores": scores,
    })

    top = scores[:config.TOP_N_POLITICIANS]
    logger.info(f"Top {len(top)} politicians:")
    for s in top:
        logger.info(
            f"  {s['politician']:30s}  score={s['score']:6.1f}  "
            f"trades={s['trade_count']}  options={s['options_trades']}  "
            f"last={s['last_trade_date']}"
        )

    return scores


def get_top_politicians() -> list[dict]:
    """Return the cached top-N politicians, or empty list if not scored yet."""
    data = _load_json(config.POLITICIAN_SCORES_FILE, {})
    scores = data.get("scores", [])
    return scores[:config.TOP_N_POLITICIANS]


def get_trades_for_top_politicians(all_trades: list[dict]) -> list[dict]:
    """Filter all_trades to only those from top-scored politicians."""
    top = {p["politician"] for p in get_top_politicians()}
    return [t for t in all_trades if t["politician"] in top]
