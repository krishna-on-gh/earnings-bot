# Key Protocol — Full Performance Archive
*Archived: 2026-05-19 | Pre-Hitman v2 migration (Airlift Protocol)*

---

## Overview

The **Key Protocol** was the original earnings-momentum stock trading methodology. It used a composite confidence score (beat_rate × revenue growth × momentum) to size and filter equity positions, entering 9:35 AM ET on scan day and exiting the morning after earnings. Positions were held as plain stock (long only) using bracket orders with 50% stop-loss and 2× take-profit targets.

- **Active period:** April 20 – May 19, 2026
- **Total trades placed:** 54 (53 closed, 1 still open as of archive date)
- **Paper trading account cap:** $45,000

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total closed trades | 53 |
| Wins (P&L > 0) | 29 |
| Losses (P&L < 0) | 24 |
| **Win rate** | **54.7%** |
| **Net P&L (closed trades)** | **+$1,288.37** |
| Avg P&L per trade | +$24.31 |
| Avg winning trade | +$128.93 |
| Avg losing trade | -$102.11 |
| Biggest win | DDOG +$636.24 (+44.89%) |
| Biggest loss | RBLX -$467.50 (-30.71%) |
| Account cap | $45,000 |
| Return on cap | **+2.9%** |
| Open position (not closed) | NVDA — entry $222.03 × 6 shares, earnings May 20 |

---

## Confidence Score Accuracy

> "Correct" = stock price went up the day after earnings (direction accuracy, not P&L)

| Confidence Tier | Calls | Correct | Accuracy |
|-----------------|-------|---------|----------|
| 70–84% | 9 | 8 | **88.9%** |
| 85–94% | 23 | 14 | **60.9%** |
| 95–100% | 21 | 12 | **57.1%** |
| **Total** | **53** | **34** | **64.2%** |

**Key finding:** Lower-confidence picks (70–84%) paradoxically outperformed higher-confidence picks on direction accuracy. High-confidence stocks were often priced for perfection — even beating estimates didn't always move them up.

---

## Top 10 Winners

| Symbol | Company | Entry Date | P&L | P&L % | Confidence | Beat Rate |
|--------|---------|------------|-----|--------|------------|-----------|
| DDOG | Datadog | Apr 23 | +$636.24 | +44.89% | 100 | 100% |
| AMD | Adv. Micro Devices | Apr 22 | +$603.45 | +40.92% | 100 | 75% |
| QCOM | Qualcomm | Apr 20 | +$326.30 | +34.14% | 90 | 100% |
| TWLO | Twilio | Apr 20 | +$330.70 | +23.16% | 100 | 100% |
| CSCO | Cisco | May 4 | +$397.92 | +26.85% | 100 | 100% |
| INTC | Intel | Apr 20 | +$212.30 | +22.39% | 90 | 75% |
| FTNT | Fortinet | Apr 22 | +$123.75 | +28.62% | 85 | 100% |
| U | Unity Software | Apr 23 | +$216.00 | +14.59% | 100 | 100% |
| AMAT | Applied Materials | May 4 | +$150.65 | +12.88% | 100 | 100% |
| CAT | Caterpillar | Apr 20 | +$93.54 | +11.74% | 70 | 50% |

---

## Top 10 Losers

| Symbol | Company | Entry Date | P&L | P&L % | Confidence | Beat Rate |
|--------|---------|------------|-----|--------|------------|-----------|
| RBLX | Roblox | Apr 20 | -$467.50 | -30.71% | 100 | 75% |
| HOOD | Robinhood | Apr 20 | -$307.79 | -21.40% | 100 | 100% |
| SOFI | SoFi Technologies | Apr 20 | -$276.86 | -18.55% | 100 | 100% |
| NOW | ServiceNow | Apr 20 | -$206.25 | -13.88% | 95 | 100% |
| PLTR | Palantir | Apr 22 | -$122.22 | -8.98% | 100 | 100% |
| ZTS | Zoetis | Apr 23 | -$112.88 | -24.17% | 80 | 100% |
| NET | Cloudflare | Apr 23 | -$89.39 | -6.18% | 100 | 75% |
| SHOP | Shopify | Apr 22 | -$76.82 | -19.42% | 80 | 50% |
| IBM | IBM | Apr 20 | -$65.93 | -8.69% | 90 | 100% |
| REGN | Regeneron | Apr 20 | -$63.37 | -8.45% | 80 | 75% |

