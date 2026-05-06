"""
New Methodology Backtest: Jan 1 – May 6, 2026
==============================================

Three-column options strategy:

  COLUMN A  — Core AI/Chips (AMD, INTC, PLTR, NVDA)
              Always BUY CALLS, regardless of any other filter.

  COLUMN B  — Dual Filter: beat_rate >= 0.50 AND price_up_5pct_rate >= 0.55
              BUY CALLS unless overridden by Column C.

  COLUMN C  — Priced-for-Perfection: revenue_growth_yoy > 25% AND momentum_30d > 5%
              BUY PUTS (overrides Column B for non-Core-AI stocks).

Priority: Column A > Column C > Column B.
A stock in Column A is always a call.
A stock in Column C (but NOT Column A) is always a put.
A stock in Column B only (not A or C) is a call.
Non-qualifying stocks are skipped.

Options Pricing (Black-Scholes):
  Pre-earnings IV   = HV30 × 1.45
  Post-earnings IV  = HV30 × 0.58  (IV crush after announcement)
  Call strike       = current_price × 1.05, rounded to nearest step
  Put  strike       = current_price × 0.95, rounded to nearest step
  Expiry            = 3rd Friday of next calendar month after earnings
  Risk-free rate    = 4.5%

Trade Mechanics:
  Entry             : Close of day BEFORE earnings (pre_close)
  Exit              : Close of earnings day (post_close = pre_close × (1 + earn_ret/100))
  Position size     : $10,000 per trade
  Contracts         : floor($10,000 / (premium × 100)), minimum 1
  Buy cost          : BS(pre_close, strike, T_entry, r, IV_pre)
  Sell price        : BS(post_close, strike, T_exit,  r, IV_post)
  P&L               : (sell - buy) × 100 × n_contracts

Metrics:
  beat_rate          : % of last 8 quarters beating EPS estimate (yfinance earnings_history)
  price_up_5pct_rate : % of training quarters where largest single-day move >= +5%
  revenue_growth_yoy : most recent completed quarter YoY revenue growth (cached)
  momentum_30d       : (pre_close / close_30_trading_days_ago) - 1
"""

import sys, math, json
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path
from datetime import date, timedelta
from scipy.stats import norm

sys.stdout.reconfigure(encoding="utf-8")

# ── Strategy parameters ──────────────────────────────────────────────────────────
TRAIN_START    = "2020-01-01"
TEST_START     = pd.Timestamp("2026-01-01")
TEST_END       = pd.Timestamp("2026-05-06")

CORE_AI_CHIPS  = {"AMD", "INTC", "PLTR", "NVDA"}

DUAL_BEAT_RATE = 0.50   # beat_rate  >= this → dual filter
DUAL_PU5_RATE  = 0.55   # price_up_5pct_rate >= this → dual filter

PFP_REV_GROWTH = 0.25   # revenue_growth_yoy > this
PFP_MOMENTUM   = 0.05   # momentum_30d       > this

RISK_FREE      = 0.045
PRE_IV_MULT    = 1.45   # HV30 × this = pre-earnings IV
POST_IV_MULT   = 0.58   # HV30 × this = post-earnings IV (after announcement)
POSITION_SIZE  = 10_000
STRIKE_OTM_PCT = 0.05   # 5% out-of-the-money
HV_WINDOW      = 30     # trading days for historical vol

MIN_QUARTERS   = 6      # minimum training quarters needed for price_up_5pct_rate
MIN_MOVE_PCT   = 1.5    # minimum |earnings return| % to count as an earnings event

REV_CACHE_FILE = Path("data/rev_growth_cache.json")

# ── Black-Scholes helpers ────────────────────────────────────────────────────────
def bs_price(S, K, T, r, sigma, option_type="call"):
    """Standard Black-Scholes option price."""
    if T <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)
    if sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def round_strike(price, otm_pct, direction="call"):
    """Round OTM strike to standard step size."""
    raw = price * (1 + otm_pct) if direction == "call" else price * (1 - otm_pct)
    if price < 25:
        step = 1.0
    elif price < 100:
        step = 2.5
    elif price < 500:
        step = 5.0
    else:
        step = 10.0
    return round(raw / step) * step


