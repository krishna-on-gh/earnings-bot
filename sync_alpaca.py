"""
Sync today's Alpaca orders back into local data files.
Fixes: trades_history.json, positions.json, then regenerates today's report.
"""
import os, sys, json, uuid
from datetime import datetime, timezone, date

sys.path.insert(0, 'src')

# ── Read credentials (env vars take priority, fallback to .env) ───────
env = {}
if os.path.exists('.env'):
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
# Environment variables override .env (used in GitHub Actions)
for key in ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY', 'ALPACA_BASE_URL'):
    if os.environ.get(key):
        env[key] = os.environ[key]

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

client = TradingClient(env['ALPACA_API_KEY'], env['ALPACA_SECRET_KEY'], paper=True)

# ── Fetch today's filled orders ───────────────────────────────────────
print("Fetching today's Alpaca orders...")
req = GetOrdersRequest(
    status=QueryOrderStatus.ALL,
    after=datetime(2026, 4, 23, 0, 0, 0, tzinfo=timezone.utc),
    limit=100
)
all_orders = client.get_orders(req)

# Only care about filled buys and sells
filled_sells = [o for o in all_orders if o.side.value == 'sell' and o.status.value == 'filled']
filled_buys  = [o for o in all_orders if o.side.value == 'buy'  and o.status.value == 'filled']

print(f"  Filled sells: {len(filled_sells)}")
print(f"  Filled buys:  {len(filled_buys)}")

# ── Load local data ───────────────────────────────────────────────────
trades   = json.load(open('data/trades_history.json'))
pos_data = json.load(open('data/positions.json'))

# Build lookup: symbol → open trade entry
open_trades = {t['symbol']: t for t in trades if t.get('status') == 'open'}

# ── Process sells (close existing positions) ──────────────────────────
print("\nProcessing exits...")
closed_today = []
for o in filled_sells:
    sym        = o.symbol
    exit_price = float(o.filled_avg_price)
    exit_time  = o.filled_at.strftime('%Y-%m-%d') if o.filled_at else '2026-04-23'

    if sym in open_trades:
        t = open_trades[sym]
        entry_price  = float(t['entry_price'])
        qty          = float(t['qty'])
        pnl          = round((exit_price - entry_price) * qty, 2)
        pnl_pct      = round((exit_price - entry_price) / entry_price * 100, 2)

        t['status']     = 'closed'
        t['exit_price'] = exit_price
        t['exit_date']  = exit_time
        t['pnl']        = pnl
        t['pnl_pct']    = pnl_pct

        result = "WIN" if pnl > 0 else "LOSS"
        print(f"  {sym:<6} {qty:.0f} sh  entry ${entry_price:.2f} to exit ${exit_price:.2f}  "
              f"P&L ${pnl:>+.2f} ({pnl_pct:>+.1f}%)  {result}")
        closed_today.append(t)
    else:
        print(f"  {sym:<6} sold but no matching open trade found — skipping")

# ── Process buys (open new positions) ────────────────────────────────
print("\nProcessing new entries...")

# Get current Alpaca positions for metadata
alpaca_positions = {p.symbol: p for p in client.get_all_positions()}

# Company name lookup (best effort from existing trades)
known_companies = {t['symbol']: t.get('company', t['symbol']) for t in trades}

# Sector lookup from existing trades
known_sectors = {t['symbol']: t.get('sector', 'Unknown') for t in trades}

# Build set of symbols already in trades_history to avoid duplication
existing_open_syms = {t['symbol'] for t in trades if t.get('status') == 'open'}

