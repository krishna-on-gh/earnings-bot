"""
Task 6 — Weekly learner (runs at 9 PM ET Sundays).
Analyzes past week's trades, identifies what scoring factors
predicted wins vs losses, adjusts weights in learning_weights.json.
"""
from datetime import date, timedelta
from typing import Dict, Any, List, Optional, Tuple

from utils import (
    get_logger, load_settings, load_json, save_json,
    send_email, now_et, today_et,
)

log = get_logger("learner")

WEIGHT_KEYS = [
    "beat_rate_high_bonus",
    "revenue_growth_bonus",
    "momentum_bonus",
    "tech_sector_bonus",
    "mega_cap_bonus",
    "beat_rate_low_penalty",
    "declining_revenue_penalty",
    "negative_momentum_penalty",
]

MAX_ADJUSTMENT = 5.0
ADJUSTMENT_STEP = 2.0


def get_week_trades(trades: List[Dict], weeks_ago: int = 0) -> List[Dict]:
    today = today_et()
    week_end = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_ago)
    week_start = week_end - timedelta(days=7)
    return [
        t for t in trades
        if t.get("entry_date")
        and week_start <= date.fromisoformat(t["entry_date"]) < week_end
    ]


def classify_outcome(trade: Dict) -> Optional[str]:
    if trade.get("status") != "closed":
        return None
    pnl_pct = trade.get("pnl_pct", 0) or 0
    if pnl_pct >= 20:
        return "big_win"
    elif pnl_pct > 0:
        return "win"
    elif pnl_pct > -20:
        return "loss"
    else:
        return "big_loss"


def analyze_factor_performance(trades: List[Dict]) -> Dict[str, Dict[str, float]]:
    """
    For each scoring factor, compute average outcome when factor was present.
    Returns {factor: {avg_pnl_pct, count, win_rate}}.
    """
    factor_data: Dict[str, List[float]] = {
        "beat_rate_high": [],
        "revenue_growth_high": [],
        "momentum_high": [],
        "tech_sector": [],
        "mega_cap": [],
        "beat_rate_low": [],
        "declining_revenue": [],
        "negative_momentum": [],
    }
    closed = [t for t in trades if t.get("status") == "closed"]
    for trade in closed:
        pnl_pct = trade.get("pnl_pct", 0) or 0
        bd = trade.get("confidence_breakdown") or {}
        if bd.get("beat_rate_high"):
            factor_data["beat_rate_high"].append(pnl_pct)
        if bd.get("revenue_growth_high"):
            factor_data["revenue_growth_high"].append(pnl_pct)
        if bd.get("momentum_high"):
            factor_data["momentum_high"].append(pnl_pct)
        if bd.get("tech_sector"):
            factor_data["tech_sector"].append(pnl_pct)
        if bd.get("mega_cap"):
            factor_data["mega_cap"].append(pnl_pct)
        if bd.get("beat_rate_low"):
            factor_data["beat_rate_low"].append(pnl_pct)
        if bd.get("declining_revenue"):
            factor_data["declining_revenue"].append(pnl_pct)
        if bd.get("negative_momentum"):
            factor_data["negative_momentum"].append(pnl_pct)
        beat_rate = trade.get("beat_rate")
        revenue_growth = trade.get("revenue_growth_yoy")
        momentum = trade.get("momentum_30d")
        if beat_rate is not None:
            if beat_rate >= 0.75 and "beat_rate_high" not in bd:
                factor_data["beat_rate_high"].append(pnl_pct)
            elif beat_rate < 0.50 and "beat_rate_low" not in bd:
                factor_data["beat_rate_low"].append(pnl_pct)
        if revenue_growth is not None:
            if revenue_growth > 0.20 and "revenue_growth_high" not in bd:
                factor_data["revenue_growth_high"].append(pnl_pct)
            elif revenue_growth < 0 and "declining_revenue" not in bd:
                factor_data["declining_revenue"].append(pnl_pct)
        if momentum is not None:
            if momentum > 0.10 and "momentum_high" not in bd:
                factor_data["momentum_high"].append(pnl_pct)
            elif momentum < -0.10 and "negative_momentum" not in bd:
                factor_data["negative_momentum"].append(pnl_pct)
    results = {}
    for factor, values in factor_data.items():
        if values:
            avg = sum(values) / len(values)
            wins = sum(1 for v in values if v > 0)
            results[factor] = {
                "avg_pnl_pct": round(avg, 2),
                "count": len(values),
                "win_rate": round(wins / len(values) * 100, 1),
            }
    return results


