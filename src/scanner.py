"""
Nightly earnings scanner — Hitman v2 methodology.
Runs nightly, identifies upcoming earnings candidates that pass all 4 filters.

Hitman v2 filters (applied as-of today):
  beat_rate  >= 62%   [last 8 EPS quarters, lookahead-free via earnings_dates]
  pu5        >= 40%   [completed quarters with +5% earnings-day move]
  momentum   +1% to +30%  [30-day, as of today]
  IV proxy   < 80%    [HV30 x 1.45, as of today]

Trade structure:
  IV < 55%  => ATM naked call
  IV 55-80% => ATM / +8% bull call spread

Entry:  close, 3 trading days before earnings
Exit:   close, 1 trading day after earnings
"""
import math
import time
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

import os
import requests as _requests
import yfinance as yf
import pandas as pd

from utils import (
    get_logger, load_settings, load_json, save_json,
    send_email, check_kill_switch, now_et, today_et,
)

log = get_logger("scanner")

# ── Hitman v2 thresholds ──────────────────────────────────────────────────────
MIN_BEAT_RATE    = 0.62
MIN_PU5          = 0.40
MIN_MOMENTUM     = 0.01
MAX_MOMENTUM     = 0.30
MAX_IV           = 0.80
MIN_EPS_QUARTERS = 5

POSITION_USD_DEFAULT = 10_000   # fallback if no universe snapshot exists
POSITION_TIERS = {"HIGH": 10_000, "MEDIUM": 5_000, "LOW": 1_000}

# ── Universe — loaded from snapshot, hardcoded list as fallback ───────────────
_UNIVERSE_FALLBACK = list(dict.fromkeys([
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","UNH",
    "XOM","JNJ","WMT","MA","HD","PG","CVX","ABBV","MRK","COST","KO","PEP",
    "BAC","AVGO","TMO","CSCO","ACN","ABT","MCD","CRM","ADBE","LIN","DHR",
    "TXN","NKE","NEE","PM","ORCL","QCOM","HON","IBM","AMD","INTC","GE",
    "INTU","MS","GS","SPGI","AMAT","NOW","ISRG","CAT","BKNG","AXP","LRCX",
    "MU","SYK","ZTS","ADI","GILD","REGN","UBER","LYFT",
    "SHOP","ROKU","TWLO","DDOG","NET","CRWD","ZS","OKTA","PANW",
    "FTNT","MDB",
    "NFLX","ASML","TMUS","AMGN","MELI","KLAC","SNPS","CDNS","MCHP",
    "ANET","MRVL","WDAY","TEAM","NXPI","ON","DXCM","IDXX",
    "BIIB","MRNA","PAYX","ROST","FAST","ODFL","CPRT","VRSK","CTSH","EA",
    "SBUX","MNST","ORLY","PCAR","MDLZ","KDP",
    "HUBS","TTD","ZM","BILL","CELH","ESTC","PSTG","NTAP","FFIV","PAYC",
    "WIX","MNDY","DUOL",
    "ABNB","COIN","SNOW","PLTR","RBLX","HOOD","SOFI",
    "APP","GTLB","IOT","TOST","KVYO","ARM","GEHC","SMCI","CEG",
]))

def _load_universe() -> tuple[list, dict]:
    """
    Load universe from universe_snapshot.json if available.
    Returns (ticker_list, sector_lookup) where sector_lookup is
    {symbol: {sector_type, confidence, position_size}}.
    Falls back to hardcoded list with no sector data.
    """
    snapshot_path = os.path.join(os.path.dirname(__file__), "..", "data", "universe_snapshot.json")
    try:
        with open(snapshot_path) as f:
            snap = __import__("json").load(f)
        full = snap.get("full_universe", [])
        if not full:
            raise ValueError("empty full_universe")
        tickers = [s["symbol"] for s in full]
        # sector lookup: symbol -> {sector_type, confidence, position_size}
        lookup  = {
            s["symbol"]: {
                "sector_type":   s.get("sector_type",   "other"),
                "confidence":    s.get("confidence",    "HIGH"),
                "position_size": s.get("position_size", POSITION_USD_DEFAULT),
            }
            for s in full
        }
        log.info(f"Universe loaded from snapshot: {len(tickers)} stocks")
        return tickers, lookup
    except Exception as e:
        log.warning(f"Could not load universe snapshot ({e}) — using hardcoded fallback ({len(_UNIVERSE_FALLBACK)} stocks)")
        return _UNIVERSE_FALLBACK, {}