new_trades = []
for o in filled_buys:
    sym        = o.symbol
    entry_price = float(o.filled_avg_price)
    qty        = int(float(o.qty))
    entry_time = o.filled_at.isoformat() if o.filled_at else '2026-04-23T15:27:00-04:00'
    entry_date = o.filled_at.strftime('%Y-%m-%d') if o.filled_at else '2026-04-23'

    if sym in existing_open_syms:
        print(f"  {sym:<6} already in open trades — skipping")
        continue

    new_trade = {
        "id":                str(uuid.uuid4())[:8],
        "symbol":            sym,
        "company":           known_companies.get(sym, sym),
        "entry_date":        entry_date,
        "entry_time":        entry_time,
        "entry_price":       entry_price,
        "qty":               qty,
        "position_size":     round(entry_price * qty, 2),
        "confidence":        85,
        "earnings_date":     None,
        "sector":            known_sectors.get(sym, 'Unknown'),
        "order_id":          str(o.id),
        "order_status":      "filled",
        "stop_loss_price":   round(entry_price * 0.50, 2),
        "take_profit_price": round(entry_price * 2.00, 2),
        "status":            "open",
        "exit_price":        None,
        "exit_date":         None,
        "pnl":               None,
        "pnl_pct":           None,
        "beat_rate":         None,
        "revenue_growth_yoy": None,
        "momentum_30d":      None,
        "earnings_timing":   None,
    }
    trades.append(new_trade)
    new_trades.append(new_trade)
    print(f"  {sym:<6} {qty} sh @ ${entry_price:.2f}  (position: ${entry_price*qty:.2f})")

# ── Save updated trades_history.json ─────────────────────────────────
with open('data/trades_history.json', 'w') as f:
    json.dump(trades, f, indent=2)
print(f"\nSaved trades_history.json — {len(trades)} total trades")

# ── Sync positions.json from Alpaca live data ─────────────────────────
print("\nSyncing positions from Alpaca...")
live_positions = client.get_all_positions()
account        = client.get_account()

pos_list = []
for p in live_positions:
    pos_list.append({
        "symbol":          p.symbol,
        "qty":             float(p.qty),
        "avg_entry_price": float(p.avg_entry_price),
        "current_price":   float(p.current_price),
        "market_value":    float(p.market_value),
        "unrealized_pl":   float(p.unrealized_pl),
        "unrealized_plpc": float(p.unrealized_plpc),
        "side":            str(p.side),
    })

# Compute daily P&L from today's closed trades
daily_pnl     = sum(t['pnl'] for t in closed_today if t['pnl'] is not None)
total_invested = sum(p['market_value'] for p in pos_list)
daily_pnl_pct  = round(daily_pnl / total_invested * 100, 4) if total_invested else 0

pos_data['positions']            = pos_list
pos_data['last_updated']         = datetime.now().isoformat()
pos_data['daily_pnl']            = round(daily_pnl, 2)
pos_data['daily_pnl_pct']        = daily_pnl_pct
pos_data['open_positions_count'] = len(pos_list)
pos_data['account'] = {
    "portfolio_value": float(account.portfolio_value),
    "cash":            float(account.cash),
    "buying_power":    float(account.buying_power),
    "equity":          float(account.equity),
}

with open('data/positions.json', 'w') as f:
    json.dump(pos_data, f, indent=2)
print(f"Saved positions.json — {len(pos_list)} open positions")

# ── Summary ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SYNC SUMMARY — April 23, 2026")
print("=" * 60)
print(f"\nPositions CLOSED today ({len(closed_today)}):")
total_pnl = 0
for t in sorted(closed_today, key=lambda x: x['pnl'], reverse=True):
    bar = "WIN " if t['pnl'] > 0 else "LOSS"
    print(f"  [{bar}] {t['symbol']:<6}  ${t['pnl']:>+8.2f}  ({t['pnl_pct']:>+.1f}%)")
    total_pnl += t['pnl']

print(f"\n  Day P&L: ${total_pnl:>+.2f}")

print(f"\nNew positions OPENED today ({len(new_trades)}):")
for t in new_trades:
    print(f"  + {t['symbol']:<6}  {t['qty']} sh @ ${t['entry_price']:.2f}  (${t['position_size']:,.2f})")

print(f"\nOpen positions: {len(pos_list)}")
print(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"Cash: ${float(account.cash):,.2f}")