def compute_weight_adjustments(
    factor_performance: Dict[str, Dict],
    current_weights: Dict[str, float],
) -> Dict[str, float]:
    """
    Nudge weights based on factor performance.
    Bonuses: if avg_pnl > 10% → increase, if avg_pnl < 0 → decrease
    Penalties: if avg_pnl > 0 → reduce penalty severity, if avg_pnl < -10% → increase penalty severity
    """
    adjustments = {}
    bonus_factors = {
        "beat_rate_high": "beat_rate_high_bonus",
        "revenue_growth_high": "revenue_growth_bonus",
        "momentum_high": "momentum_bonus",
        "tech_sector": "tech_sector_bonus",
        "mega_cap": "mega_cap_bonus",
    }
    penalty_factors = {
        "beat_rate_low": "beat_rate_low_penalty",
        "declining_revenue": "declining_revenue_penalty",
        "negative_momentum": "negative_momentum_penalty",
    }
    for factor_key, weight_key in bonus_factors.items():
        if factor_key not in factor_performance:
            continue
        perf = factor_performance[factor_key]
        current = current_weights.get(weight_key, 0)
        if perf["avg_pnl_pct"] > 10 and perf["win_rate"] > 60:
            adj = min(ADJUSTMENT_STEP, MAX_ADJUSTMENT - (current - current_weights.get(weight_key, current)))
            adjustments[weight_key] = round(current + adj, 1)
            log.info(f"  {weight_key}: +{adj} (factor performing well: avg {perf['avg_pnl_pct']:.1f}%)")
        elif perf["avg_pnl_pct"] < 0 and perf["win_rate"] < 40:
            adj = ADJUSTMENT_STEP
            adjustments[weight_key] = round(max(0, current - adj), 1)
            log.info(f"  {weight_key}: -{adj} (factor underperforming: avg {perf['avg_pnl_pct']:.1f}%)")
    for factor_key, weight_key in penalty_factors.items():
        if factor_key not in factor_performance:
            continue
        perf = factor_performance[factor_key]
        current = current_weights.get(weight_key, 0)
        if perf["avg_pnl_pct"] < -10 and perf["win_rate"] < 30:
            adj = ADJUSTMENT_STEP
            adjustments[weight_key] = round(min(-5, current - adj), 1)
            log.info(f"  {weight_key}: {-adj} more severe (strong predictor: avg {perf['avg_pnl_pct']:.1f}%)")
        elif perf["avg_pnl_pct"] > 5:
            adj = ADJUSTMENT_STEP
            adjustments[weight_key] = round(min(0, current + adj), 1)
            log.info(f"  {weight_key}: +{adj} less severe (weak predictor: avg {perf['avg_pnl_pct']:.1f}%)")
    return adjustments


