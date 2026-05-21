#!/usr/bin/env python3
"""
build_universe.py  —  Hitman v3 Universe Builder
=================================================
Layers:
  1. S&P 500          (~503 stocks, GitHub datasets CSV — Wikipedia 403s)
  2. NASDAQ 100       (~100 stocks, hardcoded NDX constituents)
  3. NASDAQ Global Select + Global Market  (Q/G tier from nasdaqtrader.com)
     filtered to market cap > $3B (proxy: price > $10, exclude penny/micro)
  4. International ADRs (hand-curated)
  5. QQQ holdings >= 0.2% weight  (slickcharts.com/nasdaq100)
  6. IWF-equivalent >= 0.2% weight  (SSGA SPYG xlsx + slickcharts S&P500)
     Note: iShares blocks automated IWF downloads; SPYG covers the S&P500
     Growth component (~85% of IWF overlap). Net-new additions are NYSE-listed
     large-caps not yet selected for the S&P500 by committee.

Then applies Hitman filters to every stock using current data:

  CALL filters (all required):
    beat_rate  >= 62%
    pu5        >= 40%
    momentum   +1% to +30%
    HV30       < 80%
    min 5 quarters of earnings history

  PUT filters (all required, no beat_rate needed):
    pd5        >= 40%   [% of earnings days with -5%+ drop]
    momentum   -30% to +30%
    HV30       < 80%

Confidence tier (position sizing):
  $10K — passes all filters, not biotech/financial, HV < 70%
  $5K  — biotech/pharma OR financial services OR HV > 70%  (one flag)
  $1K  — biotech/pharma AND (financial OR HV > 70%)        (stacked flags)

Output: universe snapshot + call candidates list
"""

import yfinance as yf
import pandas as pd
import numpy as np
import math
import json
import time
import requests
import warnings
from datetime import datetime, timedelta, date
from io import StringIO, BytesIO

warnings.filterwarnings("ignore")

# ── Hitman Call Filters ───────────────────────────────────────────────────────
MIN_BEAT_RATE   = 0.62
MIN_PU5         = 0.40
MIN_PD5         = 0.40   # puts: >= 40% of earnings days had -5%+ drop
MIN_MOMENTUM    = 0.01
MAX_MOMENTUM    = 0.30
MAX_HV          = 0.80
MIN_QUARTERS    = 5
MIN_PRICE       = 10.0
MIN_MCAP        = 3e9      # $3B market cap threshold

# ── Put momentum range (wider — no directional bias required) ─────────────────
PUT_MIN_MOMENTUM = -0.30
PUT_MAX_MOMENTUM =  0.30

# ── International ADRs ────────────────────────────────────────────────────────
INTL_ADRS = {
    # Tier 1 — liquid, quarterly, big moves
    "TSM":  "Taiwan Semiconductor (Taiwan)",
    "ASML": "ASML Holding (Netherlands)",
    "SE":   "Sea Limited (Singapore)",
    "NU":   "Nu Holdings (Brazil)",
    "MELI": "MercadoLibre (Argentina)",
    "SHOP": "Shopify (Canada)",
    "SAP":  "SAP SE (Germany)",
    # Tier 2 — liquid but higher political/regulatory risk
    "BABA": "Alibaba (China) [risk: delisting]",
    "PDD":  "PDD Holdings/Temu (China) [risk: delisting]",
    "BIDU": "Baidu (China) [risk: delisting]",
    # Other
    "NVO":  "Novo Nordisk (Denmark)",
    "SONY": "Sony Group (Japan)",
}

CHINA_TICKERS = {"BABA", "PDD", "BIDU", "JD", "TCOM", "XPEV", "NIO", "LI"}

# ── Confidence tier — sector classification ───────────────────────────────────
# Industries (yfinance 'industry' field, lowercased) that get the biotech flag
BIOTECH_KEYWORDS   = ("biotechnology", "drug manufacturer", "pharmaceutical", "diagnostics")
# Sectors (yfinance 'sector' field, lowercased) that get the financial flag
FINANCIAL_KEYWORDS = ("financial services", "financial", "bank", "insurance", "capital markets")

def classify_sector(sector: str, industry: str) -> str:
    """
    Returns 'biotech', 'financial', or 'other'.
    Used to apply position size caps.
    """
    s = (sector   or "").lower()
    i = (industry or "").lower()
    if any(kw in i for kw in BIOTECH_KEYWORDS):
        return "biotech"
    if any(kw in s for kw in FINANCIAL_KEYWORDS):
        return "financial"
    return "other"