---

## Full Trade Log — All 53 Closed Trades

| # | Symbol | Company | Entry | Exit | Size | P&L | P&L% | Conf | Beat | Outcome |
|---|--------|---------|-------|------|------|-----|------|------|------|---------|
| 1 | GOOGL | Alphabet | Apr 20 | Apr 30 | $1,369.62 | +$136.74 | +10.07% | 100 | 100% | WIN |
| 2 | AMZN | Amazon | Apr 20 | Apr 30 | $1,457.07 | +$42.01 | +2.81% | 100 | 75% | WIN |
| 3 | META | Meta Platforms | Apr 20 | Apr 30 | $1,336.73 | -$149.52 | -10.98% | 100 | 75% | LOSS |
| 4 | TMO | Thermo Fisher | Apr 20 | Apr 23 | $1,047.81 | -$126.46 | -12.07% | 100 | 100% | LOSS |
| 5 | ISRG | Intuitive Surgical | Apr 20 | Apr 22 | $1,410.83 | +$53.80 | +3.81% | 100 | 100% | WIN |
| 6 | BKNG | Booking Holdings | Apr 20 | Apr 29 | $1,319.53 | -$135.66 | -10.07% | 100 | 100% | LOSS |
| 7 | AXP | American Express | Apr 20 | Apr 23 | $1,320.20 | -$49.87 | -3.73% | 100 | 75% | LOSS |
| 8 | LRCX | Lam Research | Apr 20 | Apr 23 | $1,322.07 | -$8.23 | -0.62% | 100 | 100% | LOSS |
| 9 | ROKU | Roku | Apr 20 | May 1 | $1,385.94 | +$102.60 | +7.34% | 100 | 100% | WIN |
| 10 | TWLO | Twilio | Apr 20 | May 1 | $1,458.35 | +$330.70 | +23.16% | 100 | 100% | WIN |
| 11 | RBLX | Roblox | Apr 20 | May 1 | $1,487.88 | -$467.50 | -30.71% | 100 | 75% | LOSS |
| 12 | HOOD | Robinhood | Apr 20 | Apr 29 | $1,444.40 | -$307.79 | -21.40% | 100 | 100% | LOSS |
| 13 | SOFI | SoFi Technologies | Apr 20 | Apr 29 | $1,496.11 | -$276.86 | -18.55% | 100 | 100% | LOSS |
| 14 | AAPL | Apple | Apr 20 | May 1 | $817.44 | +$26.27 | +3.22% | 90 | 100% | WIN |
| 15 | QCOM | Qualcomm | Apr 20 | Apr 30 | $947.13 | +$326.30 | +34.14% | 90 | 100% | WIN |
| 16 | INTC | Intel | Apr 20 | Apr 24 | $947.80 | +$212.30 | +22.39% | 90 | 75% | WIN |
| 17 | V | Visa | Apr 20 | Apr 29 | $308.35 | +$18.79 | +5.92% | 85 | 100% | WIN |
| 18 | PG | Procter & Gamble | Apr 20 | Apr 24 | $442.77 | +$14.67 | +3.36% | 85 | 100% | WIN |
| 19 | ABBV | AbbVie | Apr 20 | Apr 29 | $416.26 | -$6.86 | -1.65% | 85 | 100% | LOSS |
| 20 | MRK | Merck | Apr 20 | Apr 30 | $462.12 | -$32.25 | -6.81% | 85 | 100% | LOSS |
| 21 | KO | Coca-Cola | Apr 20 | Apr 28 | $455.19 | +$25.08 | +5.51% | 85 | 100% | WIN |
| 22 | PM | Philip Morris | Apr 20 | Apr 22 | $477.96 | +$9.05 | +1.89% | 85 | 100% | WIN |
| 23 | HON | Honeywell | Apr 20 | Apr 23 | $466.76 | -$36.96 | -7.96% | 85 | 100% | LOSS |
| 24 | GE | GE Aerospace | Apr 20 | Apr 21 | $308.75 | -$16.86 | -5.58% | 85 | 100% | LOSS |
| 25 | SYK | Stryker | Apr 20 | May 1 | $345.56 | -$35.12 | -10.24% | 85 | 100% | LOSS |
| 26 | GILD | Gilead Sciences | Apr 20 | Apr 24 | $414.33 | -$20.91 | -5.06% | 85 | 100% | LOSS |
| 27 | REGN | Regeneron | Apr 20 | Apr 29 | $750.28 | -$63.37 | -8.45% | 80 | 75% | LOSS |
| 28 | TXN | Texas Instruments | Apr 20 | Apr 23 | $461.30 | +$98.26 | +21.30% | 75 | 50% | WIN |
| 29 | UNH | UnitedHealth | Apr 20 | Apr 21 | $316.17 | +$26.14 | +8.05% | 70 | 50% | WIN |
| 30 | XOM | ExxonMobil | Apr 20 | May 1 | $448.74 | +$11.37 | +2.57% | 70 | 100% | WIN |
| 31 | CVX | Chevron | Apr 20 | May 1 | $369.99 | +$11.53 | +3.11% | 70 | 100% | WIN |
| 32 | NOW | ServiceNow | Apr 20 | Apr 23 | $1,485.60 | -$206.25 | -13.88% | 95 | 100% | LOSS |
| 33 | MSFT | Microsoft | Apr 20 | Apr 30 | $835.16 | -$29.64 | -3.55% | 90 | 100% | LOSS |
| 34 | IBM | IBM | Apr 20 | Apr 23 | $803.25 | -$65.93 | -8.69% | 90 | 100% | LOSS |
| 35 | MA | Mastercard | Apr 20 | Apr 30 | $515.39 | -$7.42 | -1.44% | 85 | 100% | LOSS |
| 36 | LIN | Linde | Apr 20 | May 1 | $498.02 | +$7.98 | +1.60% | 85 | 100% | WIN |
| 37 | DHR | Danaher | Apr 20 | Apr 21 | $389.70 | -$5.92 | -1.52% | 85 | 100% | LOSS |
| 38 | NEE | NextEra Energy | Apr 20 | Apr 23 | $461.75 | +$17.85 | +3.87% | 85 | 100% | WIN |
| 39 | SPGI | S&P Global | Apr 20 | Apr 28 | $466.73 | +$6.44 | +1.46% | 85 | 75% | WIN |
| 40 | CAT | Caterpillar | Apr 20 | Apr 30 | $797.07 | +$93.54 | +11.74% | 70 | 50% | WIN |
| 41 | AMD | Adv. Micro Devices | Apr 22 | May 6 | $1,494.95 | +$603.45 | +40.92% | 100 | 75% | WIN |
| 42 | PLTR | Palantir | Apr 22 | May 5 | $1,360.80 | -$122.22 | -8.98% | 100 | 100% | LOSS |
| 43 | UBER | Uber | Apr 22 | May 7 | $983.19 | +$21.84 | +2.22% | 90 | 75% | WIN |
| 44 | FTNT | Fortinet | Apr 22 | May 7 | $446.75 | +$123.75 | +28.62% | 85 | 100% | WIN |
| 45 | SHOP | Shopify | Apr 22 | May 6 | $449.43 | -$76.82 | -19.42% | 80 | 50% | LOSS |
| 46 | DDOG | Datadog | Apr 23 | May 7 | $1,417.57 | +$636.24 | +44.89% | 100 | 100% | WIN |
| 47 | NET | Cloudflare | Apr 23 | May 8 | $1,456.00 | -$89.39 | -6.18% | 100 | 75% | LOSS |
| 48 | U | Unity Software | Apr 23 | May 7 | $1,480.20 | +$216.00 | +14.59% | 100 | 100% | WIN |
| 49 | LYFT | Lyft | Apr 23 | May 8 | $495.95 | +$6.91 | +1.39% | 85 | 100% | WIN |
| 50 | ZTS | Zoetis | Apr 23 | May 7 | $467.08 | -$112.88 | -24.17% | 80 | 100% | LOSS |
| 51 | GILD | Gilead Sciences | Apr 27 | May 8 | $385.48 | +$10.88 | +2.82% | 83 | 100% | WIN |
| 52 | CSCO | Cisco | May 4 | May 14 | $1,481.76 | +$397.92 | +26.85% | 100 | 100% | WIN |
| 53 | AMAT | Applied Materials | May 4 | May 15 | $1,170.21 | +$150.65 | +12.88% | 100 | 100% | WIN |