UNIVERSE, _SECTOR_LOOKUP = _load_universe()

# Earliest eligible date per stock (IPO or when options market matured).
# Stocks not listed here are eligible from any date.
ELIGIBLE_FROM = {
    "UBER":  date(2019, 5, 10), "LYFT":  date(2019, 3, 29),
    "BILL":  date(2019, 12, 11),"DDOG":  date(2019, 9, 19),
    "CRWD":  date(2019, 6, 12), "NET":   date(2019, 9, 13),
    "ZM":    date(2019, 4, 18),
    "SNOW":  date(2020, 9, 16), "PLTR":  date(2020, 9, 30),
    "ABNB":  date(2020, 12, 10),
    "COIN":  date(2021, 4, 14), "RBLX":  date(2021, 3, 10),
    "DUOL":  date(2021, 7, 28), "HOOD":  date(2021, 7, 29),
    "SOFI":  date(2021, 6, 1),  "APP":   date(2021, 4, 1),
    "GTLB":  date(2021, 10, 14),"IOT":   date(2021, 12, 15),
    "TOST":  date(2021, 9, 22),
    "KVYO":  date(2023, 9, 20), "ARM":   date(2023, 9, 14),
    "GEHC":  date(2023, 1, 4),  "SMCI":  date(2022, 1, 1),
    "CEG":   date(2022, 2, 1),
}

# ── Earnings calendar ─────────────────────────────────────────────────────────
def fetch_fmp_earnings_calendar(window_days: int = 14) -> Dict[str, tuple]:
    """Fetch earnings calendar from FMP. Returns {symbol: (earnings_date, timing)}."""
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        log.warning("FMP_API_KEY not set — skipping FMP earnings calendar")
        return {}
    today = today_et()
    horizon = today + timedelta(days=window_days)
    url = "https://financialmodelingprep.com/api/v3/earning_calendar"
    try:
        r = _requests.get(url, params={
            "from": today.isoformat(),
            "to": horizon.isoformat(),
            "apikey": api_key,
        }, timeout=15)
        if r.status_code != 200:
            log.warning(f"FMP returned {r.status_code}")
            return {}
        data = r.json()
        result = {}
        for entry in data:
            sym = entry.get("symbol", "").upper()
            date_str = entry.get("date", "")
            time_str = (entry.get("time") or "unknown").lower()
            if not sym or not date_str:
                continue
            try:
                ed = date.fromisoformat(date_str)
            except ValueError:
                continue
            if sym not in UNIVERSE:
                continue
            timing = "pre_market" if time_str == "bmo" else ("post_market" if time_str == "amc" else "unknown")
            result[sym] = (ed, timing)
        log.info(f"FMP calendar: {len(result)} universe stocks with earnings in next {window_days} days")
        return result
    except Exception as e:
        log.warning(f"FMP calendar fetch failed: {e}")
        return {}


