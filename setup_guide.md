# Earnings Trading Bot — Setup Guide

## Quick Start (5 minutes)

### 1. Install Dependencies
```bash
cd "C:/Users/krish/Documents/Claude Stock Trader"
pip install -r requirements.txt
```

### 2. Test Alpaca Connection
```bash
cd src
python -c "from utils import get_trading_client; c = get_trading_client(); print('Account:', c.get_account().equity)"
```
Should print your paper account equity (~$100,000).

### 3. Run Scanner Manually (First Test)
```bash
python src/scanner.py
```
This scans ~600 stocks for earnings in the next 14 days. Takes 5-10 minutes.
Results saved to `data/recommendations.json`.

### 4. View Dashboard
Open `dashboard.html` in a browser via a local server:
```bash
python -m http.server 8080
# Then visit: http://localhost:8080/dashboard.html
```

---

## Configuration

All settings are in `config/settings.json`.

### Email Alerts (Optional but Recommended)
```json
"email": {
  "enabled": true,
  "smtp_server": "smtp.gmail.com",
  "smtp_port": 587,
  "username": "your@gmail.com",
  "password": "your-app-password",
  "recipient": "your@gmail.com"
}
```
For Gmail: use an **App Password** (Google Account → Security → 2FA → App Passwords).

---

## Scheduled Tasks (Cloud Automation)

The bot needs 6 tasks scheduled in the Claude Code scheduled tasks system.
Run these commands to set them up:

| Task | Schedule | Command |
|------|----------|---------|
| Scanner | 11 PM daily | `python src/scanner.py` |
| Validator | 8 AM weekdays | `python src/validator.py` |
| Executor | 9:35 AM weekdays | `python src/executor.py` |
| Monitor | Hourly 10AM–4PM weekdays | `python src/monitor.py` |
| Reporter | 4:30 PM weekdays | `python src/reporter.py` |
| Learner | 9 PM Sundays | `python src/learner.py` |

**All times are Eastern (ET).**

---

## Safety Features

### Kill Switch
Create a file named `STOP_TRADING` in the project root to halt all activity:
```bash
touch STOP_TRADING        # halt
rm STOP_TRADING           # resume
```

### Circuit Breakers (Auto-trigger)
- Daily loss > 3% of deployed capital → halt + close all positions
- Weekly loss > 10% of deployed capital → halt + close all positions

### Budget Hard Caps
- First 2 weeks (Apr 19 – May 3): max $10,000 total
- All-time earnings trades: max $45,000
- These are enforced in code — executor refuses to trade beyond limits

### Manual Halt
Set `"halted": true` in `data/budget_tracker.json` to stop trading without deleting the kill switch.

---

## File Structure

```
Claude Stock Trader/
├── src/
│   ├── scanner.py      # Task 1: Nightly earnings scanner
│   ├── validator.py    # Task 2: Pre-market validator
│   ├── executor.py     # Task 3: Trade executor
│   ├── monitor.py      # Task 4: Hourly position monitor
│   ├── reporter.py     # Task 5: Daily P&L report
│   ├── learner.py      # Task 6: Weekly strategy optimizer
│   └── utils.py        # Shared utilities
├── data/
│   ├── recommendations.json   # Upcoming earnings plays
│   ├── trades_history.json    # All executed trades
│   ├── positions.json         # Live positions + P&L
│   ├── budget_tracker.json    # Budget status
│   └── learning_weights.json  # AI-adjusted scoring weights
├── config/
│   └── settings.json   # All configuration
├── reports/            # Daily HTML reports
├── logs/               # Per-module log files
├── dashboard.html      # React web dashboard
├── STOP_TRADING        # Create this file to halt (gitignored)
└── requirements.txt
```

---

## How the Confidence Score Works

| Factor | Points | Condition |
|--------|--------|-----------|
| Base | +50 | Always |
| Historical beat rate | +30 | ≥75% of recent quarters beat EPS estimate |
| Revenue growth | +20 | >20% YoY revenue growth |
| 30-day momentum | +15 | >10% price appreciation |
| Tech/Comms sector | +5 | Technology or Communication Services |
| Mega-cap | +5 | Market cap > $100B |
| Low beat rate | -10 | <50% beat rate |
| Declining revenue | -15 | Negative YoY revenue |
| Negative momentum | -15 | <-10% 30-day performance |

**Minimum to trade: 90%** (only ~1 in 10 scanned stocks qualifies).
**Position sizing**: 95–100% → $1,500 | 90–94% → $1,000

After each week, `learner.py` adjusts the weights based on which factors actually predicted winners.

---

## Eligibility Screening

A stock must pass ALL of:
1. **Age**: IPO date ≥ 5 years ago (or on mega-cap exception list: ABNB, COIN, SNOW, UBER, LYFT, PLTR)
2. **Size**: Market cap ≥ $1 billion
3. **Financial health**: Net income never worse than -50% of market cap in any of last 5 years

---

## Testing Before Going Live

1. Run scanner: `python src/scanner.py` — check `data/recommendations.json`
2. Manually set `"validated": true` on one recommendation in `recommendations.json`
3. Run executor: `python src/executor.py` — verify order appears in Alpaca paper dashboard
4. Check Alpaca paper account at https://app.alpaca.markets (Paper Trading tab)
5. Run monitor: `python src/monitor.py` — verify `data/positions.json` updates
6. Run reporter: `python src/reporter.py` — check `reports/` folder for HTML file

---

## GitHub Pages Dashboard (Optional)

To view the dashboard from anywhere:
1. Push this repo to GitHub
2. Enable GitHub Pages (Settings → Pages → main branch / root)
3. In `dashboard.html`, set:
   ```js
   const GITHUB_RAW = "https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main";
   ```
4. The dashboard auto-fetches latest JSON files from GitHub every 5 minutes

Note: data files must be committed and pushed for the public dashboard to update.