def third_friday(year, month):
    """Return date of 3rd Friday of given year/month."""
    d = date(year, month, 1)
    days_to_fri = (4 - d.weekday()) % 7   # 4 = Friday
    return d + timedelta(days=days_to_fri + 14)


def expiry_after(earn_date):
    """3rd Friday of the month AFTER earnings."""
    m = earn_date.month
    y = earn_date.year
    if m == 12:
        y, m = y + 1, 1
    else:
        m += 1
    return third_friday(y, m)


def strip_tz(series):
    idx = series.index
    if hasattr(idx, "tz") and idx.tz is not None:
        series = series.copy()
        series.index = idx.tz_localize(None)
    return series


# ── Load S&P 500 universe ────────────────────────────────────────────────────────
try:
    sp500 = [s.replace(".", "-") for s in
             pd.read_csv("data/sp500.csv", encoding="latin1")["Symbol"].tolist()]
    print(f"Loaded {len(sp500)} S&P 500 tickers from data/sp500.csv")
except FileNotFoundError:
    tbl   = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
    sp500 = [s.replace(".", "-") for s in tbl["Symbol"].tolist()]
    print(f"Loaded {len(sp500)} S&P 500 tickers from Wikipedia")

# ── Download full price history ──────────────────────────────────────────────────
print(f"\nDownloading prices {TRAIN_START} – 2026-05-07 for {len(sp500)} symbols…")
raw    = yf.download(sp500, start=TRAIN_START, end="2026-05-07",
                     auto_adjust=True, progress=True, threads=True)
closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
closes.index = pd.to_datetime(closes.index).normalize()
closes = closes.ffill()
returns = closes.pct_change()
print(f"  {closes.shape[0]} days × {closes.shape[1]} symbols\n")

# ── Fetch EPS beat rates ──────────────────────────────────────────────────────────
print("Fetching EPS beat rates (yfinance)…")
eps_beat_rates = {}
for i, sym in enumerate(sp500):
    if i % 50 == 0:
        print(f"  {i}/{len(sp500)} …")
    try:
        h = yf.Ticker(sym).earnings_history
        if h is not None and not h.empty and len(h) >= 4:
            recent = h.tail(8)
            beats  = (recent["epsActual"] > recent["epsEstimate"]).sum()
            eps_beat_rates[sym] = beats / len(recent)
    except Exception:
        pass
print(f"  Got EPS beat rates for {len(eps_beat_rates)} symbols\n")

# ── Revenue growth cache ──────────────────────────────────────────────────────────
if REV_CACHE_FILE.exists():
    with open(REV_CACHE_FILE, "r") as f:
        rev_cache = json.load(f)   # {sym: [{date_str, revenue}, ...]}
    print(f"Loaded revenue cache for {len(rev_cache)} symbols from {REV_CACHE_FILE}\n")
else:
    print("Revenue cache not found — fetching quarterly revenue for all symbols…")
    print("(This takes ~10-20 minutes on first run; cached to data/rev_growth_cache.json)\n")
    rev_cache = {}
    for i, sym in enumerate(sp500):
        if i % 25 == 0:
            print(f"  {i}/{len(sp500)} …")
        try:
            t  = yf.Ticker(sym)
            qi = t.quarterly_income_stmt
            if qi is None or qi.empty:
                continue
            # Find "Total Revenue" row (case-insensitive search)
            revenue_row = None
            for row_label in qi.index:
                if "total revenue" in str(row_label).lower() or "totalrevenue" in str(row_label).lower():
                    revenue_row = qi.loc[row_label]
                    break
            if revenue_row is None:
                # Try "Revenue" directly
                for row_label in qi.index:
                    if str(row_label).lower() in ("revenue", "net revenue", "revenues"):
                        revenue_row = qi.loc[row_label]
                        break
            if revenue_row is None:
                continue
            # Store as list of {date, revenue} sorted oldest-first
            rev_series = revenue_row.dropna().sort_index()
            rev_cache[sym] = [
                {"date": str(d.date()), "rev": float(v)}
                for d, v in rev_series.items()
                if v > 0
            ]
        except Exception:
            pass
    REV_CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(REV_CACHE_FILE, "w") as f:
        json.dump(rev_cache, f)
    print(f"  Cached revenue data for {len(rev_cache)} symbols → {REV_CACHE_FILE}\n")


