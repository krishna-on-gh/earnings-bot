"""
Task 1 — Nightly earnings scanner (runs at 11 PM ET daily).
Scans S&P 500 + major stocks for earnings in next 14 days,
scores each eligible stock, saves recommendations.json.
"""
import time
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

import yfinance as yf
import pandas as pd

from utils import (
    get_logger, load_settings, load_json, save_json,
    send_email, check_kill_switch, now_et, today_et,
)

log = get_logger("scanner")

SP500_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

EXTRA_LARGE_CAPS = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
    "BRK-B", "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "HD", "PG",
    "CVX", "ABBV", "MRK", "COST", "KO", "PEP", "BAC", "AVGO", "TMO",
    "CSCO", "ACN", "ABT", "MCD", "CRM", "ADBE", "LIN", "DHR", "TXN",
    "NKE", "NEE", "PM", "ORCL", "QCOM", "HON", "IBM", "AMD", "INTC",
    "GE", "INTU", "MS", "GS", "SPGI", "AMAT", "NOW", "ISRG", "CAT",
    "BKNG", "AXP", "LRCX", "MU", "SYK", "ZTS", "ADI", "GILD", "REGN",
    "ABNB", "COIN", "SNOW", "UBER", "LYFT", "PLTR", "RIVN", "LCID",
    "SHOP", "SQ", "ROKU", "TWLO", "DDOG", "NET", "CRWD", "ZS", "OKTA",
    "PANW", "FTNT", "MDB", "ESTC", "U", "RBLX", "HOOD", "SOFI",
]


def get_sp500_tickers() -> List[str]:
    try:
        tables = pd.read_html(SP500_WIKI)
        tickers = tables[0]["Symbol"].tolist()
        tickers = [t.replace(".", "-") for t in tickers]
        log.info(f"Fetched {len(tickers)} S&P 500 tickers from Wikipedia")
        return tickers
    except Exception as e:
        log.warning(f"Could not fetch S&P 500 from Wikipedia: {e}. Using fallback list.")
        return EXTRA_LARGE_CAPS


def get_all_tickers() -> List[str]:
    sp500 = get_sp500_tickers()
    combined = list(dict.fromkeys(sp500 + EXTRA_LARGE_CAPS))
    log.info(f"Total unique tickers to scan: {len(combined)}")
    return combined


def get_earnings_date(ticker: yf.Ticker, symbol: str) -> Optional[date]:
    """Return the next earnings date if within scan window, else None."""
    settings = load_settings()
    window = settings["thresholds"]["earnings_window_days"]
    today = today_et()
    horizon = today + timedelta(days=window)
    try:
        cal = ticker.calendar
        if cal is not None and len(cal) > 0:
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    if isinstance(ed, (list, tuple)):
                        ed = ed[0]
                    ed_date = pd.Timestamp(ed).date()
                    if today <= ed_date <= horizon:
                        return ed_date
            elif isinstance(cal, pd.DataFrame):
                if "Earnings Date" in cal.columns:
                    ed_date = pd.Timestamp(cal["Earnings Date"].iloc[0]).date()
                    if today <= ed_date <= horizon:
                        return ed_date
    except Exception:
        pass
    try:
        edf = ticker.earnings_dates
        if edf is not None and not edf.empty:
            future = edf[edf.index.tz_localize(None) >= pd.Timestamp(today)]
            future = future[future.index.tz_localize(None) <= pd.Timestamp(horizon)]
            if not future.empty:
                return future.index[0].date()
    except Exception:
        pass
    return None


def check_eligibility(symbol: str, info: Dict[str, Any]) -> tuple[bool, str]:
    settings = load_settings()
    mega_caps = settings["mega_cap_exceptions"]
    if symbol in mega_caps:
        pass
    else:
        ipo_epoch = info.get("firstTradeDateEpochUtc") or info.get("ipoDate")
        if ipo_epoch:
            try:
                if isinstance(ipo_epoch, (int, float)):
                    ipo_dt = date.fromtimestamp(ipo_epoch)
                else:
                    ipo_dt = date.fromisoformat(str(ipo_epoch)[:10])
                years_old = (today_et() - ipo_dt).days / 365.25
                if years_old < 5:
                    return False, f"Too young ({years_old:.1f} years since IPO)"
            except Exception:
                pass
    market_cap = info.get("marketCap", 0) or 0
    min_cap = settings["thresholds"]["min_market_cap_b"] * 1e9
    if market_cap < min_cap:
        return False, f"Market cap ${market_cap/1e9:.1f}B below ${settings['thresholds']['min_market_cap_b']}B minimum"
    return True, "eligible"


def get_historical_beat_rate(ticker: yf.Ticker) -> Optional[float]:
    """Fraction of recent quarters where EPS beat estimate."""
    try:
        history = ticker.earnings_history
        if history is not None and not history.empty and len(history) >= 4:
            recent = history.tail(8)
            beats = (recent["epsActual"] > recent["epsEstimate"]).sum()
            return beats / len(recent)
    except Exception:
        pass
    try:
        hist = ticker.earnings_history
        if hist is not None and not hist.empty:
            recent = hist.tail(4)
            beats = (recent.get("epsActual", pd.Series()) > recent.get("epsEstimate", pd.Series())).sum()
            return beats / max(len(recent), 1)
    except Exception:
        pass
    return None


