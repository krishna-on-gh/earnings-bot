"""
Learner — Hitman v2 weekly performance analyst.
Runs every Monday at 6 AM ET.

Reads all closed trades and produces a diagnostic breakdown:
  - Overall win rate and P&L
  - Win rate by beat_rate bucket
  - Win rate by pu5 bucket
  - Win rate by momentum bucket
  - Win rate by IV bucket
  - Win rate by trade_type (call vs spread)
  - Win rate by sector
  - Filter correlation summary (which filters matter most)

NO changes are made to settings, thresholds, or any data files.
This is a read-only log. All decisions are made by the human.
"""
from typing import List, Dict, Any, Optional, Tuple
from datetime import date

from utils import (
    get_logger, load_json, save_json,
    send_email, today_et, now_et,
    REPORTS_DIR,
)

log = get_logger("learner")

MIN_TRADES_FOR_ANALYSIS = 5   # minimum closed trades before any bucket analysis


# ── Bucket helpers ────────────────────────────────────────────────────────────
def bucket_win_rate(
    trades: List[Dict],
    field: str,
    buckets: List[Tuple[Optional[float], Optional[float], str]],
) -> List[Dict]:
    """
    For each (low, high, label) bucket, compute win rate and sample size.
    low=None means no lower bound; high=None means no upper bound.
    """
    results = []
    for low, high, label in buckets:
        subset = []
        for t in trades:
            val = t.get(field)
            if val is None:
                continue
            if low is not None and val < low:
                continue
            if high is not None and val >= high:
                continue
            subset.append(t)
        if not subset:
            results.append({"label": label, "n": 0, "win_rate": None, "pnl": None})
            continue
        wins = sum(1 for t in subset if (t.get("pnl") or 0) > 0)
        total_pnl = sum(t.get("pnl") or 0 for t in subset)
        results.append({
            "label":    label,
            "n":        len(subset),
            "win_rate": round(wins / len(subset) * 100, 1),
            "pnl":      round(total_pnl, 2),
        })
    return results


def fmt_bucket_table(rows: List[Dict], field_label: str) -> str:
    lines = [f"  {field_label:<22}  n    WR%    P&L"]
    lines.append("  " + "-" * 44)
    for r in rows:
        if r["n"] == 0:
            lines.append(f"  {r['label']:<22}  0    —      —")
        else:
            flag = " ◄" if r["win_rate"] is not None and r["win_rate"] >= 70 else (
                   " ✗" if r["win_rate"] is not None and r["win_rate"] < 40 else "")
            lines.append(
                f"  {r['label']:<22}  {r['n']:<4} {r['win_rate']:>5.1f}%  "
                f"${r['pnl']:>+,.0f}{flag}"
            )
    return "\n".join(lines)


# ── Filter correlation ─────────────────────────────────────────────────────────
def filter_correlation(trades: List[Dict]) -> str:
    """
    For each filter, compute correlation between filter value and win (1/0).
    Simple Pearson r — positive means higher value → more wins.
    """
    fields = [
        ("beat_rate",    "Beat rate"),
        ("pu5",          "pu5"),
        ("momentum_30d", "Momentum"),
        ("iv_proxy",     "IV proxy"),
    ]
    lines = ["  Filter          Corr   Signal"]
    lines.append("  " + "-" * 38)
    for field, label in fields:
        pairs = [(t[field], 1 if (t.get("pnl") or 0) > 0 else 0)
                 for t in trades if t.get(field) is not None]
        if len(pairs) < 3:
            lines.append(f"  {label:<16} —      (insufficient data)")
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        n  = len(pairs)
        mx, my = sum(xs) / n, sum(ys) / n
        num  = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den  = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        r    = round(num / den, 3) if den else 0
        if abs(r) < 0.1:
            signal = "weak / no signal"
        elif r > 0:
            signal = f"higher {label.lower()} → more wins"
        else:
            signal = f"lower {label.lower()} → more wins"
        lines.append(f"  {label:<16} {r:>+.3f}  {signal}")
    return "\n".join(lines)