def get_rev_growth(sym, before_date):
    """
    Return most recent YoY quarterly revenue growth for `sym` using data
    with quarter-end date strictly before `before_date`.
    Returns None if insufficient data.
    """
    if sym not in rev_cache:
        return None
    entries = [e for e in rev_cache[sym]
               if pd.Timestamp(e["date"]) < before_date]
    if len(entries) < 5:   # need at least 5 quarters to get Q and Q-4
        return None
    # Sort newest-first
    entries_sorted = sorted(entries, key=lambda x: x["date"], reverse=True)
    latest   = entries_sorted[0]
    # Find same quarter 1 year ago (within ±45 days of 365 days back)
    latest_dt = pd.Timestamp(latest["date"])
    target_dt = latest_dt - pd.DateOffset(years=1)
    best_prior = None
    best_delta = float("inf")
    for e in entries_sorted[1:]:
        dt    = pd.Timestamp(e["date"])
        delta = abs((dt - target_dt).days)
        if delta < best_delta:
            best_delta = delta
            best_prior = e
    if best_prior is None or best_delta > 60:
        return None
    prior_rev = best_prior["rev"]
    if prior_rev <= 0:
        return None
    return (latest["rev"] - prior_rev) / prior_rev


# ── Compute price_up_5pct_rate, beat_rate, momentum_30d for a stock ──────────────
def compute_metrics(sym, earn_day, pre_close):
    """
    Return dict of metrics for `sym` as of `earn_day`, using only data
    strictly before `earn_day` where applicable.
    """
    if sym not in returns.columns:
        return None
    sr = returns[sym][returns.index < earn_day].dropna()
    if len(sr) < 100:
        return None
    sr = strip_tz(sr)

    # price_up_5pct_rate
    moves = []
    for _, qr in sr.groupby(sr.index.to_period("Q")):
        if len(qr) < 10:
            continue
        best = qr.abs().idxmax()
        moves.append(float(qr.loc[best]) * 100)
    if len(moves) < MIN_QUARTERS:
        return None
    pu5 = sum(1 for m in moves if m >= 5.0) / len(moves)

    # beat_rate
    beat_rate = eps_beat_rates.get(sym)

    # momentum_30d: close 30 trading days ago vs pre_close
    hist_closes = closes[sym][closes.index < earn_day].dropna()
    if len(hist_closes) < 32:
        momentum = None
    else:
        close_30d_ago = float(hist_closes.iloc[-31])
        momentum = (pre_close / close_30d_ago) - 1.0 if close_30d_ago > 0 else None

    # HV30 (for options pricing)
    hist_r = sr.iloc[-HV_WINDOW:]
    hv30 = max(min(float(hist_r.std() * math.sqrt(252)), 1.50), 0.08)

    # revenue_growth_yoy
    rev_growth = get_rev_growth(sym, earn_day)

    return {
        "pu5":        pu5,
        "beat_rate":  beat_rate,
        "momentum":   momentum,
        "hv30":       hv30,
        "rev_growth": rev_growth,
    }


# ── Strategy classification ───────────────────────────────────────────────────────
def classify(sym, metrics):
    """
    Returns "call", "put", or None (skip).

    Priority:
      1. Core AI/Chips → "call"
      2. Priced-for-Perfection (AND not Core AI) → "put"
      3. Dual Filter (AND not PFP, not Core AI) → "call"
      4. Otherwise → None
    """
    if sym in CORE_AI_CHIPS:
        return "call"

    if metrics is None:
        return None

    # Check Priced-for-Perfection
    rev  = metrics["rev_growth"]
    mom  = metrics["momentum"]
    is_pfp = (rev is not None and rev > PFP_REV_GROWTH
              and mom is not None and mom > PFP_MOMENTUM)
    if is_pfp:
        return "put"

    # Check Dual Filter
    beat = metrics["beat_rate"]
    pu5  = metrics["pu5"]
    if beat is not None and beat >= DUAL_BEAT_RATE and pu5 >= DUAL_PU5_RATE:
        return "call"

    return None