def get_revenue_growth_yoy(ticker: yf.Ticker) -> Optional[float]:
    """YoY revenue growth (most recent annual vs prior year)."""
    try:
        fs = ticker.financials
        if fs is not None and not fs.empty and "Total Revenue" in fs.index:
            rev = fs.loc["Total Revenue"].dropna()
            if len(rev) >= 2:
                return float((rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1]))
    except Exception:
        pass
    try:
        fs = ticker.income_stmt
        if fs is not None and not fs.empty:
            rev_rows = [r for r in fs.index if "Revenue" in str(r) or "revenue" in str(r).lower()]
            if rev_rows:
                rev = fs.loc[rev_rows[0]].dropna()
                if len(rev) >= 2:
                    return float((rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1]))
    except Exception:
        pass
    return None


def get_momentum_30d(ticker: yf.Ticker) -> Optional[float]:
    """30-day price momentum."""
    try:
        hist = ticker.history(period="40d")
        if hist is not None and len(hist) >= 20:
            current = hist["Close"].iloc[-1]
            past = hist["Close"].iloc[-21]
            return float((current - past) / past)
    except Exception:
        pass
    return None


def check_bankruptcy_risk(ticker: yf.Ticker, market_cap: float) -> bool:
    """Returns True if OK (no bankruptcy-level losses)."""
    try:
        fs = ticker.financials
        if fs is None or fs.empty:
            return True
        net_income_rows = [r for r in fs.index if "Net Income" in str(r)]
        if not net_income_rows:
            return True
        net_income = fs.loc[net_income_rows[0]].dropna()
        for val in net_income.values:
            if market_cap > 0 and val < -(0.5 * market_cap):
                return False
    except Exception:
        pass
    return True


def calculate_confidence(
    symbol: str,
    info: Dict[str, Any],
    beat_rate: Optional[float],
    revenue_growth: Optional[float],
    momentum: Optional[float],
) -> tuple[float, Dict[str, Any]]:
    settings = load_settings()
    w = settings["scoring"]
    score = w["base_score"]
    breakdown = {"base": w["base_score"]}
    if beat_rate is not None:
        if beat_rate >= 0.75:
            score += w["beat_rate_high_bonus"]
            breakdown["beat_rate_high"] = w["beat_rate_high_bonus"]
        elif beat_rate < 0.50:
            score += w["beat_rate_low_penalty"]
            breakdown["beat_rate_low"] = w["beat_rate_low_penalty"]
    if revenue_growth is not None:
        if revenue_growth > 0.20:
            score += w["revenue_growth_bonus"]
            breakdown["revenue_growth_high"] = w["revenue_growth_bonus"]
        elif revenue_growth < 0:
            score += w["declining_revenue_penalty"]
            breakdown["declining_revenue"] = w["declining_revenue_penalty"]
    if momentum is not None:
        if momentum > 0.10:
            score += w["momentum_bonus"]
            breakdown["momentum_high"] = w["momentum_bonus"]
        elif momentum < -0.10:
            score += w["negative_momentum_penalty"]
            breakdown["negative_momentum"] = w["negative_momentum_penalty"]
    sector = info.get("sector", "") or ""
    tech_sectors = settings.get("tech_sectors", [])
    if any(s.lower() in sector.lower() for s in tech_sectors):
        score += w["tech_sector_bonus"]
        breakdown["tech_sector"] = w["tech_sector_bonus"]
    market_cap = info.get("marketCap", 0) or 0
    if market_cap > 100e9:
        score += w["mega_cap_bonus"]
        breakdown["mega_cap"] = w["mega_cap_bonus"]
    score = max(0, min(100, score))
    return score, breakdown