# ── Main analysis ─────────────────────────────────────────────────────────────
def run_learner():
    log.info("=" * 60)
    log.info("LEARNER: Hitman v2 weekly performance analysis")
    log.info("=" * 60)

    trades   = load_json("trades_history.json") or []
    closed   = [t for t in trades if t.get("status") == "closed"]
    open_pos = [t for t in trades if t.get("status") == "open"]

    total_pnl = sum(t.get("pnl") or 0 for t in closed)
    wins      = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    losses    = len(closed) - wins
    win_rate  = (wins / len(closed) * 100) if closed else 0

    today_str = today_et().isoformat()

    lines = [
        f"HITMAN V2 — WEEKLY LEARNING REPORT",
        f"Generated: {now_et().strftime('%Y-%m-%d %H:%M ET')}",
        "=" * 60,
        "",
        "OVERALL PERFORMANCE",
        "-" * 40,
        f"  Closed trades:   {len(closed)}",
        f"  Open positions:  {len(open_pos)}",
        f"  Win / Loss:      {wins}W / {losses}L",
        f"  Win rate:        {win_rate:.1f}%",
        f"  Total P&L:       ${total_pnl:+,.2f}",
        "",
    ]

    if len(closed) < MIN_TRADES_FOR_ANALYSIS:
        lines += [
            f"NOTE: Only {len(closed)} closed trade(s). Need at least "
            f"{MIN_TRADES_FOR_ANALYSIS} for bucket analysis.",
            "Check back next week.",
            "",
        ]
        report = "\n".join(lines)
        log.info("\n" + report)
        send_email(
            f"Weekly Learning Report — {today_str} ({len(closed)} trades, insufficient data)",
            report,
        )
        return

    # ── Beat rate buckets ─────────────────────────────────────────────────────
    lines.append("BEAT RATE  (min threshold: 62%)")
    lines.append(fmt_bucket_table(
        bucket_win_rate(closed, "beat_rate", [
            (0.62, 0.70, "62–70%"),
            (0.70, 0.80, "70–80%"),
            (0.80, 0.90, "80–90%"),
            (0.90, None, "90–100%"),
        ]),
        "Range"
    ))
    lines.append("")

    # ── pu5 buckets ───────────────────────────────────────────────────────────
    lines.append("pu5  (min threshold: 40%)")
    lines.append(fmt_bucket_table(
        bucket_win_rate(closed, "pu5", [
            (0.40, 0.50, "40–50%"),
            (0.50, 0.65, "50–65%"),
            (0.65, 0.80, "65–80%"),
            (0.80, None, "80–100%"),
        ]),
        "Range"
    ))
    lines.append("")

    # ── Momentum buckets ──────────────────────────────────────────────────────
    lines.append("MOMENTUM  (allowed range: +1% to +30%)")
    lines.append(fmt_bucket_table(
        bucket_win_rate(closed, "momentum_30d", [
            (0.01, 0.08, "+1–8%"),
            (0.08, 0.15, "+8–15%"),
            (0.15, 0.22, "+15–22%"),
            (0.22, None, "+22–30%"),
        ]),
        "Range"
    ))
    lines.append("")

    # ── IV buckets ────────────────────────────────────────────────────────────
    lines.append("IV PROXY  (max threshold: 80%)")
    lines.append(fmt_bucket_table(
        bucket_win_rate(closed, "iv_proxy", [
            (None, 0.40, "< 40%"),
            (0.40, 0.55, "40–55%  (calls)"),
            (0.55, 0.70, "55–70%  (spreads)"),
            (0.70, 0.80, "70–80%  (spreads)"),
        ]),
        "Range"
    ))
    lines.append("")

    # ── Trade type ────────────────────────────────────────────────────────────
    lines.append("TRADE TYPE")
    lines.append(fmt_bucket_table(
        [
            {
                "label": tt,
                "n": len([t for t in closed if t.get("trade_type") == tt]),
                "win_rate": round(
                    sum(1 for t in closed if t.get("trade_type") == tt and (t.get("pnl") or 0) > 0) /
                    max(len([t for t in closed if t.get("trade_type") == tt]), 1) * 100, 1
                ),
                "pnl": sum(t.get("pnl") or 0 for t in closed if t.get("trade_type") == tt),
            }
            for tt in ["call", "spread"]
        ],
        "Type"
    ))
    lines.append("")

    # ── Sector breakdown ─────────────────────────────────────────────────────
    sectors = sorted(set(t.get("sector", "Unknown") for t in closed))
    lines.append("SECTOR BREAKDOWN")
    sector_rows = []
    for s in sectors:
        st = [t for t in closed if t.get("sector", "Unknown") == s]
        sw = sum(1 for t in st if (t.get("pnl") or 0) > 0)
        sector_rows.append({
            "label":    s[:22],
            "n":        len(st),
            "win_rate": round(sw / len(st) * 100, 1) if st else None,
            "pnl":      sum(t.get("pnl") or 0 for t in st),
        })
    lines.append(fmt_bucket_table(sector_rows, "Sector"))
    lines.append("")

    # ── Filter correlation ────────────────────────────────────────────────────
    lines.append("FILTER CORRELATION  (higher = filter predicts wins)")
    lines.append(filter_correlation(closed))
    lines.append("")

    # ── Observations ─────────────────────────────────────────────────────────
    lines.append("OBSERVATIONS  (data-driven flags, no action taken)")
    lines.append("-" * 40)
    obs = []

    # Flag any bucket with ≥3 trades and win rate < 35%
    for field, label, buckets in [
        ("beat_rate",    "beat_rate", [(0.62,0.70,"62–70%"),(0.70,0.80,"70–80%"),(0.80,0.90,"80–90%"),(0.90,None,"90–100%")]),
        ("pu5",          "pu5",       [(0.40,0.50,"40–50%"),(0.50,0.65,"50–65%"),(0.65,0.80,"65–80%"),(0.80,None,"80–100%")]),
        ("momentum_30d", "momentum",  [(0.01,0.08,"+1–8%"),(0.08,0.15,"+8–15%"),(0.15,0.22,"+15–22%"),(0.22,None,"+22–30%")]),
        ("iv_proxy",     "IV",        [(None,0.40,"<40%"),(0.40,0.55,"40–55%"),(0.55,0.70,"55–70%"),(0.70,0.80,"70–80%")]),
    ]:
        rows = bucket_win_rate(closed, field, buckets)
        for r in rows:
            if r["n"] >= 3 and r["win_rate"] is not None and r["win_rate"] < 35:
                obs.append(f"  ⚠  {label} {r['label']} is underperforming ({r['win_rate']:.0f}% WR on {r['n']} trades) — consider tightening threshold")
            if r["n"] >= 3 and r["win_rate"] is not None and r["win_rate"] >= 75:
                obs.append(f"  ◄  {label} {r['label']} is outperforming ({r['win_rate']:.0f}% WR on {r['n']} trades) — strong zone")

    if not obs:
        obs.append("  No significant signals yet — keep accumulating trades.")
    lines += obs
    lines += ["", "NOTE: No changes were made. All decisions are yours.", ""]

    report = "\n".join(lines)
    log.info("\n" + report)

    # Save weekly report locally
    weekly_dir = REPORTS_DIR / "Weekly Learning"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    report_file = weekly_dir / f"learning_{today_str}.txt"
    report_file.write_text(report, encoding="utf-8")
    log.info(f"Report saved: {report_file}")

    send_email(
        f"Weekly Learning Report — {today_str} | {win_rate:.0f}% WR | ${total_pnl:+,.0f}",
        report,
    )


if __name__ == "__main__":
    run_learner()