def run_learner():
    log.info("=" * 60)
    log.info("LEARNER: Weekly strategy analysis starting")
    log.info("=" * 60)
    trades = load_json("trades_history.json") or []
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    if len(closed_trades) < 3:
        log.info(f"Only {len(closed_trades)} closed trades — need at least 3 for meaningful analysis. Skipping adjustments.")
        send_email(
            "Weekly Learner: Insufficient Data",
            f"Only {len(closed_trades)} closed trades so far. Analysis requires at least 3.\n"
            f"Weights unchanged. Check back next week.",
        )
        return
    week_trades = get_week_trades(trades, weeks_ago=0)
    week_closed = [t for t in week_trades if t.get("status") == "closed"]
    week_pnl = sum(t.get("pnl", 0) or 0 for t in week_closed)
    week_wins = sum(1 for t in week_closed if (t.get("pnl") or 0) > 0)
    week_win_rate = (week_wins / len(week_closed) * 100) if week_closed else 0
    total_pnl = sum(t.get("pnl", 0) or 0 for t in closed_trades)
    total_wins = sum(1 for t in closed_trades if (t.get("pnl") or 0) > 0)
    total_win_rate = (total_wins / len(closed_trades) * 100) if closed_trades else 0
    log.info(f"This week: {len(week_closed)} closed, P&L ${week_pnl:.2f}, win rate {week_win_rate:.0f}%")
    log.info(f"All time:  {len(closed_trades)} closed, P&L ${total_pnl:.2f}, win rate {total_win_rate:.0f}%")
    factor_performance = analyze_factor_performance(closed_trades)
    log.info("Factor performance analysis:")
    for factor, perf in factor_performance.items():
        log.info(f"  {factor}: avg {perf['avg_pnl_pct']:+.1f}% | {perf['count']} trades | win rate {perf['win_rate']:.0f}%")
    weights_data = load_json("learning_weights.json")
    if not isinstance(weights_data, dict):
        settings = load_settings()
        weights_data = dict(settings["scoring"])
    current_weights = {k: weights_data.get(k, 0) for k in WEIGHT_KEYS + ["base_score"]}
    if len(closed_trades) >= 5:
        log.info("Computing weight adjustments...")
        adjustments = compute_weight_adjustments(factor_performance, current_weights)
        if adjustments:
            for key, new_val in adjustments.items():
                old_val = current_weights.get(key, 0)
                current_weights[key] = new_val
                log.info(f"  Weight adjusted: {key}: {old_val} → {new_val}")
        else:
            log.info("  No adjustments needed — weights performing well")
    else:
        adjustments = {}
        log.info(f"Only {len(closed_trades)} closed trades — not enough for weight adjustments (need 5)")
    history_entry = {
        "week_ending": today_et().isoformat(),
        "week_trades": len(week_closed),
        "week_pnl": round(week_pnl, 2),
        "week_win_rate": round(week_win_rate, 1),
        "total_trades": len(closed_trades),
        "total_pnl": round(total_pnl, 2),
        "total_win_rate": round(total_win_rate, 1),
        "factor_performance": factor_performance,
        "adjustments_made": adjustments,
    }
    weights_data.update(current_weights)
    weights_data["version"] = weights_data.get("version", 1) + 1
    weights_data["last_updated"] = now_et().isoformat()
    history = weights_data.get("adjustment_history", [])
    history.append(history_entry)
    weights_data["adjustment_history"] = history[-10:]
    save_json("learning_weights.json", weights_data)
    log.info(f"Learning weights updated (version {weights_data['version']})")
    best_trade = max(closed_trades, key=lambda t: t.get("pnl_pct") or -999, default=None)
    worst_trade = min(closed_trades, key=lambda t: t.get("pnl_pct") or 999, default=None)
    adjustment_summary = "\n".join(
        f"  {k}: {current_weights.get(k, '?')} (was {v})"
        for k, v in ((k, load_settings()["scoring"].get(k, 0)) for k in adjustments)
    ) or "  No adjustments this week."
    factor_summary = "\n".join(
        f"  {f}: avg {p['avg_pnl_pct']:+.1f}% over {p['count']} trades (win rate {p['win_rate']:.0f}%)"
        for f, p in sorted(factor_performance.items(), key=lambda x: x[1]["avg_pnl_pct"], reverse=True)
    ) or "  No factor data yet."
    report = (
        f"WEEKLY LEARNING REPORT — {today_et().isoformat()}\n"
        f"{'=' * 60}\n\n"
        f"THIS WEEK:\n"
        f"  Trades: {len(week_closed)} | P&L: ${week_pnl:+.2f} | Win Rate: {week_win_rate:.0f}%\n\n"
        f"ALL TIME:\n"
        f"  Trades: {len(closed_trades)} | P&L: ${total_pnl:+.2f} | Win Rate: {total_win_rate:.0f}%\n"
        f"  Best:  {best_trade['symbol'] if best_trade else 'N/A'} {best_trade.get('pnl_pct', 0):+.1f}%\n"
        f"  Worst: {worst_trade['symbol'] if worst_trade else 'N/A'} {worst_trade.get('pnl_pct', 0):+.1f}%\n\n"
        f"FACTOR PERFORMANCE:\n{factor_summary}\n\n"
        f"WEIGHT ADJUSTMENTS:\n{adjustment_summary}\n\n"
        f"Weights version: {weights_data['version']}\n"
        f"Scanner will use updated weights on next run."
    )
    log.info("\n" + report)
    send_email(
        f"Weekly Learning: Win Rate {total_win_rate:.0f}% | P&L ${total_pnl:+.2f}",
        report,
    )


if __name__ == "__main__":
    run_learner()