---

## Open Position at Archive Date

| Symbol | Company | Entry Date | Entry Price | Qty | Position Size | Earnings Date | Confidence |
|--------|---------|------------|-------------|-----|---------------|---------------|------------|
| NVDA | NVIDIA Corporation | May 11 | $222.03 | 6 | $1,333.20 | May 20, 2026 | 100 |

---

## Performance by Confidence Tier

| Tier | Trades | Wins | Losses | Win Rate | Total P&L | Avg P&L |
|------|--------|------|--------|----------|-----------|---------|
| 95–100% | 21 | 11 | 10 | 52.4% | +$832.17 | +$39.63 |
| 85–94% | 22 | 14 | 8 | 63.6% | +$57.55 | +$2.61 |
| 70–84% | 10 | 4 | 6 | 40.0% | +$398.65 | +$39.87 |
| **Total** | **53** | **29** | **24** | **54.7%** | **+$1,288.37** | **+$24.31** |

> Note: Win rate here is P&L-based (trade profitable). "Direction accuracy" from earnings_accuracy.json uses a different definition (price closed higher the day after earnings).

---

## Performance by Sector

| Sector | Trades | Wins | Win Rate | Net P&L |
|--------|--------|------|----------|---------|
| Technology | 22 | 14 | 63.6% | +$1,493.88 |
| Healthcare | 10 | 3 | 30.0% | -$488.04 |
| Financial Services | 6 | 4 | 66.7% | -$277.17 |
| Communication Services | 5 | 3 | 60.0% | -$425.87 |
| Consumer Defensive | 4 | 4 | 100.0% | +$59.85 |
| Industrials | 4 | 2 | 50.0% | +$39.76 |
| Consumer Cyclical | 2 | 1 | 50.0% | -$93.65 |
| Energy | 2 | 2 | 100.0% | +$22.90 |
| Basic Materials | 1 | 1 | 100.0% | +$7.98 |
| Utilities | 1 | 1 | 100.0% | +$17.85 |