# ── Find earnings events in the test window ───────────────────────────────────────
print("Scanning for earnings events Jan 1 – May 6, 2026…")

test_rets  = returns[(returns.index >= TEST_START) & (returns.index <= TEST_END)]
events     = []

for sym in closes.columns:
    if sym not in test_rets.columns:
        continue
    sr_test = test_rets[sym].dropna()
    if sr_test.empty:
        continue
    sr_test = strip_tz(sr_test)

    for qtr, qr in sr_test.groupby(sr_test.index.to_period("Q")):
        if qr.empty:
            continue
        earn_day = qr.abs().idxmax()
        earn_ret = float(qr.loc[earn_day]) * 100

        if abs(earn_ret) < MIN_MOVE_PCT:
            continue

        pre_mask  = closes[sym].index < earn_day
        if not pre_mask.any():
            continue
        pre_close = float(closes[sym][pre_mask].iloc[-1])
        if pre_close <= 0:
            continue

        events.append({"sym": sym, "earn_day": earn_day,
                        "earn_ret": earn_ret, "pre_close": pre_close})

print(f"  Found {len(events)} earnings events across "
      f"{len({e['sym'] for e in events})} symbols\n")

# ── Score events and compute options P&L ────────────────────────────────────────
print("Computing metrics and options P&L for each event…")
trades = []

for ev in events:
    sym       = ev["sym"]
    earn_day  = ev["earn_day"]
    earn_ret  = ev["earn_ret"]
    pre_close = ev["pre_close"]
    post_close = pre_close * (1 + earn_ret / 100)

    metrics   = compute_metrics(sym, earn_day, pre_close)
    direction = classify(sym, metrics)

    if direction is None:
        continue  # not traded

    # ── Column label for reporting
    if sym in CORE_AI_CHIPS:
        column = "A (Core AI)"
    elif direction == "put":
        column = "C (PFP Put)"
    else:
        column = "B (Dual Filter)"

    # ── Options pricing
    hv30    = metrics["hv30"] if metrics else 0.30
    iv_pre  = hv30 * PRE_IV_MULT
    iv_post = hv30 * POST_IV_MULT

    earn_date   = earn_day.date() if hasattr(earn_day, "date") else earn_day
    expiry_date = expiry_after(earn_date)
    T_entry     = (expiry_date - earn_date).days / 365 + 1 / 252
    T_exit      = max((expiry_date - earn_date).days / 365, 1 / 365)

    if direction == "call":
        strike     = round_strike(pre_close, STRIKE_OTM_PCT, "call")
        prem_buy   = bs_price(pre_close,  strike, T_entry, RISK_FREE, iv_pre,  "call")
        prem_sell  = bs_price(post_close, strike, T_exit,  RISK_FREE, iv_post, "call")
    else:
        strike     = round_strike(pre_close, STRIKE_OTM_PCT, "put")
        prem_buy   = bs_price(pre_close,  strike, T_entry, RISK_FREE, iv_pre,  "put")
        prem_sell  = bs_price(post_close, strike, T_exit,  RISK_FREE, iv_post, "put")

    if prem_buy <= 0:
        continue

    n_contracts  = max(1, int(POSITION_SIZE / (prem_buy * 100)))
    cost         = round(prem_buy  * 100 * n_contracts, 2)
    proceeds     = round(prem_sell * 100 * n_contracts, 2)
    pnl          = round(proceeds - cost, 2)
    pnl_pct      = pnl / cost * 100

    trades.append({
        "sym":          sym,
        "earn_day":     earn_day,
        "month":        earn_day.strftime("%b"),
        "month_num":    earn_day.month,
        "earn_ret":     earn_ret,
        "pre_close":    pre_close,
        "post_close":   post_close,
        "direction":    direction,
        "column":       column,
        "strike":       strike,
        "hv30":         round(hv30, 4),
        "iv_pre":       round(iv_pre, 4),
        "prem_buy":     round(prem_buy, 2),
        "prem_sell":    round(prem_sell, 2),
        "n_contracts":  n_contracts,
        "cost":         cost,
        "pnl":          pnl,
        "pnl_pct":      pnl_pct,
        "result":       "WIN" if pnl > 0 else "LOSS",
        "beat_rate":    metrics["beat_rate"]  if metrics else None,
        "pu5":          metrics["pu5"]        if metrics else None,
        "momentum":     metrics["momentum"]   if metrics else None,
        "rev_growth":   metrics["rev_growth"] if metrics else None,
        "expiry":       str(expiry_date),
    })