KNOWN_TIMING: Dict[str, str] = {
    "AAPL": "post_market", "AMZN": "post_market", "GOOGL": "post_market",
    "META": "post_market", "MSFT": "post_market", "NVDA": "post_market",
    "TSLA": "post_market", "NFLX": "post_market", "AMD":  "post_market",
    "QCOM": "post_market", "INTC": "post_market", "TXN":  "post_market",
    "NOW":  "post_market", "SNOW": "post_market", "CRM":  "post_market",
    "ADBE": "post_market", "ORCL": "post_market", "IBM":  "post_market",
    "ISRG": "post_market", "V":    "post_market", "MA":   "post_market",
    "BKNG": "post_market", "ABNB": "post_market", "UBER": "post_market",
    "LYFT": "post_market", "RBLX": "post_market", "HOOD": "post_market",
    "SOFI": "post_market", "TWLO": "post_market", "ROKU": "post_market",
    "PLTR": "post_market", "LRCX": "post_market", "KLAC": "post_market",
    "AMAT": "post_market", "MU":   "post_market", "MRVL": "post_market",
    "SHOP": "post_market", "ZM":   "post_market", "OKTA": "post_market",
    "NET":  "post_market", "DDOG": "pre_market",  "CRWD": "post_market",
    "ZS":   "post_market", "PANW": "post_market", "FTNT": "post_market",
    "MRK":  "post_market", "GILD": "post_market", "AVGO": "post_market",
    "JPM":  "pre_market",  "BAC":  "pre_market",  "GS":   "pre_market",
    "MS":   "pre_market",  "UNH":  "pre_market",  "JNJ":  "pre_market",
    "PG":   "pre_market",  "KO":   "pre_market",  "PM":   "pre_market",
    "PEP":  "pre_market",  "GE":   "pre_market",  "HON":  "pre_market",
    "CAT":  "pre_market",  "XOM":  "pre_market",  "CVX":  "pre_market",
    "NEE":  "pre_market",  "TMO":  "pre_market",  "DHR":  "pre_market",
    "ABT":  "pre_market",  "ABBV": "pre_market",  "AXP":  "pre_market",
    "SPGI": "pre_market",  "LIN":  "pre_market",  "SYK":  "pre_market",
    "ZTS":  "pre_market",  "REGN": "pre_market",  "AMGN": "pre_market",
    "BIIB": "pre_market",  "WMT":  "pre_market",  "COST": "pre_market",
}


def get_earnings_date_and_timing(ticker: yf.Ticker, symbol: str) -> tuple:
    """Return (earnings_date, timing) within the next window_days."""
    settings = load_settings()
    window = settings["thresholds"]["earnings_window_days"]
    today = today_et()
    horizon = today + timedelta(days=window)

    def classify_timing(ts) -> str:
        try:
            t = pd.Timestamp(ts)
            if t.tzinfo is not None:
                t = t.tz_convert("America/New_York")
            if t.hour == 0 and t.minute == 0:
                return "unknown"
            return "pre_market" if t.hour < 12 else "post_market"
        except Exception:
            return "unknown"

    known = KNOWN_TIMING.get(symbol)

    try:
        cal = ticker.calendar
        if cal is not None and len(cal) > 0:
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    if isinstance(ed, (list, tuple)):
                        ed = ed[0]
                    ts = pd.Timestamp(ed)
                    ed_date = ts.date()
                    if today <= ed_date <= horizon:
                        timing = known or classify_timing(ts)
                        return ed_date, timing
            elif isinstance(cal, pd.DataFrame):
                if "Earnings Date" in cal.columns:
                    ts = pd.Timestamp(cal["Earnings Date"].iloc[0])
                    ed_date = ts.date()
                    if today <= ed_date <= horizon:
                        timing = known or classify_timing(ts)
                        return ed_date, timing
    except Exception:
        pass
    try:
        edf = ticker.earnings_dates
        if edf is not None and not edf.empty:
            idx = edf.index.tz_convert(None) if getattr(edf.index, "tz", None) else edf.index
            future = edf[(idx >= pd.Timestamp(today)) & (idx <= pd.Timestamp(horizon))]
            if not future.empty:
                ts = future.index[0]
                timing = known or classify_timing(ts)
                return ts.date(), timing
    except Exception:
        pass
    return None, known or "unknown"


# ── Hitman v2 metric functions ────────────────────────────────────────────────
_cache: Dict[str, Any] = {}

def _get_history_and_dates(sym: str):
    """Cache yfinance history + earnings_dates per symbol."""
    if sym in _cache:
        return _cache[sym]
    try:
        t    = yf.Ticker(sym)
        hist = t.history(period="5y", interval="1d")
        ed   = t.get_earnings_dates(limit=20)
        _cache[sym] = (hist, ed)
        return _cache[sym]
    except Exception:
        _cache[sym] = None
        return None