def confidence_tier(sector_type: str, hv30: float) -> tuple[int, str]:
    """
    3-tier position sizing — no beat/pu5 re-weighting, purely sector + HV.

    $10K  — reliable sector, HV < 70%
    $5K   — biotech/financial OR HV > 70%  (one flag)
    $1K   — biotech/financial AND HV > 70%  (stacked — genuinely risky)
    """
    risky_sector = sector_type in ("biotech", "financial")
    high_hv      = hv30 > 0.70

    if risky_sector and high_hv:
        return 1_000, "LOW"
    elif risky_sector or high_hv:
        return 5_000, "MEDIUM"
    else:
        return 10_000, "HIGH"

TODAY = date.today()
LOOKBACK_YEARS = 3   # how far back to pull price history

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Fetch ticker lists
# ─────────────────────────────────────────────────────────────────────────────

def get_sp500() -> set:
    """Fetch S&P 500 tickers from GitHub datasets (Wikipedia 403s in automated envs)."""
    print("  Fetching S&P 500 from GitHub datasets...", end=" ", flush=True)
    urls = [
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
    ]
    for url in urls:
        try:
            df = pd.read_csv(url)
            # Column is 'Symbol' in this dataset
            col = next((c for c in df.columns if c.lower() in ("symbol", "ticker")), None)
            if col:
                tickers = set(df[col].str.replace(".", "-", regex=False).str.strip().tolist())
                print(f"{len(tickers)} tickers")
                return tickers
        except Exception:
            continue
    print("FAILED — falling back to empty set")
    return set()

def get_nasdaq100() -> set:
    """Fetch NASDAQ 100 tickers from GitHub datasets."""
    print("  Fetching NASDAQ 100 from GitHub datasets...", end=" ", flush=True)
    urls = [
        "https://raw.githubusercontent.com/datasets/nasdaq-listings/main/data/NASDAQ-listed.csv",
    ]
    # Simpler: just hardcode the stable NDX100 — it changes rarely and GitHub CSVs vary
    # These are the 101 NDX constituents as of early 2026
    ndx100 = {
        "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","COST",
        "NFLX","ASML","AMD","AZN","TMUS","QCOM","CSCO","PEP","ADBE","INTU",
        "TXN","CMCSA","AMGN","HON","ISRG","BKNG","VRTX","ARM","PANW","SBUX",
        "GILD","MU","REGN","LRCX","KLAC","CTAS","CDNS","SNPS","MDLZ","CEG",
        "MRVL","ORLY","PYPL","CRWD","FTNT","MAR","ABNB","MCHP","WDAY","ADSK",
        "PCAR","KDP","AEP","ROST","CPRT","FANG","IDXX","CTSH","FAST","BIIB",
        "DDOG","TEAM","MNST","ODFL","ON","EXC","VRSK","ANSS","GEHC","XEL",
        "CCEP","CDW","DLTR","TTWO","CSGP","GFS","ZS","ILMN","WBD","MRNA",
        "SMCI","NTAP","MDB","ALGN","DXCM","SIRI","SPLK","EXPE","PARA","LCID",
        "RIVN","ENPH","ZM","OKTA","DOCU","HOOD","COIN","PLTR","SNOW","RBLX",
        "MELI",
    }
    print(f"{len(ndx100)} tickers (hardcoded)")
    return ndx100

def get_nasdaq_global() -> set:
    """
    Download full NASDAQ listed file from nasdaqtrader.com.
    Keep only Market Category Q (Global Select) and G (Global Market).
    Exclude ETFs, test issues, and preferred shares.
    """
    print("  Fetching NASDAQ Global (Q+G tier) from nasdaqtrader.com...", end=" ", flush=True)
    url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), sep="|")
        # Drop the last row (file creation timestamp)
        df = df[df["Symbol"].notna() & (df["Symbol"] != "File Creation Time")]
        # Filter: Global Select (Q) or Global Market (G), not ETF, not test
        df = df[
            df["Market Category"].isin(["Q", "G"]) &
            (df["ETF"] == "N") &
            (df["Test Issue"] == "N")
        ]
        # Remove symbols with special chars (warrants, preferred, units)
        clean = df["Symbol"].str.strip()
        clean = clean[~clean.str.contains(r"[+$\^\.~%]", regex=True)]
        tickers = set(clean.tolist())
        print(f"{len(tickers)} tickers")
        return tickers
    except Exception as e:
        print(f"FAILED: {e}")
        return set()