print(f"  Trades after filtering: {len(trades)}\n")

# ── Reporting helpers ────────────────────────────────────────────────────────────
SEP = "=" * 112
DIV = "─" * 112

def section(title):
    print(f"\n{SEP}\n{title}\n{SEP}")

def agg(subset):
    if not subset:
        return {"n": 0, "cost": 0, "pnl": 0, "wins": 0, "ret": 0, "wp": 0}
    n    = len(subset)
    cost = sum(t["cost"] for t in subset)
    pnl  = sum(t["pnl"]  for t in subset)
    wins = sum(1 for t in subset if t["pnl"] > 0)
    ret  = pnl / cost * 100 if cost else 0
    wp   = wins / n * 100   if n    else 0
    return {"n": n, "cost": cost, "pnl": pnl, "wins": wins, "ret": ret, "wp": wp}


def print_trade_rows(subset, label, top_n=25):
    if not subset:
        print(f"  {label}: (none)")
        return
    sorted_t = sorted(subset, key=lambda x: x["pnl"], reverse=True)
    top = sorted_t[:top_n]
    bot = sorted_t[-top_n:] if len(sorted_t) > top_n else []

    def _row(t):
        br = f"{t['beat_rate']:.0%}" if t["beat_rate"] is not None else " N/A"
        p5 = f"{t['pu5']:.0%}"       if t["pu5"]       is not None else " N/A"
        mo = f"{t['momentum']:>+.0%}" if t["momentum"]  is not None else "  N/A"
        rg = f"{t['rev_growth']:>+.0%}" if t["rev_growth"] is not None else "  N/A"
        print(f"  {t['sym']:<6} {t['earn_day'].date()}  "
              f"{t['direction'].upper():<4}  K={t['strike']:>7.2f}  "
              f"earn={t['earn_ret']:>+6.1f}%  "
              f"prem=${t['prem_buy']:>6.2f}→${t['prem_sell']:>6.2f}  "
              f"n={t['n_contracts']:>2}  cost=${t['cost']:>6,.0f}  "
              f"P&L=${t['pnl']:>+8,.0f} ({t['pnl_pct']:>+6.1f}%)  "
              f"{t['result']}  col={t['column']}")

    print(f"\n  {label}  [{len(subset)} trades]")
    for t in top:
        _row(t)
    if bot and bot != top:
        print(f"\n  … bottom losses …")
        for t in reversed(bot):
            _row(t)


# ── Overall summary ──────────────────────────────────────────────────────────────
section("NEW METHODOLOGY BACKTEST — Jan 1 to May 6, 2026")
overall = agg(trades)
print(f"\n  Universe          : S&P 500 ({len(sp500)} symbols)")
print(f"  Earnings events   : {len(events)}")
print(f"  Trades taken      : {overall['n']}")
print(f"  Total deployed    : ${overall['cost']:,.0f}")
print(f"  Total P&L         : ${overall['pnl']:+,.0f}")
print(f"  Portfolio return  : {overall['ret']:+.2f}%")
print(f"  Win rate          : {overall['wins']}/{overall['n']} ({overall['wp']:.1f}%)")

# ── Column breakdown ─────────────────────────────────────────────────────────────
section("BREAKDOWN BY COLUMN (strategy bucket)")