def get_beat_rate(sym: str, before_date: date) -> Optional[float]:
    """
    Beat rate using only EPS quarters reported strictly before before_date.
    Uses earnings_dates (goes back ~4 years) — NOT earnings_history (only ~4 recent quarters).
    Requires MIN_EPS_QUARTERS qualifying rows.
    """
    data = _get_history_and_dates(sym)
    if not data: return None
    _, ed = data
    if ed is None or ed.empty: return None
    try:
        idx = ed.index.tz_convert(None) if getattr(ed.index, "tz", None) is not None else ed.index
        past = ed[(idx.date < before_date) & ed["Reported EPS"].notna()]
        if len(past) < MIN_EPS_QUARTERS: return None
        recent = past.head(8)   # earnings_dates is sorted newest-first
        return float((recent["Reported EPS"] > recent["EPS Estimate"]).sum() / len(recent))
    except Exception:
        return None


def get_pu5(sym: str, as_of: date) -> Optional[float]:
    """
    Fraction of completed quarters where the stock had a +5%+ single-day move.
    Data capped at as_of date; current in-progress quarter excluded.
    """
    data = _get_history_and_dates(sym)
    if not data: return None
    hist, _ = data
    if hist is None or len(hist) < 40: return None
    try:
        h = hist.copy()
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_convert(None)
        h = h[h.index.date <= as_of]
        h["ret"] = h["Close"].pct_change()
        h["quarter"] = h.index.to_period("Q")
        current_q = pd.Period(as_of, freq="Q")
        up5 = total = 0
        for q, g in h.groupby("quarter"):
            if q >= current_q: continue   # skip in-progress quarter
            if len(g) < 5: continue
            peak = g.loc[g["ret"].abs().idxmax(), "ret"]
            total += 1
            if peak >= 0.05: up5 += 1
        return up5 / total if total >= 4 else None
    except Exception:
        return None


def get_momentum(sym: str, as_of: date) -> Optional[float]:
    """30-day price momentum ending on as_of."""
    data = _get_history_and_dates(sym)
    if not data: return None
    hist, _ = data
    if hist is None or hist.empty: return None
    try:
        if getattr(hist.index, "tz", None) is not None:
            hist = hist.copy(); hist.index = hist.index.tz_convert(None)
        sub = hist[hist.index.date <= as_of]
        if len(sub) < 21: return None
        return float((sub["Close"].iloc[-1] - sub["Close"].iloc[-21]) / sub["Close"].iloc[-21])
    except Exception:
        return None


def get_hv30(sym: str, as_of: date) -> Optional[float]:
    """30-day historical volatility (annualised) ending on as_of."""
    data = _get_history_and_dates(sym)
    if not data: return None
    hist, _ = data
    if hist is None or hist.empty: return None
    try:
        if getattr(hist.index, "tz", None) is not None:
            hist = hist.copy(); hist.index = hist.index.tz_convert(None)
        sub = hist[hist.index.date <= as_of]
        if len(sub) < 22: return None
        rets = sub["Close"].pct_change().dropna().tail(30)
        if len(rets) < 20: return None
        return float(rets.std() * math.sqrt(252))
    except Exception:
        return None


