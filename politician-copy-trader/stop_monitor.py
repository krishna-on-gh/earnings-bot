"""
Trailing stop monitor for stock positions opened by the copy trader.

For every stock position in the bot's trade log, this module:
  1. Checks the current market price via Alpaca
  2. Tracks the peak price seen since entry
  3. If price drops more than TRAILING_STOP_PCT from peak → places a market sell

Peak prices are persisted to data/peak_prices.json so restarts don't reset the trail.
Options are ignored here (no trailing stops on options per user request).
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

import config

logger = logging.getLogger(__name__)

PEAK_PRICES_FILE = os.path.join(config.DATA_DIR, "peak_prices.json")


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _get_copied_tickers() -> set[str]:
    """Return tickers of stock positions we opened (from trade log)."""
    log = _load_json(config.TRADE_LOG_FILE, [])
    return {
        entry["ticker"]
        for entry in log
        if entry.get("asset_type") == "stock" and entry.get("side") == "buy"
    }


class StopMonitor:
    def __init__(self):
        self.trading = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=True,
        )
        self.data = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
        self.peak_prices: dict[str, float] = _load_json(PEAK_PRICES_FILE, {})

    def _save_peaks(self):
        _save_json(PEAK_PRICES_FILE, self.peak_prices)

    def _current_price(self, ticker: str) -> Optional[float]:
        try:
            result = self.data.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=ticker)
            )
            return float(result[ticker].price)
        except Exception as e:
            logger.warning(f"Could not get price for {ticker}: {e}")
            return None

    def _get_open_positions(self) -> dict[str, object]:
        """Return dict of ticker → position for all open positions."""
        try:
            positions = self.trading.get_all_positions()
            return {p.symbol: p for p in positions}
        except Exception as e:
            logger.error(f"Could not fetch positions: {e}")
            return {}

    def _sell_position(self, ticker: str, qty: float) -> bool:
        try:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self.trading.submit_order(req)
            logger.info(f"Trailing stop triggered — sold {qty} {ticker} (order {order.id})")
            return True
        except Exception as e:
            logger.error(f"Failed to place sell order for {ticker}: {e}")
            return False

    def run(self):
        """
        Check all open stock positions we copied.
        Update peak prices and trigger sells if trail breached.
        """
        copied_tickers = _get_copied_tickers()
        if not copied_tickers:
            logger.info("No copied stock positions to monitor")
            return

        open_positions = self._get_open_positions()
        # Only monitor positions we actually opened
        to_monitor = {t: p for t, p in open_positions.items() if t in copied_tickers}

        if not to_monitor:
            logger.info("No open copied positions currently held")
            # Clean up stale peak prices
            for ticker in list(self.peak_prices.keys()):
                if ticker not in open_positions:
                    del self.peak_prices[ticker]
            self._save_peaks()
            return

        logger.info(f"Monitoring {len(to_monitor)} positions for trailing stops ({config.TRAILING_STOP_PCT*100:.0f}% trail)")

        peaks_updated = False
        for ticker, position in to_monitor.items():
            price = self._current_price(ticker)
            if price is None:
                continue

            qty = float(position.qty)
            entry_price = float(position.avg_entry_price)
            unrealized_pct = float(position.unrealized_plpc) * 100

            # Update peak
            prev_peak = self.peak_prices.get(ticker, entry_price)
            if price > prev_peak:
                self.peak_prices[ticker] = price
                peaks_updated = True
                logger.info(f"{ticker}: new peak ${price:.2f} (was ${prev_peak:.2f})")

            current_peak = self.peak_prices.get(ticker, entry_price)
            trail_trigger = current_peak * (1 - config.TRAILING_STOP_PCT)

            logger.info(
                f"{ticker}: price=${price:.2f}  peak=${current_peak:.2f}  "
                f"trigger=${trail_trigger:.2f}  P&L={unrealized_pct:+.1f}%"
            )

            if price <= trail_trigger:
                logger.warning(
                    f"TRAILING STOP HIT — {ticker}: price ${price:.2f} <= "
                    f"trigger ${trail_trigger:.2f} ({config.TRAILING_STOP_PCT*100:.0f}% from peak ${current_peak:.2f})"
                )
                sold = self._sell_position(ticker, qty)
                if sold:
                    # Log the stop-out
                    _append_stop_log(ticker, entry_price, current_peak, price, qty)
                    del self.peak_prices[ticker]
                    peaks_updated = True

        if peaks_updated:
            self._save_peaks()

    def get_summary(self) -> list[dict]:
        """Return a list of dicts with current trail status for all monitored positions."""
        copied_tickers = _get_copied_tickers()
        open_positions = self._get_open_positions()
        summary = []

        for ticker, position in open_positions.items():
            if ticker not in copied_tickers:
                continue
            price = self._current_price(ticker) or float(position.current_price or 0)
            entry = float(position.avg_entry_price)
            peak = self.peak_prices.get(ticker, entry)
            trail_trigger = peak * (1 - config.TRAILING_STOP_PCT)
            pct_from_trigger = ((price - trail_trigger) / trail_trigger) * 100

            summary.append({
                "ticker": ticker,
                "qty": float(position.qty),
                "entry_price": entry,
                "current_price": price,
                "peak_price": peak,
                "trail_trigger": round(trail_trigger, 2),
                "pct_above_trigger": round(pct_from_trigger, 2),
                "unrealized_pl": float(position.unrealized_pl),
                "unrealized_plpc": round(float(position.unrealized_plpc) * 100, 2),
            })

        return summary


def _append_stop_log(ticker: str, entry: float, peak: float, exit_price: float, qty: float):
    stop_log_file = os.path.join(config.DATA_DIR, "stop_log.json")
    log = _load_json(stop_log_file, [])
    gain_pct = ((exit_price - entry) / entry) * 100
    log.append({
        "ticker": ticker,
        "qty": qty,
        "entry_price": entry,
        "peak_price": peak,
        "exit_price": exit_price,
        "gain_pct": round(gain_pct, 2),
        "trail_pct": config.TRAILING_STOP_PCT * 100,
        "timestamp": datetime.now().isoformat(),
    })
    _save_json(stop_log_file, log)
    logger.info(
        f"Stop log: {ticker} entry=${entry:.2f} peak=${peak:.2f} "
        f"exit=${exit_price:.2f} gain={gain_pct:+.1f}%"
    )