def analyze_stock(symbol: str, earnings_date: date) -> Optional[Dict[str, Any]]:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        eligible, reason = check_eligibility(symbol, info)
        if not eligible:
            log.debug(f"{symbol}: ineligible — {reason}")
            return None
        market_cap = info.get("marketCap", 0) or 0
        if not check_bankruptcy_risk(ticker, market_cap):
            log.info(f"{symbol}: skipped — bankruptcy-level losses detected")
            return None
        beat_rate = get_historical_beat_rate(ticker)
        revenue_growth = get_revenue_growth_yoy(ticker)
        momentum = get_momentum_30d(ticker)
        confidence, breakdown = calculate_confidence(
            symbol, info, beat_rate, revenue_growth, momentum
        )
        settings = load_settings()
        min_conf = settings["thresholds"]["min_confidence"]
        if confidence < min_conf:
            log.info(f"{symbol}: confidence {confidence:.0f}% below threshold {min_conf}%")
            return None
        if confidence >= 95:
            pos_size = settings["budget"]["high_confidence_position_size"]
        elif confidence >= 90:
            pos_size = settings["budget"]["mid_confidence_position_size"]
        else:
            pos_size = settings["budget"].get("low_confidence_position_size", 500)
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        result = {
            "id": str(uuid.uuid4())[:8],
            "symbol": symbol,
            "company": info.get("longName", symbol),
            "earnings_date": earnings_date.isoformat(),
            "confidence": round(confidence, 1),
            "confidence_breakdown": breakdown,
            "market_cap_b": round(market_cap / 1e9, 1),
            "sector": info.get("sector", "Unknown"),
            "beat_rate": round(beat_rate, 3) if beat_rate is not None else None,
            "revenue_growth_yoy": round(revenue_growth, 3) if revenue_growth is not None else None,
            "momentum_30d": round(momentum, 3) if momentum is not None else None,
            "current_price": round(current_price, 2),
            "position_size": pos_size,
            "validated": False,
            "executed": False,
            "scan_date": today_et().isoformat(),
            "notes": [],
        }
        log.info(
            f"{symbol}: confidence={confidence:.0f}% | beat_rate={beat_rate} | "
            f"rev_growth={revenue_growth} | momentum={momentum} | earnings={earnings_date}"
        )
        return result
    except Exception as e:
        log.error(f"{symbol}: analysis failed — {e}")
        return None


def run_scanner():
    log.info("=" * 60)
    log.info("SCANNER: Starting nightly earnings scan")
    log.info("=" * 60)
    if check_kill_switch():
        log.warning("Kill switch active — scanner aborted")
        return
    tickers = get_all_tickers()
    earnings_candidates: Dict[str, date] = {}
    log.info(f"Checking earnings dates for {len(tickers)} tickers...")
    for i, symbol in enumerate(tickers):
        try:
            t = yf.Ticker(symbol)
            ed = get_earnings_date(t, symbol)
            if ed:
                earnings_candidates[symbol] = ed
                log.info(f"  {symbol}: earnings on {ed}")
            if i > 0 and i % 50 == 0:
                log.info(f"  Progress: {i}/{len(tickers)} checked, {len(earnings_candidates)} candidates found")
                time.sleep(1)
        except Exception as e:
            log.debug(f"  {symbol}: date check failed — {e}")
        time.sleep(0.1)
    log.info(f"Found {len(earnings_candidates)} stocks with earnings in next 14 days")
    recommendations = []
    for symbol, earnings_date in earnings_candidates.items():
        log.info(f"Analyzing {symbol} (earnings {earnings_date})...")
        result = analyze_stock(symbol, earnings_date)
        if result:
            recommendations.append(result)
        time.sleep(0.5)
    recommendations.sort(key=lambda x: x["confidence"], reverse=True)
    # Deduplicate same-company ticker pairs (e.g. GOOGL/GOOG) — keep higher avg volume
    seen_companies: Dict[str, Dict] = {}
    deduped = []
    for r in recommendations:
        company = r["company"]
        if company not in seen_companies:
            seen_companies[company] = r
            deduped.append(r)
        else:
            prev = seen_companies[company]
            try:
                vol_new  = yf.Ticker(r["symbol"]).info.get("averageVolume", 0) or 0
                vol_prev = yf.Ticker(prev["symbol"]).info.get("averageVolume", 0) or 0
                if vol_new > vol_prev:
                    deduped.remove(prev)
                    deduped.append(r)
                    seen_companies[company] = r
                    log.info(f"Dedup: kept {r['symbol']} over {prev['symbol']} (higher volume)")
                else:
                    log.info(f"Dedup: kept {prev['symbol']} over {r['symbol']} (higher volume)")
            except Exception:
                log.info(f"Dedup: kept {prev['symbol']} over {r['symbol']} (first seen)")
    recommendations = deduped
    existing = load_json("recommendations.json")
    if not isinstance(existing, list):
        existing = []
    today_str = today_et().isoformat()
    existing = [r for r in existing if r.get("scan_date") != today_str and not r.get("executed")]
    merged = {r["symbol"]: r for r in existing}
    for r in recommendations:
        merged[r["symbol"]] = r
    final = sorted(merged.values(), key=lambda x: x["confidence"], reverse=True)
    save_json("recommendations.json", final)
    log.info(f"SCANNER COMPLETE: {len(recommendations)} new recommendations saved")
    summary_lines = []
    for r in recommendations:
        summary_lines.append(
            f"  {r['symbol']:6s} | {r['company'][:30]:30s} | "
            f"Conf: {r['confidence']:.0f}% | Earnings: {r['earnings_date']} | "
            f"Size: ${r['position_size']}"
        )
    summary = "\n".join(summary_lines) if summary_lines else "  No high-confidence plays found tonight."
    log.info("New recommendations:\n" + summary)
    send_email(
        f"Scan Complete: {len(recommendations)} plays found",
        f"Earnings Scanner Results — {today_str}\n\n"
        f"Scanned {len(tickers)} tickers, found {len(earnings_candidates)} with upcoming earnings.\n"
        f"High-confidence plays (≥90%):\n\n{summary}\n\n"
        f"Full data saved to recommendations.json",
    )


if __name__ == "__main__":
    run_scanner()