# ── Stock analysis ────────────────────────────────────────────────────────────
def analyze_stock(symbol: str, earnings_date: date, earnings_timing: str = "unknown") -> Optional[Dict[str, Any]]:
    """
    Apply Hitman v2 filters to a stock with upcoming earnings.
    Returns a candidate dict if all 4 filters pass, else None.
    """
    # Skip if not yet eligible (IPO date check)
    eligible_from = ELIGIBLE_FROM.get(symbol, date(2000, 1, 1))
    if earnings_date < eligible_from:
        log.debug(f"{symbol}: not yet eligible (IPO gate)")
        return None

    today = today_et()

    # Compute all metrics as of today
    # (rechecked at actual entry date ~3 trading days before earnings)
    beat_rate = get_beat_rate(symbol, before_date=today)
    pu5       = get_pu5(symbol, as_of=today)
    momentum  = get_momentum(symbol, as_of=today)
    hv30      = get_hv30(symbol, as_of=today)
    iv        = hv30 * 1.45 if hv30 is not None else None

    # Apply all 4 filters
    fails = []
    if beat_rate is None or beat_rate < MIN_BEAT_RATE:
        fails.append(f"beat_rate={beat_rate:.0%}" if beat_rate is not None else "beat_rate=None")
    if pu5 is None or pu5 < MIN_PU5:
        fails.append(f"pu5={pu5:.0%}" if pu5 is not None else "pu5=None")
    if momentum is None or not (MIN_MOMENTUM <= momentum <= MAX_MOMENTUM):
        fails.append(f"momentum={momentum:+.0%}" if momentum is not None else "momentum=None")
    if iv is None or iv >= MAX_IV:
        fails.append(f"IV={iv:.0%}" if iv is not None else "IV=None")

    if fails:
        log.debug(f"{symbol}: filtered out — {', '.join(fails)}")
        return None

    # Always naked ATM call — spreads cap upside on stocks that historically move 14%+
    trade_type = "call"

    # Confidence tier — from universe snapshot sector data + current HV
    snap_data    = _SECTOR_LOOKUP.get(symbol, {})
    sector_type  = snap_data.get("sector_type", "other")
    # Re-derive tier live using current HV (HV can change since snapshot was built)
    high_hv      = hv30 > 0.70
    risky_sector = sector_type in ("biotech", "financial")
    if risky_sector and high_hv:
        confidence, position_size = "LOW",    1_000
    elif risky_sector or high_hv:
        confidence, position_size = "MEDIUM", 5_000
    else:
        confidence, position_size = "HIGH",   10_000

    # Get current price for display
    try:
        info = yf.Ticker(symbol).info or {}
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        company = info.get("longName", symbol)
        sector = info.get("sector", "Unknown")
    except Exception:
        current_price = 0
        company = symbol
        sector = "Unknown"

    result = {
        "id":               str(uuid.uuid4())[:8],
        "symbol":           symbol,
        "company":          company,
        "sector":           sector,
        "sector_type":      sector_type,
        "confidence":       confidence,
        "earnings_date":    earnings_date.isoformat(),
        "earnings_timing":  earnings_timing,
        "beat_rate":        round(beat_rate, 3),
        "pu5":              round(pu5, 3),
        "momentum_30d":     round(momentum, 3),
        "hv30":             round(hv30, 3),
        "iv_proxy":         round(iv, 3),
        "trade_type":       trade_type,
        "position_size":    position_size,
        "current_price":    round(current_price, 2),
        "validated":        False,
        "executed":         False,
        "scan_date":        today.isoformat(),
        "notes":            [],
    }

    log.info(
        f"{symbol}: PASS | beat={beat_rate:.0%} pu5={pu5:.0%} "
        f"mom={momentum:+.0%} IV={iv:.0%} | {trade_type} | earnings={earnings_date}"
    )
    return result