def get_qqq_holdings(min_weight: float = 0.002) -> set:
    """
    Fetch QQQ (Invesco Nasdaq 100 ETF) holdings with weight >= min_weight.
    Source: slickcharts.com/nasdaq100 — public, no auth required.
    Returns set of ticker symbols.
    """
    print(f"  Fetching QQQ (Nasdaq 100) holdings >= {min_weight*100:.1f}% via slickcharts...", end=" ", flush=True)
    try:
        resp = requests.get(
            "https://www.slickcharts.com/nasdaq100",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        df = tables[0]
        sym_col    = next((c for c in df.columns if str(c).lower() in ("symbol", "ticker")), None)
        weight_col = next((c for c in df.columns if "weight" in str(c).lower()), None)
        if sym_col is None or weight_col is None:
            raise ValueError(f"Columns not found: {list(df.columns)}")
        df["_w"] = pd.to_numeric(
            df[weight_col].astype(str).str.replace("%", "").str.strip(),
            errors="coerce",
        ) / 100.0
        qualified = df[df["_w"] >= min_weight][sym_col].astype(str).str.strip()
        tickers = {t.replace(".", "-") for t in qualified if t and not t.startswith("-")}
        print(f"{len(tickers)} tickers")
        return tickers
    except Exception as e:
        print(f"FAILED ({e}) — returning empty set")
        return set()


def get_iwf_holdings(min_weight: float = 0.002) -> set:
    """
    Fetch IWF-equivalent large-cap growth holdings with weight >= min_weight.

    IWF tracks the Russell 1000 Growth index (~500 stocks). BlackRock/iShares
    blocks automated downloads, so we use two publicly accessible sources that
    together cover the meaningful IWF weight:

      1. SSGA SPYG  — SPDR S&P 500 Growth ETF (xlsx, ~230 stocks).
         Covers the S&P 500 Growth subset, which accounts for ~85% of IWF's
         total net assets by overlap.

      2. slickcharts S&P 500 with weight >= min_weight (top ~60 stocks).
         Captures any remaining large-cap S&P500 names not in SPYG.

    Both sources are deduplicated against the S&P500 + NASDAQ Global sets
    inside build_full_universe(), so only genuinely new tickers are added.
    The practical net-new additions are NYSE-listed large-caps not yet in the
    S&P500 committee selection — these pass naturally through the market-cap
    filter in Step 2.

    Returns set of ticker symbols.
    """
    print(f"  Fetching IWF-equivalent (SPYG + S&P500 growth leaders) >= {min_weight*100:.1f}%...", end=" ", flush=True)
    tickers: set = set()

    # Source 1: SSGA SPYG xlsx (daily holdings, ~230 S&P500 Growth stocks)
    try:
        r = requests.get(
            "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spyg.xlsx",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20,
        )
        r.raise_for_status()
        df = pd.read_excel(BytesIO(r.content), skiprows=4)
        df.columns = df.columns.str.strip()
        df = df[df["Ticker"].notna() & (df["Ticker"] != "-")]
        df["_w"] = pd.to_numeric(df["Weight"], errors="coerce")
        qualified = df[df["_w"] >= min_weight * 100]["Ticker"].astype(str).str.strip()
        tickers |= {t.replace(".", "-") for t in qualified if t and not t.startswith("-")}
    except Exception as e:
        print(f"[SPYG failed: {e}] ", end="")

    # Source 2: slickcharts S&P 500 weights — catches anything SPYG misses
    try:
        resp = requests.get(
            "https://www.slickcharts.com/sp500",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15,
        )
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        df2 = tables[0]
        sym_col    = next((c for c in df2.columns if str(c).lower() in ("symbol", "ticker")), None)
        weight_col = next((c for c in df2.columns if "weight" in str(c).lower()), None)
        if sym_col and weight_col:
            df2["_w"] = pd.to_numeric(
                df2[weight_col].astype(str).str.replace("%", "").str.strip(),
                errors="coerce",
            ) / 100.0
            qualified2 = df2[df2["_w"] >= min_weight][sym_col].astype(str).str.strip()
            tickers |= {t.replace(".", "-") for t in qualified2 if t and not t.startswith("-")}
    except Exception as e:
        print(f"[slickcharts S&P500 failed: {e}] ", end="")

    print(f"{len(tickers)} tickers (pre-dedup)")
    return tickers


def build_full_universe() -> list:
    print("\n[STEP 1] Building raw universe...")
    sp500    = get_sp500()
    ndx100   = get_nasdaq100()
    nasdaq_g = get_nasdaq_global()
    intl     = set(INTL_ADRS.keys())
    qqq      = get_qqq_holdings(min_weight=0.002)   # QQQ >= 0.2% weight
    iwf      = get_iwf_holdings(min_weight=0.002)   # IWF-equiv >= 0.2% weight

    # ETF layer: only add tickers NOT already in S&P500 or NASDAQ sources
    # (avoids double-counting; any truly new ticker still enters for mcap filter)
    etf_only = (qqq | iwf) - sp500 - ndx100 - nasdaq_g - intl

    combined = sp500 | ndx100 | nasdaq_g | intl | etf_only

    # Remove obvious non-equity suffixes
    remove_if = lambda s: (
        len(s) > 5 or
        s.endswith("W") or s.endswith("R") or s.endswith("U") or
        s.endswith("Z") or "__" in s
    )
    combined = {t for t in combined if not remove_if(t)}

    print(f"\n  S&P 500:               {len(sp500):>5}")
    print(f"  NASDAQ 100:            {len(ndx100):>5}")
    print(f"  NASDAQ Global:         {len(nasdaq_g):>5}")
    print(f"  International ADRs:    {len(intl):>5}")
    print(f"  QQQ >= 0.2% weight:    {len(qqq):>5}  (+{len(qqq - sp500 - ndx100 - nasdaq_g - intl)} net new)")
    print(f"  IWF-equiv >= 0.2%:     {len(iwf):>5}  (+{len(iwf - sp500 - ndx100 - nasdaq_g - intl)} net new)")
    print(f"  Combined unique:       {len(combined):>5}")
    return sorted(combined)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Market cap filter (batch yfinance fast_info)
# ─────────────────────────────────────────────────────────────────────────────

def filter_by_mcap(tickers: list, min_mcap: float = MIN_MCAP) -> list:
    print(f"\n[STEP 2] Filtering by market cap > ${min_mcap/1e9:.0f}B + price > ${MIN_PRICE}...")
    passed = []
    failed = 0
    skipped = 0

    BATCH = 80
    batches = [tickers[i:i+BATCH] for i in range(0, len(tickers), BATCH)]
    total = len(tickers)
    done = 0

    for batch in batches:
        for sym in batch:
            try:
                fi = yf.Ticker(sym).fast_info
                mcap  = getattr(fi, "market_cap",  None) or 0
                price = getattr(fi, "last_price",  None) or 0
                if mcap >= min_mcap and price >= MIN_PRICE:
                    passed.append(sym)
                else:
                    failed += 1
            except Exception:
                skipped += 1
            done += 1
        pct = done / total * 100
        print(f"  Progress: {done}/{total} ({pct:.0f}%) — passed: {len(passed)}    ", end="\r", flush=True)
        time.sleep(0.05)   # gentle throttle

    print(f"\n  Passed market cap filter: {len(passed)} / {total}")
    print(f"  Removed (too small):      {failed}")
    print(f"  Skipped (no data):        {skipped}")
    return passed

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Compute Hitman metrics per stock
# ─────────────────────────────────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def compute_hv30(prices: pd.Series) -> float:
    if len(prices) < 31:
        return np.nan
    rets = np.log(prices / prices.shift(1)).dropna()
    return float(rets.tail(30).std() * math.sqrt(252))

def compute_momentum_30d(prices: pd.Series) -> float:
    if len(prices) < 31:
        return np.nan
    p_now  = prices.iloc[-1]
    p_30   = prices.iloc[-31]
    return (p_now - p_30) / p_30

def get_earnings_moves(hist: pd.DataFrame, earn_dates: list) -> list:
    """
    For each earnings date, compute the next-trading-day return.
    Returns list of (date, pct_move) tuples, sorted oldest first.
    """
    if hist.empty:
        return []
    closes = hist["Close"].dropna()
    if hasattr(closes.index, "tz") and closes.index.tz is not None:
        closes.index = closes.index.tz_convert(None)
    closes.index = pd.to_datetime(closes.index).normalize()
    idx = closes.index

    moves = []
    for ed in sorted(earn_dates):
        ed_ts = pd.Timestamp(ed).normalize()
        # Find the close ON earnings day (or closest before)
        pos_before = idx.searchsorted(ed_ts, side="right") - 1
        if pos_before < 0 or pos_before + 1 >= len(idx):
            continue
        p_before = closes.iloc[pos_before]
        p_after  = closes.iloc[pos_before + 1]
        if p_before <= 0:
            continue
        move = (p_after - p_before) / p_before
        moves.append((ed, move))
    return moves

def compute_hitman_metrics(sym: str) -> dict | None:
    """
    Returns dict with beat_rate, pu5, momentum_30d, hv30, n_quarters, current_price
    or None if insufficient data.
    """
    try:
        tk = yf.Ticker(sym)

        # Price history
        end_dt   = datetime.today()
        start_dt = end_dt - timedelta(days=LOOKBACK_YEARS * 365 + 90)
        hist = tk.history(start=start_dt.strftime("%Y-%m-%d"),
                          end=end_dt.strftime("%Y-%m-%d"),
                          auto_adjust=True)
        if hist.empty or len(hist) < 60:
            return None

        prices = hist["Close"].dropna()

        # Earnings dates — try earnings_dates first, then calendar
        earn_dates = []
        try:
            ed_df = tk.earnings_dates
            if ed_df is not None and not ed_df.empty:
                # Only use past dates (not future)
                past = ed_df[ed_df.index < pd.Timestamp.now(tz="UTC")]
                if hasattr(past.index, "tz") and past.index.tz is not None:
                    past.index = past.index.tz_convert(None)
                earn_dates = [d.date() for d in past.index if not pd.isna(d)]
        except Exception:
            pass

        if len(earn_dates) < MIN_QUARTERS:
            return None

        # Use last 8 quarters
        earn_dates = sorted(set(earn_dates))[-8:]

        # Compute moves
        moves = get_earnings_moves(hist, earn_dates)
        if len(moves) < MIN_QUARTERS:
            return None

        move_vals = [m for _, m in moves]
        beat_rate = sum(1 for m in move_vals if m > 0)    / len(move_vals)
        pu5       = sum(1 for m in move_vals if m >= 0.05) / len(move_vals)
        pd5       = sum(1 for m in move_vals if m <= -0.05) / len(move_vals)

        momentum  = compute_momentum_30d(prices)
        hv30      = compute_hv30(prices)
        price     = float(prices.iloc[-1])

        if np.isnan(momentum) or np.isnan(hv30):
            return None

        # Next earnings (upcoming) — for info only
        next_earn = None
        try:
            cal = tk.calendar
            if cal is not None:
                if isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                    next_earn = str(cal.loc["Earnings Date"].iloc[0])
                elif isinstance(cal, dict) and "Earnings Date" in cal:
                    dates = cal["Earnings Date"]
                    if dates:
                        next_earn = str(dates[0] if isinstance(dates, list) else dates)
        except Exception:
            pass

        # Sector / industry (for confidence tier)
        yf_sector   = ""
        yf_industry = ""
        try:
            info        = tk.info
            yf_sector   = info.get("sector",   "") or ""
            yf_industry = info.get("industry", "") or ""
        except Exception:
            pass

        sector_type           = classify_sector(yf_sector, yf_industry)
        pos_size, conf_label  = confidence_tier(sector_type, hv30)

        return {
            "symbol":        sym,
            "beat_rate":     round(beat_rate, 3),
            "pu5":           round(pu5, 3),
            "pd5":           round(pd5, 3),
            "momentum_30d":  round(momentum, 3),
            "hv30":          round(hv30, 3),
            "n_quarters":    len(move_vals),
            "price":         round(price, 2),
            "next_earnings": next_earn,
            "sector":        yf_sector,
            "industry":      yf_industry,
            "sector_type":   sector_type,          # 'biotech' | 'financial' | 'other'
            "confidence":    conf_label,            # 'HIGH' | 'MEDIUM' | 'LOW'
            "position_size": pos_size,              # 10000 | 5000 | 1000
            "is_china":      sym in CHINA_TICKERS,
            "is_intl":       sym in INTL_ADRS,
            "intl_name":     INTL_ADRS.get(sym, ""),
            "recent_moves":  [round(m, 4) for _, m in moves[-4:]],
        }

    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Screen all stocks
# ─────────────────────────────────────────────────────────────────────────────

def apply_hitman_filters(metrics: dict) -> bool:
    """Call candidate: beat_rate + pu5 + positive momentum + low HV."""
    return (
        metrics["beat_rate"]    >= MIN_BEAT_RATE  and
        metrics["pu5"]          >= MIN_PU5         and
        MIN_MOMENTUM <= metrics["momentum_30d"] <= MAX_MOMENTUM and
        metrics["hv30"]         <  MAX_HV
    )

def apply_put_filters(metrics: dict) -> bool:
    """
    Put candidate: consistent earnings-day dumper, IV in range.
    No beat_rate requirement — stocks can dump even on EPS beats.
    Wider momentum range (-30% to +30%) — no directional bias needed.
    """
    return (
        metrics.get("pd5", 0.0) >= MIN_PD5  and
        PUT_MIN_MOMENTUM <= metrics["momentum_30d"] <= PUT_MAX_MOMENTUM and
        metrics["hv30"]  <  MAX_HV
    )

def screen_universe(tickers: list) -> tuple[list, list, list]:
    """
    Returns (all_metrics, call_candidates, put_candidates)
    """
    print(f"\n[STEP 3] Computing Hitman metrics for {len(tickers)} stocks...")
    print("  (This takes ~8-15 min — rate limited by yfinance)")

    all_metrics    = []
    call_candidates = []
    put_candidates  = []
    no_data        = 0

    for i, sym in enumerate(tickers):
        m = compute_hitman_metrics(sym)

        if m is None:
            no_data += 1
        else:
            all_metrics.append(m)
            if apply_hitman_filters(m):
                call_candidates.append(m)
            elif apply_put_filters(m):
                # Only add as put if it didn't already qualify as call
                put_candidates.append(m)

        if (i + 1) % 25 == 0 or (i + 1) == len(tickers):
            pct = (i + 1) / len(tickers) * 100
            print(f"  [{i+1:>4}/{len(tickers)}] {pct:>5.1f}%  "
                  f"calls: {len(call_candidates)}  puts: {len(put_candidates)}    ",
                  end="\r", flush=True)
        time.sleep(0.12)   # ~8 req/sec — stay within yfinance limits

    print(f"\n  Total screened:      {len(tickers)}")
    print(f"  Had enough data:     {len(all_metrics)}")
    print(f"  No/insufficient data:{no_data}")
    return all_metrics, call_candidates, put_candidates

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Report
# ─────────────────────────────────────────────────────────────────────────────

def print_report(all_metrics: list, call_candidates: list, put_candidates: list):
    total  = len(all_metrics)
    n_call = len(call_candidates)
    n_put  = len(put_candidates)

    print("\n" + "=" * 70)
    print(f"  HITMAN v3 UNIVERSE SCAN — {n_call} CALLS  |  {n_put} PUTS")
    print(f"  {TODAY}  |  Universe: {total} stocks with data")
    print("=" * 70)

    # ── Call filter funnel ────────────────────────────────────────────────────
    br_pass  = sum(1 for m in all_metrics if m["beat_rate"]    >= MIN_BEAT_RATE)
    pu5_pass = sum(1 for m in all_metrics if m["pu5"]          >= MIN_PU5)
    pd5_pass = sum(1 for m in all_metrics if m.get("pd5", 0)   >= MIN_PD5)
    mom_pass = sum(1 for m in all_metrics if MIN_MOMENTUM <= m["momentum_30d"] <= MAX_MOMENTUM)
    hv_pass  = sum(1 for m in all_metrics if m["hv30"]         <  MAX_HV)

    print(f"\n  -- Call filter funnel --")
    print(f"    Total with data:               {total:>4}")
    print(f"    beat_rate >= {MIN_BEAT_RATE:.0%}:             {br_pass:>4}  ({br_pass/total*100:.0f}%)")
    print(f"    pu5 >= {MIN_PU5:.0%}:                   {pu5_pass:>4}  ({pu5_pass/total*100:.0f}%)")
    print(f"    momentum {MIN_MOMENTUM:.0%} to {MAX_MOMENTUM:.0%}:         {mom_pass:>4}  ({mom_pass/total*100:.0f}%)")
    print(f"    HV30 < {MAX_HV:.0%}:                   {hv_pass:>4}  ({hv_pass/total*100:.0f}%)")
    print(f"    ALL call filters:              {n_call:>4}  ({n_call/total*100:.0f}%)")

    print(f"\n  -- Put filter funnel --")
    print(f"    pd5 >= {MIN_PD5:.0%}:                   {pd5_pass:>4}  ({pd5_pass/total*100:.0f}%)")
    print(f"    momentum {PUT_MIN_MOMENTUM:.0%} to +{PUT_MAX_MOMENTUM:.0%}:       {mom_pass:>4}  ({mom_pass/total*100:.0f}%)")
    print(f"    HV30 < {MAX_HV:.0%}:                   {hv_pass:>4}  ({hv_pass/total*100:.0f}%)")
    print(f"    ALL put filters (excl. calls): {n_put:>4}  ({n_put/total*100:.0f}%)")

    # ─────────────────────────────────────────────────────────────────────────
    # CALL CANDIDATES
    # ─────────────────────────────────────────────────────────────────────────
    if call_candidates:
        call_candidates.sort(key=lambda m: m["beat_rate"] * m["pu5"], reverse=True)
        domestic_c = [m for m in call_candidates if not m["is_intl"]]
        intl_c     = [m for m in call_candidates if m["is_intl"]]
        china_c    = [m for m in call_candidates if m["is_china"]]

        t10 = sum(1 for m in call_candidates if m.get("position_size") == 10_000)
        t5  = sum(1 for m in call_candidates if m.get("position_size") ==  5_000)
        t1  = sum(1 for m in call_candidates if m.get("position_size") ==  1_000)

        print(f"\n  -- CALL CANDIDATES: {n_call} total --")
        print(f"    Domestic: {len(domestic_c)}  |  Intl (non-China): {len([m for m in intl_c if not m['is_china']])}  |  China (flagged): {len(china_c)}")
        print(f"    Position tiers:  $10K x{t10}   $5K x{t5}   $1K x{t1}   "
              f"Max deploy: ${t10*10_000 + t5*5_000 + t1*1_000:,.0f}")

        print(f"\n  {'Symbol':<8} {'Tier':>6} {'Pos$':>7} {'Beat%':>6} {'PU5%':>6} {'Mom%':>6} {'HV%':>5} {'Sector':<10} {'Next Earn'}")
        print(f"  {'-'*7:<8} {'-'*6:>6} {'-'*7:>7} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6} {'-'*5:>5} {'-'*10:<10} {'-'*12}")
        for m in call_candidates:
            flag = " [CHINA]" if m["is_china"] else (" [INTL]" if m["is_intl"] else "")
            ne   = (m["next_earnings"] or "")[:10]
            sec  = (m.get("sector_type") or "other")[:6].upper()
            pos  = f"${m.get('position_size', 10000):,}"
            conf = m.get("confidence", "HIGH")
            print(f"  {m['symbol']:<8} {conf:>6} {pos:>7}  {m['beat_rate']:>5.0%}  {m['pu5']:>5.0%}  "
                  f"{m['momentum_30d']:>+5.1%}  {m['hv30']:>4.0%}  {sec:<10} {ne}{flag}")

        avg_br  = np.mean([m["beat_rate"]    for m in call_candidates])
        avg_pu5 = np.mean([m["pu5"]          for m in call_candidates])
        avg_mom = np.mean([m["momentum_30d"] for m in call_candidates])
        avg_hv  = np.mean([m["hv30"]         for m in call_candidates])
        print(f"\n  Call averages:  beat={avg_br:.0%}  pu5={avg_pu5:.0%}  mom={avg_mom:+.1%}  HV={avg_hv:.0%}")

        with_earn = sorted(
            [m for m in call_candidates if m["next_earnings"] and m["next_earnings"] != "None"],
            key=lambda m: m["next_earnings"] or "9999",
        )
        if with_earn:
            print(f"\n  Upcoming earnings ({len(with_earn)} calls):")
            for m in with_earn[:20]:
                flag = " [CHINA]" if m["is_china"] else (" [INTL]" if m["is_intl"] else "")
                print(f"    {m['symbol']:<8}  next: {(m['next_earnings'] or '')[:10]}  "
                      f"beat={m['beat_rate']:.0%}  pu5={m['pu5']:.0%}{flag}")
    else:
        print("\n  No call candidates today.")

    # ─────────────────────────────────────────────────────────────────────────
    # PUT CANDIDATES
    # ─────────────────────────────────────────────────────────────────────────
    if put_candidates:
        put_candidates.sort(key=lambda m: m.get("pd5", 0), reverse=True)
        domestic_p = [m for m in put_candidates if not m["is_intl"]]
        china_p    = [m for m in put_candidates if m["is_china"]]

        print(f"\n  -- PUT CANDIDATES: {n_put} total --")
        print(f"    Domestic: {len(domestic_p)}  |  China (flagged): {len(china_p)}")

        print(f"\n  {'Symbol':<8} {'PD5%':>6} {'Mom%':>6} {'HV%':>5} {'Sector':<10} {'Next Earn'}")
        print(f"  {'-'*7:<8} {'-'*6:>6} {'-'*6:>6} {'-'*5:>5} {'-'*10:<10} {'-'*12}")
        for m in put_candidates:
            flag = " [CHINA]" if m["is_china"] else (" [INTL]" if m["is_intl"] else "")
            ne   = (m["next_earnings"] or "")[:10]
            sec  = (m.get("sector_type") or "other")[:6].upper()
            pd5v = m.get("pd5", 0.0)
            print(f"  {m['symbol']:<8} {pd5v:>5.0%}  "
                  f"{m['momentum_30d']:>+5.1%}  {m['hv30']:>4.0%}  {sec:<10} {ne}{flag}")

        avg_pd5 = np.mean([m.get("pd5", 0) for m in put_candidates])
        avg_mom = np.mean([m["momentum_30d"] for m in put_candidates])
        avg_hv  = np.mean([m["hv30"]         for m in put_candidates])
        print(f"\n  Put averages:  pd5={avg_pd5:.0%}  mom={avg_mom:+.1%}  HV={avg_hv:.0%}")

        with_earn_p = sorted(
            [m for m in put_candidates if m["next_earnings"] and m["next_earnings"] != "None"],
            key=lambda m: m["next_earnings"] or "9999",
        )
        if with_earn_p:
            print(f"\n  Upcoming earnings ({len(with_earn_p)} puts):")
            for m in with_earn_p[:20]:
                flag = " [CHINA]" if m["is_china"] else (" [INTL]" if m["is_intl"] else "")
                print(f"    {m['symbol']:<8}  next: {(m['next_earnings'] or '')[:10]}  "
                      f"pd5={m.get('pd5', 0):.0%}  mom={m['momentum_30d']:+.0%}{flag}")
    else:
        print("\n  No put candidates today.")

def save_results(all_metrics: list, call_candidates: list, put_candidates: list):
    out = {
        "generated":            TODAY.isoformat(),
        "total_screened":       len(all_metrics),
        "total_call_candidates": len(call_candidates),
        "total_put_candidates":  len(put_candidates),
        "filters": {
            "min_beat_rate":     MIN_BEAT_RATE,
            "min_pu5":           MIN_PU5,
            "min_pd5":           MIN_PD5,
            "min_momentum":      MIN_MOMENTUM,
            "max_momentum":      MAX_MOMENTUM,
            "put_min_momentum":  PUT_MIN_MOMENTUM,
            "put_max_momentum":  PUT_MAX_MOMENTUM,
            "max_hv30":          MAX_HV,
            "min_quarters":      MIN_QUARTERS,
            "min_price":         MIN_PRICE,
            "min_mcap_bn":       MIN_MCAP / 1e9,
        },
        "call_candidates": call_candidates,
        "put_candidates":  put_candidates,
        # Legacy key kept for backwards compatibility with scanner.py _load_universe()
        "candidates":      call_candidates,
        "full_universe":   all_metrics,
    }
    path = r"C:\Users\krish\Documents\Claude Stock Trader\data\universe_snapshot.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  Saved -> {path}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  HITMAN v3 — UNIVERSE BUILDER")
    print(f"  {TODAY}  |  S&P500 + NDX100 + NASDAQ Global + Intl ADRs")
    print(f"  Screens for CALL candidates (beat+pu5) AND PUT candidates (pd5)")
    print("=" * 70)

    # 1. Build raw ticker list
    raw_tickers = build_full_universe()

    # 2. Market cap filter
    qualified = filter_by_mcap(raw_tickers)

    # 3. Screen with Hitman metrics
    all_metrics, call_candidates, put_candidates = screen_universe(qualified)

    # 4. Save first — so a crash in print_report never loses computed data
    save_results(all_metrics, call_candidates, put_candidates)

    # 5. Report
    print_report(all_metrics, call_candidates, put_candidates)

    print("\nDone.")
