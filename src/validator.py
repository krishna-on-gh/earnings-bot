"""
Task 2 — Pre-market validator (runs at 8 AM ET weekdays).
Re-checks recommendations: confirms news sentiment, liquidity,
and no red flags before the 9:35 AM executor fires.
"""
from datetime import date
from typing import Dict, Any, List, Optional

import yfinance as yf

from utils import (
    get_logger, load_settings, load_json, save_json,
    send_email, check_kill_switch, check_circuit_breakers,
    now_et, today_et, is_weekday,
)

log = get_logger("validator")

NEGATIVE_KEYWORDS = [
    "fraud", "sec investigation", "restatement", "bankruptcy", "delisted",
    "accounting irregularities", "class action", "revenue miss",
    "guidance cut", "layoffs", "fda rejection", "recall", "criminal",
]


def check_news_sentiment(symbol: str) -> tuple[bool, List[str]]:
    """Returns (is_clean, list_of_warnings). Uses yfinance news."""
    warnings = []
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        recent = news[:10]
        for item in recent:
            title = (item.get("title") or "").lower()
            for kw in NEGATIVE_KEYWORDS:
                if kw in title:
                    warnings.append(f"News flag: '{kw}' in '{item.get('title', '')[:60]}'")
    except Exception as e:
        log.debug(f"{symbol}: news check failed — {e}")
    return len(warnings) == 0, warnings


def check_price_moved(rec: Dict[str, Any]) -> tuple[bool, str]:
    """Flag if price moved >15% since scan — may invalidate thesis."""
    try:
        ticker = yf.Ticker(rec["symbol"])
        info = ticker.info or {}
        current = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        scan_price = rec.get("current_price", 0)
        if scan_price > 0 and current > 0:
            pct_change = (current - scan_price) / scan_price
            if abs(pct_change) > 0.15:
                return False, f"Price moved {pct_change*100:.1f}% since scan (${scan_price:.2f} → ${current:.2f})"
            rec["current_price"] = round(current, 2)
    except Exception:
        pass
    return True, "ok"


def check_liquidity(symbol: str) -> tuple[bool, str]:
    """Ensure average volume > 500k shares."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
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
    earnings_date = date.fromisoformat(rec["earnings_date"])
    if earnings_date < today_et():
        rec["validation_notes"].append("Earnings date passed — removing")
        rec["disqualified"] = True
        log.info(f"{symbol}: disqualified — earnings already passed")
        return rec
    news_ok, news_warnings = check_news_sentiment(symbol)
    if not news_ok:
        rec["validation_notes"].extend(news_warnings)
        rec["disqualified"] = True
        log.warning(f"{symbol}: disqualified — negative news: {news_warnings}")
        return rec
    price_ok, price_note = check_price_moved(rec)
    if not price_ok:
        rec["validation_notes"].append(price_note)
        log.warning(f"{symbol}: price movement warning — {price_note}")
    liquidity_ok, liq_note = check_liquidity(symbol)
    if not liquidity_ok:
        rec["validation_notes"].append(liq_note)
        rec["disqualified"] = True
        log.warning(f"{symbol}: disqualified — {liq_note}")
        return rec
    rec["validated"] = True
    rec["validated_date"] = today_et().isoformat()
    rec["disqualified"] = False
    log.info(f"{symbol}: VALIDATED — confidence {rec['confidence']}%, earnings {rec['earnings_date']}")
    return rec


def run_validator():
    log.info("=" * 60)
    log.info("VALIDATOR: Pre-market validation starting")
    log.info("=" * 60)
    if not is_weekday():
        log.info("Not a weekday — skipping validation")
        return
    if check_kill_switch():
        log.warning("Kill switch active — validator aborted")
        return
    cb_reason = check_circuit_breakers(log)
    if cb_reason:
        log.warning(f"Circuit breaker active ({cb_reason}) — no new trades today")
        send_email("Circuit Breaker Active", f"No new trades today.\nReason: {cb_reason}")
        return
    recommendations = load_json("recommendations.json")
    if not isinstance(recommendations, list):
        recommendations = []
    today_str = today_et().isoformat()
    to_validate = [
        r for r in recommendations
        if not r.get("executed")
        and not r.get("disqualified")
        and not r.get("validated")
    ]
    log.info(f"Validating {len(to_validate)} recommendations...")
    validated_count = 0
    disqualified_count = 0
    for i, rec in enumerate(recommendations):
        if rec.get("executed") or rec.get("disqualified"):
            continue
        if rec not in to_validate:
            continue
        updated = validate_recommendation(rec)
        recommendations[i] = updated
        if updated.get("validated"):
            validated_count += 1
        elif updated.get("disqualified"):
            disqualified_count += 1
    save_json("recommendations.json", recommendations)
    valid_plays = [r for r in recommendations if r.get("validated") and not r.get("executed")]
    summary_lines = []
    for r in valid_plays:
        summary_lines.append(
            f"  {r['symbol']:6s} | Conf: {r['confidence']:.0f}% | "
            f"Earnings: {r['earnings_date']} | Size: ${r['position_size']} | "
            f"Price: ${r.get('current_price', 0):.2f}"
        )
    summary = "\n".join(summary_lines) if summary_lines else "  No validated plays."
    log.info(f"Validation complete: {validated_count} validated, {disqualified_count} disqualified")
    log.info(f"Ready for execution:\n{summary}")
    send_email(
        f"Pre-Market Validation: {validated_count} plays cleared",
        f"Pre-Market Validation Report — {today_str}\n\n"
        f"Validated: {validated_count} | Disqualified: {disqualified_count}\n\n"
        f"Cleared for execution at 9:35 AM ET:\n{summary}\n\n"
        f"Executor fires in ~1.5 hours.",
    )


if __name__ == "__main__":
    run_validator()
