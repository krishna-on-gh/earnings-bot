"""
data_audit.py — cross-validate the backtest's price data
=========================================================
Every backtest so far ran on yfinance (free, scraped, unofficial). This script
independently re-pulls the SAME daily prices from Tiingo (a real data vendor)
and compares them, so we have evidence — not assumption — about data quality.

WHAT IT CHECKS
--------------
The backtests depend on short-horizon RETURNS (4-day windows around earnings).
A daily-return match is the verdict metric: it is independent of how each
provider handles cumulative split/dividend adjustment, so it isolates genuine
data errors (bad bars, missing days, mis-applied splits).

  PASS  -> daily returns match tightly, no missing bars  => yfinance data sound
  FLAG  -> missing bars or material return divergence    => rebuild data layer

It also runs a light internal-consistency check on yfinance earnings dates
(quarterly spacing, weekday) — NOT a cross-validation, just a gross-error catch,
since Tiingo's free tier has no earnings data.
"""

import os
import sys
import warnings
from datetime import datetime, timedelta

import requests
import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

# ── .env / Tiingo key ─────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load_dotenv():
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()
TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")

# Sample spans mega-caps, mid-caps, volatile tech, biotech, financials
SAMPLE = ["AAPL", "NVDA", "MDB", "OKTA", "JPM", "MRNA",
          "AVGO", "TSLA", "SNOW", "PANW", "SNPS", "CRM"]

# Divergence thresholds (daily return abs difference)
NOISE_THRESH = 0.005   # 0.5% — below this is dividend-adjustment noise, ignore
ERROR_THRESH = 0.02    # 2%+  — a genuine bad bar / missing day / mis-split


# ── Data fetchers ─────────────────────────────────────────────────────────────
def tiingo_closes(sym: str, start: str, end: str) -> pd.Series | None:
    """Daily adjusted close from Tiingo, indexed by normalized date."""
    if not TIINGO_KEY:
        return None
    try:
        url = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
               f"?startDate={start}&endDate={end}&token={TIINGO_KEY}")
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        df = pd.DataFrame(data)
        dt = pd.to_datetime(df["date"])
        if dt.dt.tz is not None:          # Tiingo returns tz-aware UTC timestamps
            dt = dt.dt.tz_convert(None)   # strip tz so it aligns with yfinance's naive index
        df["date"] = dt.dt.normalize()
        df = df.sort_values("date").set_index("date")
        col = "adjClose" if "adjClose" in df.columns else "close"
        return df[col].dropna()
    except Exception:
        return None


def yf_closes(sym: str, start: str, end: str) -> pd.Series | None:
    """Daily adjusted close from yfinance (auto_adjust — matches the backtest)."""
    try:
        h = yf.Ticker(sym).history(start=start, end=end, auto_adjust=True)
        if h.empty:
            return None
        s = h["Close"].dropna()
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_convert(None)
        s.index = pd.to_datetime(s.index).normalize()
        return s
    except Exception:
        return None


# ── Earnings-date consistency check (internal, not cross-validation) ─────────
def earnings_consistency(sym: str) -> str:
    """Light gross-error check on yfinance earnings dates."""
    try:
        ed = yf.Ticker(sym).get_earnings_dates(limit=20)
        if ed is None or ed.empty:
            return "no earnings data"
        idx = ed.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert(None)
        past = sorted(d.date() for d in idx
                      if not pd.isna(d) and d.date() < datetime.today().date())
        if len(past) < 4:
            return f"only {len(past)} past dates"
        gaps = [(past[i+1] - past[i]).days for i in range(len(past) - 1)]
        bad_gaps = [g for g in gaps if g < 60 or g > 130]
        weekends = [d for d in past if d.weekday() >= 5]
        flags = []
        if bad_gaps:
            flags.append(f"{len(bad_gaps)} non-quarterly gap(s) {bad_gaps}")
        if weekends:
            flags.append(f"{len(weekends)} weekend date(s)")
        return "  ".join(flags) if flags else f"OK ({len(past)} dates, quarterly)"
    except Exception as e:
        return f"check failed: {e}"


# ── Main audit ────────────────────────────────────────────────────────────────
def run_audit():
    end   = datetime.today()
    start = end - timedelta(days=3 * 365 + 60)
    s_str, e_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    print("=" * 74)
    print("  DATA AUDIT — yfinance vs Tiingo (independent cross-validation)")
    print(f"  {len(SAMPLE)} stocks | {s_str} to {e_str}")
    print("=" * 74)

    if not TIINGO_KEY:
        print("\n  ERROR: no TIINGO_API_KEY in .env — cannot cross-validate.")
        return

    print(f"\n  {'Sym':<6} {'common':>7} {'yf-only':>8} {'tg-only':>8} "
          f"{'maxΔret':>9} {'>0.5%':>7} {'>2%':>6}  verdict")
    print("  " + "-" * 70)

    all_clean = True
    rows = []
    for sym in SAMPLE:
        yf_s = yf_closes(sym, s_str, e_str)
        tg_s = tiingo_closes(sym, s_str, e_str)
        if yf_s is None or tg_s is None:
            print(f"  {sym:<6}  fetch failed (yf={yf_s is not None}, tiingo={tg_s is not None})")
            all_clean = False
            continue

        common  = yf_s.index.intersection(tg_s.index)
        yf_only = len(yf_s.index.difference(tg_s.index))
        tg_only = len(tg_s.index.difference(yf_s.index))

        # daily returns computed within each full series, then aligned
        yf_ret = yf_s.pct_change().reindex(common)
        tg_ret = tg_s.pct_change().reindex(common)
        diff   = (yf_ret - tg_ret).abs().dropna()

        max_d   = float(diff.max()) if len(diff) else 0.0
        n_noise = int((diff > NOISE_THRESH).sum())
        n_error = int((diff > ERROR_THRESH).sum())

        # missing bars beyond a tiny tolerance, or real errors -> flag
        ok = (yf_only <= 2 and tg_only <= 2 and n_error == 0)
        verdict = "OK" if ok else "FLAG"
        if not ok:
            all_clean = False

        print(f"  {sym:<6} {len(common):>7} {yf_only:>8} {tg_only:>8} "
              f"{max_d:>8.2%} {n_noise:>7} {n_error:>6}  {verdict}")
        rows.append((sym, len(common), yf_only, tg_only, max_d, n_noise, n_error, ok))

    # ── Earnings-date consistency ────────────────────────────────────────────
    print("\n  -- Earnings-date consistency (yfinance internal check) --")
    for sym in SAMPLE:
        print(f"    {sym:<6} {earnings_consistency(sym)}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    if all_clean:
        print("  AUDIT PASSED — yfinance daily returns match Tiingo across the sample.")
        print("  The price data underlying the backtests is sound. Conclusions hold.")
    else:
        print("  AUDIT FLAGGED — divergences found. Review the FLAG rows above before")
        print("  trusting the backtest. A data-layer rebuild on Tiingo may be needed.")
    print("=" * 74)
    print("\n  Note: '>0.5%' counts are mostly ex-dividend adjustment differences")
    print("  (normal, not errors). '>2%' counts are genuine data discrepancies.")
    print("  Earnings dates are NOT cross-validated — Tiingo's free tier has none.")


if __name__ == "__main__":
    run_audit()