for col_name in ["A (Core AI)", "B (Dual Filter)", "C (PFP Put)"]:
    subset = [t for t in trades if t["column"] == col_name]
    s = agg(subset)
    if s["n"] == 0:
        print(f"\n  Column {col_name}: no trades")
        continue
    print(f"\n  Column {col_name}")
    print(f"    Trades    : {s['n']}")
    print(f"    Deployed  : ${s['cost']:,.0f}")
    print(f"    P&L       : ${s['pnl']:+,.0f}")
    print(f"    Return    : {s['ret']:+.2f}%")
    print(f"    Win Rate  : {s['wins']}/{s['n']} ({s['wp']:.1f}%)")

# ── Monthly breakdown ────────────────────────────────────────────────────────────
section("MONTHLY BREAKDOWN")

months     = ["Jan", "Feb", "Mar", "Apr", "May"]
month_nums = {"Jan":1, "Feb":2, "Mar":3, "Apr":4, "May":5}

print(f"\n  {'Month':<5}  {'Trades':>6}  {'Deployed':>10}  {'P&L':>10}  {'Return':>8}  "
      f"{'Win Rate':>10}  Calls  Puts")
print(f"  {'-'*5}  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*10}  {'-'*5}  {'-'*4}")

for mo in months:
    mn  = month_nums[mo]
    sub = [t for t in trades if t["month_num"] == mn]
    if not sub:
        continue
    s      = agg(sub)
    calls  = sum(1 for t in sub if t["direction"] == "call")
    puts   = sum(1 for t in sub if t["direction"] == "put")
    print(f"  {mo:<5}  {s['n']:>6}  ${s['cost']:>9,.0f}  ${s['pnl']:>+9,.0f}  "
          f"{s['ret']:>+7.1f}%  {s['wins']}/{s['n']} ({s['wp']:.0f}%)  "
          f"{calls:>5}  {puts:>4}")

tot = agg(trades)
calls_tot = sum(1 for t in trades if t["direction"] == "call")
puts_tot  = sum(1 for t in trades if t["direction"] == "put")
print(f"  {'─'*5}  {'─'*6}  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*10}  {'─'*5}  {'─'*4}")
print(f"  {'TOTAL':<5}  {tot['n']:>6}  ${tot['cost']:>9,.0f}  ${tot['pnl']:>+9,.0f}  "
      f"{tot['ret']:>+7.1f}%  {tot['wins']}/{tot['n']} ({tot['wp']:.0f}%)  "
      f"{calls_tot:>5}  {puts_tot:>4}")

# ── Calls vs Puts summary ────────────────────────────────────────────────────────
section("CALLS vs PUTS — HEAD-TO-HEAD")
calls_all = [t for t in trades if t["direction"] == "call"]
puts_all  = [t for t in trades if t["direction"] == "put"]
sc = agg(calls_all)
sp = agg(puts_all)

print(f"\n  {'Metric':<22}  {'CALLS':>18}  {'PUTS':>18}")
print(f"  {'─'*22}  {'─'*18}  {'─'*18}")
for label, cv, pv in [
    ("Trades",         sc['n'],                   sp['n']),
    ("Deployed",       f"${sc['cost']:,.0f}",     f"${sp['cost']:,.0f}"),
    ("Total P&L",      f"${sc['pnl']:+,.0f}",     f"${sp['pnl']:+,.0f}"),
    ("Return",         f"{sc['ret']:+.2f}%",       f"{sp['ret']:+.2f}%"),
    ("Win rate",       f"{sc['wins']}/{sc['n']} ({sc['wp']:.1f}%)",
                       f"{sp['wins']}/{sp['n']} ({sp['wp']:.1f}%)"),
]:
    print(f"  {label:<22}  {str(cv):>18}  {str(pv):>18}")

# ── Top/bottom individual trades ─────────────────────────────────────────────────
section("CALL TRADES — Sorted by P&L")
print_trade_rows(calls_all, "All Call Trades", top_n=30)

section("PUT TRADES — Sorted by P&L")
print_trade_rows(puts_all, "All Put Trades", top_n=30)