# ── Main scanner ──────────────────────────────────────────────────────────────
def run_scanner():
    log.info("=" * 60)
    log.info("SCANNER: Starting Hitman v2 nightly scan")
    log.info("=" * 60)
    scan_started = now_et().isoformat()

    if check_kill_switch():
        log.warning("Kill switch active — scanner aborted")
        save_json("scanner_status.json", {
            "last_run": scan_started, "status": "aborted",
            "reason": "kill switch active", "recommendations_found": 0,
        })
        return

    settings = load_settings()
    window = settings["thresholds"]["earnings_window_days"]
    today = today_et()

    # Step 1: Get earnings dates (FMP primary, yfinance fallback)
    fmp_calendar = fetch_fmp_earnings_calendar(window)
    earnings_candidates: Dict[str, tuple] = {}

    log.info(f"Checking earnings dates for {len(UNIVERSE)} universe stocks...")
    for i, symbol in enumerate(UNIVERSE):
        try:
            if symbol in fmp_calendar:
                ed, timing = fmp_calendar[symbol]
                earnings_candidates[symbol] = (ed, timing)
                log.info(f"  {symbol}: earnings on {ed} ({timing}) [FMP]")
                continue
            t = yf.Ticker(symbol)
            ed, timing = get_earnings_date_and_timing(t, symbol)
            if ed:
                earnings_candidates[symbol] = (ed, timing)
                log.info(f"  {symbol}: earnings on {ed} ({timing})")
            if i > 0 and i % 50 == 0:
                log.info(f"  Progress: {i}/{len(UNIVERSE)} checked, {len(earnings_candidates)} found")
                time.sleep(1)
        except Exception as e:
            log.debug(f"  {symbol}: date check failed — {e}")
        time.sleep(0.1)

    log.info(f"Found {len(earnings_candidates)} stocks with earnings in next {window} days")

    # Step 2: Apply Hitman v2 filters
    recommendations = []
    for symbol, (earnings_date, earnings_timing) in earnings_candidates.items():
        log.info(f"Analyzing {symbol} (earnings {earnings_date}, {earnings_timing})...")
        result = analyze_stock(symbol, earnings_date, earnings_timing)
        if result:
            recommendations.append(result)
        time.sleep(0.4)

    # Sort by earnings date (soonest first)
    recommendations.sort(key=lambda x: x["earnings_date"])

    # Step 3: Merge with existing recommendations.json
    existing = load_json("recommendations.json")
    if not isinstance(existing, list):
        existing = []
    today_str = today.isoformat()
    cutoff = today - timedelta(days=1)

    def _keep(r: Dict) -> bool:
        if r.get("executed"):
            return True
        if r.get("scan_date") == today_str:
            return False
        try:
            ed = date.fromisoformat(r["earnings_date"])
            return ed >= cutoff
        except Exception:
            return True

    existing = [r for r in existing if _keep(r)]
    merged = {r["symbol"]: r for r in existing}
    for r in recommendations:
        merged[r["symbol"]] = r
    final = sorted(merged.values(), key=lambda x: x["earnings_date"])

    save_json("recommendations.json", final)
    save_json("scanner_status.json", {
        "last_run":              scan_started,
        "status":                "ok",
        "universe_size":         len(UNIVERSE),
        "earnings_candidates":   len(earnings_candidates),
        "candidates":            list(earnings_candidates.keys()),
        "recommendations_found": len(recommendations),
        "recommendations":       [r["symbol"] for r in recommendations],
    })

    log.info(f"SCANNER COMPLETE: {len(recommendations)} Hitman v2 candidates saved")

    # Email summary
    if recommendations:
        lines = []
        for r in recommendations:
            lines.append(
                f"  {r['symbol']:6s} | {r['company'][:28]:28s} | "
                f"beat={r['beat_rate']:.0%} pu5={r['pu5']:.0%} "
                f"mom={r['momentum_30d']:+.0%} IV={r['iv_proxy']:.0%} | "
                f"{r['trade_type']:6s} | earnings={r['earnings_date']}"
            )
        summary = "\n".join(lines)
    else:
        summary = "  No Hitman v2 candidates found tonight."

    send_email(
        f"Hitman v2 Scan: {len(recommendations)} candidates found",
        f"Hitman v2 Scanner Results — {today_str}\n\n"
        f"Universe: {len(UNIVERSE)} stocks | "
        f"Earnings this window: {len(earnings_candidates)}\n\n"
        f"Candidates (all 4 filters passed):\n\n{summary}\n\n"
        f"Filters: beat>={MIN_BEAT_RATE:.0%}  pu5>={MIN_PU5:.0%}  "
        f"mom {MIN_MOMENTUM:.0%}-{MAX_MOMENTUM:.0%}  IV<{MAX_IV:.0%}",
    )


if __name__ == "__main__":
    run_scanner()
