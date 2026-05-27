"""
Pre-market validator (runs at 8 AM ET weekdays).
Re-checks Hitman v2 candidates before the 3:50 PM executor fires.

Checks:
  1. Earnings date hasn't already passed
  2. No negative news headlines (fraud, SEC, bankruptcy, etc.)
  3. Adequate liquidity (avg volume > 500k shares)
"""
from datetime import date
from typing import Dict, Any, List, Optional

import yfinance as yf

from utils import (
    get_logger, load_settings, load_json, save_json,
    send_email, check_kill_switch, check_circuit_breakers,
    now_et, today_et, is_weekday,
)
from executor import get_entry_date  # shared 3-trading-day-before-earnings helper

log = get_logger("validator")

NEGATIVE_KEYWORDS = [
    "fraud", "sec investigation", "restatement", "bankruptcy", "delisted",
    "accounting irregularities", "class action", "revenue miss",
    "guidance cut", "layoffs", "fda rejection", "recall", "criminal",
]


def check_news_sentiment(symbol: str) -> tuple[bool, List[str]]:
    """Returns (is_clean, list_of_warnings). Scans 10 most recent headlines."""
    warnings = []
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        for item in news[:10]:
            title = (item.get("title") or "").lower()
            for kw in NEGATIVE_KEYWORDS:
                if kw in title:
                    warnings.append(f"News flag: '{kw}' in '{item.get('title', '')[:60]}'")
    except Exception as e:
        log.debug(f"{symbol}: news check failed — {e}")
    return len(warnings) == 0, warnings


def check_liquidity(symbol: str) -> tuple[bool, str]:
    """Ensure average volume > 500k shares."""
    try:
        info = yf.Ticker(symbol).info or {}
        avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0
        if avg_vol < 500_000:
            return False, f"Low liquidity: avg volume {avg_vol:,}"
    except Exception:
        pass
    return True, "ok"


def validate_recommendation(rec: Dict[str, Any]) -> Dict[str, Any]:
    symbol = rec["symbol"]
    rec = dict(rec)
    rec["validated"] = False
    rec["validation_notes"] = []

    # Entry-window check.
    # The strategy enters 3 trading days before earnings. Once today is PAST
    # that entry date, the window is closed and there is no point keeping this
    # recommendation around — re-validating it daily just wastes API quota and
    # pollutes the queue. Disqualify so it stops being re-checked.
    earnings_date = date.fromisoformat(rec["earnings_date"])
    entry_date    = get_entry_date(earnings_date)
    today         = today_et()
    if today > entry_date:
        days_late = (today - entry_date).days
        reason = (
            f"Entry window closed ({days_late}d late). "
            f"Entry date was {entry_date} (3 trading days before earnings {earnings_date})."
        )
        rec["validation_notes"].append(reason)
        rec["disqualified"] = True
        rec["disqualified_reason"] = "entry_window_closed"
        log.info(f"{symbol}: disqualified — {reason}")
        return rec
    if earnings_date < today:
        # Belt-and-suspenders: also catch the case where earnings already happened
        # (shouldn't fire if the entry-window check above is correct, but cheap to keep)
        rec["validation_notes"].append("Earnings date already passed")
        rec["disqualified"] = True
        rec["disqualified_reason"] = "earnings_passed"
        log.info(f"{symbol}: disqualified — earnings already passed")
        return rec

    # News sentiment check
    news_ok, news_warnings = check_news_sentiment(symbol)
    if not news_ok:
        rec["validation_notes"].extend(news_warnings)
        rec["disqualified"] = True
        log.warning(f"{symbol}: disqualified — negative news: {news_warnings}")
        return rec

    # Liquidity check
    liquidity_ok, liq_note = check_liquidity(symbol)
    if not liquidity_ok:
        rec["validation_notes"].append(liq_note)
        rec["disqualified"] = True
        log.warning(f"{symbol}: disqualified — {liq_note}")
        return rec

    rec["validated"]      = True
    rec["validated_date"] = today_et().isoformat()
    rec["disqualified"]   = False
    log.info(
        f"{symbol}: VALIDATED | beat={rec.get('beat_rate', 0):.0%} "
        f"pu5={rec.get('pu5', 0):.0%} IV={rec.get('iv_proxy', 0):.0%} "
        f"trade={rec.get('trade_type','?')} | earnings={rec['earnings_date']}"
    )
    return rec


def run_validator():
    log.info("=" * 60)
    log.info("VALIDATOR: Pre-market validation starting")
    log.info("=" * 60)
    if not is_weekday():
        log.info("Not a weekday — skipping")
        return
    if check_kill_switch():
        log.warning("Kill switch active — validator aborted")
        return
    cb_reason = check_circuit_breakers(log)
    if cb_reason:
        log.warning(f"Circuit breaker active ({cb_reason}) — no trades today")
        send_email("Circuit Breaker Active", f"No trades today.\nReason: {cb_reason}")
        return

    recommendations = load_json("recommendations.json")
    if not isinstance(recommendations, list):
        recommendations = []

    today_str  = today_et().isoformat()
    to_validate = [
        r for r in recommendations
        if not r.get("executed")
        and not r.get("disqualified")
        and not r.get("validated")
    ]
    log.info(f"Validating {len(to_validate)} candidates...")

    validated_count    = 0
    disqualified_count = 0

    for i, rec in enumerate(recommendations):
        if rec.get("executed") or rec.get("disqualified") or rec not in to_validate:
            continue
        updated = validate_recommendation(rec)
        recommendations[i] = updated
        if updated.get("validated"):
            validated_count += 1
        elif updated.get("disqualified"):
            disqualified_count += 1

    save_json("recommendations.json", recommendations)

    valid_plays = [r for r in recommendations if r.get("validated") and not r.get("executed")]
    lines = []
    for r in valid_plays:
        lines.append(
            f"  {r['symbol']:6s} | {r.get('trade_type','?'):6s} | "
            f"beat={r.get('beat_rate',0):.0%} pu5={r.get('pu5',0):.0%} "
            f"IV={r.get('iv_proxy',0):.0%} | earnings={r['earnings_date']}"
        )
    summary = "\n".join(lines) if lines else "  No validated candidates."

    log.info(f"Validation complete: {validated_count} validated, {disqualified_count} disqualified")
    send_email(
        f"Pre-Market Validation: {validated_count} Hitman v2 plays cleared",
        f"Pre-Market Validation — {today_str}\n\n"
        f"Validated: {validated_count} | Disqualified: {disqualified_count}\n\n"
        f"Cleared for execution at 3:50 PM ET:\n{summary}",
    )


if __name__ == "__main__":
    run_validator()