# ── Individual stock detail for Core AI ─────────────────────────────────────────
section("CORE AI/CHIPS — Detailed Results")
core_trades = sorted(
    [t for t in trades if t["column"] == "A (Core AI)"],
    key=lambda x: x["pnl"], reverse=True
)
for t in core_trades:
    print(f"  {t['sym']:<6}  {t['earn_day'].date()}  "
          f"entry=${t['pre_close']:.2f}  exit=${t['post_close']:.2f}  "
          f"earn={t['earn_ret']:>+6.1f}%  K={t['strike']:.2f}  "
          f"IV_pre={t['iv_pre']:.1%}  "
          f"prem ${t['prem_buy']:.2f}→${t['prem_sell']:.2f}  "
          f"n={t['n_contracts']}  P&L=${t['pnl']:>+,.0f} ({t['pnl_pct']:>+.1f}%)  {t['result']}")

# ── PFP Puts detail ──────────────────────────────────────────────────────────────
section("PRICED-FOR-PERFECTION PUTS — Detailed Results")
pfp_trades = sorted(
    [t for t in trades if t["column"] == "C (PFP Put)"],
    key=lambda x: x["pnl"], reverse=True
)
if pfp_trades:
    print(f"  {'Sym':<6}  {'Date':<12}  {'Earn%':>7}  {'Rev Gr':>7}  {'Mom30d':>7}  "
          f"{'K':>7}  {'Prem Buy':>9}  {'Prem Sell':>9}  {'P&L':>10}  {'Result'}")
    print(f"  {'─'*6}  {'─'*12}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*9}  {'─'*9}  {'─'*10}  {'─'*6}")
    for t in pfp_trades:
        rg = f"{t['rev_growth']:>+.0%}" if t["rev_growth"] is not None else "  N/A "
        mo = f"{t['momentum']:>+.0%}"   if t["momentum"]   is not None else "  N/A "
        print(f"  {t['sym']:<6}  {str(t['earn_day'].date()):<12}  "
              f"{t['earn_ret']:>+7.1f}%  {rg:>7}  {mo:>7}  "
              f"${t['strike']:>6.2f}  "
              f"${t['prem_buy']:>8.2f}  ${t['prem_sell']:>8.2f}  "
              f"${t['pnl']:>+9,.0f}  {t['result']}")
else:
    print("\n  (No PFP put trades found — revenue data may not be available for qualifying stocks)")
    print("  Stocks with momentum > 5% before earnings:")
    mom_cands = []
    for ev in events:
        m = compute_metrics(ev["sym"], ev["earn_day"], ev["pre_close"])
        if m and m["momentum"] is not None and m["momentum"] > PFP_MOMENTUM:
            rg = m["rev_growth"]
            mom_cands.append((ev["sym"], ev["earn_day"], m["momentum"], rg))
    mom_cands.sort(key=lambda x: x[2], reverse=True)
    for sym, ed, mom, rg in mom_cands[:20]:
        rg_s = f"{rg:+.0%}" if rg is not None else "no rev data"
        pfp = "PFP PUT" if (rg is not None and rg > PFP_REV_GROWTH) else ""
        print(f"    {sym:<6}  {ed.date()}  mom={mom:>+.1%}  rev_growth={rg_s:<12}  {pfp}")

# ── $10K per position sensitivity ────────────────────────────────────────────────
section("POSITION SIZING SUMMARY")
print(f"\n  Strategy uses ${POSITION_SIZE:,} per trade.")
print(f"  Avg contracts per trade : "
      f"{sum(t['n_contracts'] for t in trades)/len(trades):.1f}" if trades else "  N/A")
avg_cost = sum(t["cost"] for t in trades) / len(trades) if trades else 0
print(f"  Avg actual cost/trade   : ${avg_cost:,.0f}")
print(f"  Avg P&L per trade       : ${sum(t['pnl'] for t in trades)/len(trades):+,.0f}" if trades else "  N/A")

# ── Save full results ────────────────────────────────────────────────────────────
out_path = Path("data/backtest_new_methodology.json")
out_path.parent.mkdir(exist_ok=True)
save_data = []
for t in trades:
    row = dict(t)
    row["earn_day"] = str(t["earn_day"].date())
    save_data.append(row)
with open(out_path, "w") as f:
    json.dump(save_data, f, indent=2)
print(f"\n  Full trade details saved → {out_path}")

print("\nDone.")