---

## Budget Summary

| Item | Value |
|------|-------|
| Account cap | $45,000 |
| Two-week cap (Apr 19 – May 3) | $45,000 |
| Total deployed (cumulative open) | $14,322.82 |
| Available | $30,677.18 |
| Net P&L (closed trades) | +$1,288.37 |
| Return on cap | +2.9% |

---

## Key Protocol — Methodology Notes

**What it was:**
- Confidence score = weighted combination of beat_rate, revenue_growth_yoy, and momentum_30d
- Position sizing: tiered by confidence score (roughly $300–$1,500 per trade)
- Universe: S&P 500 stocks screened for upcoming earnings
- Entry: 9:35 AM ET on scan day (pre-earnings, 1–14 day hold)
- Exit: Morning after earnings (market open)
- Order type: Market order + bracket (50% stop-loss, 2× take-profit)

**Why it was retired:**
- Confidence score did not predict post-earnings direction reliably (64% direction accuracy overall, worse at high confidence)
- High-confidence stocks were priced for perfection — beat rate alone didn't move them up
- Position sizing was inconsistent and capital-inefficient
- No IV filter: entered options-expensive situations without edge
- Replaced by Hitman v2: options-based, fixed $10K/trade, IV-gated, 4-condition filter (beat_rate ≥ 62%, pu5 ≥ 40%, momentum +1–30%, IV < 80%)

---

*This document was created as part of the Airlift Protocol (Phase 7 pre-reset archive).*
*All trades above were paper trades on Alpaca Paper Trading.*
