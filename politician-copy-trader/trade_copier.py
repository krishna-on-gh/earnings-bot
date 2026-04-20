"""
Copies politician trades to Alpaca paper account.

For stock trades: market buy/sell the same ticker.
For option trades: find the nearest ATM contract (same expiry week) and place
                   a buy_to_open or sell_to_close order.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetOptionContractsRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    ContractType,
    PositionIntent,
)
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest

import config

logger = logging.getLogger(__name__)


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


class TradeCopier:
    def __init__(self):
        self.client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=True,
        )
        self.seen: set[str] = set(
            _load_json(config.SEEN_TRADES_FILE, [])
        )

    def _save_seen(self):
        _save_json(config.SEEN_TRADES_FILE, list(self.seen))

    def get_account(self):
        return self.client.get_account()

    def _deployed_capital(self) -> float:
        """Sum of market value of all open stock positions we copied."""
        try:
            positions = self.client.get_all_positions()
            return sum(float(p.market_value) for p in positions if float(p.market_value) > 0)
        except Exception:
            return 0.0

    def process_trades(self, trades: list[dict]) -> list[dict]:
        """
        Given a list of politician trade dicts, copy any new ones to Alpaca.
        Returns list of orders placed.
        """
        orders_placed = []
        for trade in trades:
            trade_id = trade.get("id")
            if not trade_id or trade_id in self.seen:
                continue

            # Enforce $50k capital cap before any buy
            if trade.get("trade_type") == "buy":
                deployed = self._deployed_capital()
                if deployed >= config.TRADE_CAPITAL_LIMIT_USD:
                    logger.warning(
                        f"Capital cap reached (${deployed:,.0f} / "
                        f"${config.TRADE_CAPITAL_LIMIT_USD:,.0f}) — "
                        f"skipping {trade['ticker']} buy"
                    )
                    self.seen.add(trade_id)  # mark seen so we don't retry
                    self._save_seen()
                    continue

            logger.info(
                f"New trade from {trade['politician']}: "
                f"{trade['trade_type'].upper()} {trade['ticker']} "
                f"({trade['asset_type']}) filed {trade.get('filed_date')}"
            )

            order = self._copy_trade(trade)
            if order:
                orders_placed.append(order)
                self.seen.add(trade_id)
                self._save_seen()

        return orders_placed

    def _copy_trade(self, trade: dict) -> Optional[dict]:
        ticker = trade["ticker"]
        trade_type = trade["trade_type"]
        asset_type = trade["asset_type"]

        try:
            if asset_type == "option":
                return self._copy_option_trade(trade)
            else:
                return self._copy_stock_trade(ticker, trade_type, trade)
        except Exception as e:
            logger.error(f"Failed to copy trade {trade.get('id')}: {e}")
            return None

    def _copy_stock_trade(self, ticker: str, trade_type: str, trade: dict) -> Optional[dict]:
        account = self.client.get_account()
        buying_power = float(account.buying_power)
        alloc = min(config.TRADE_ALLOCATION_USD, buying_power * 0.05)

        if alloc < 1:
            logger.warning("Insufficient buying power, skipping stock trade")
            return None

        side = OrderSide.BUY if trade_type == "buy" else OrderSide.SELL

        # For sells, check if we hold the position first
        if side == OrderSide.SELL:
            try:
                pos = self.client.get_open_position(ticker)
                qty = float(pos.qty)
            except Exception:
                logger.info(f"No position in {ticker} to sell, skipping")
                return None
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        else:
            # Buy: use notional allocation
            req = MarketOrderRequest(
                symbol=ticker,
                notional=round(alloc, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )

        order = self.client.submit_order(req)
        result = {
            "order_id": str(order.id),
            "ticker": ticker,
            "asset_type": "stock",
            "side": trade_type,
            "notional": alloc,
            "status": str(order.status),
            "copied_from": trade["politician"],
            "trade_id": trade.get("id"),
            "timestamp": datetime.now().isoformat(),
        }
        logger.info(f"Stock order placed: {result}")
        return result

    def _copy_option_trade(self, trade: dict) -> Optional[dict]:
        ticker = trade["ticker"]
        trade_type = trade["trade_type"]

        # Find a suitable near-term ATM contract
        contract = self._find_atm_option(ticker, trade_type)
        if not contract:
            logger.warning(f"Could not find ATM option for {ticker}, falling back to stock")
            return self._copy_stock_trade(ticker, trade_type, trade)

        symbol = contract["symbol"]
        account = self.client.get_account()
        buying_power = float(account.buying_power)
        alloc = min(config.TRADE_ALLOCATION_USD, buying_power * 0.05)

        # Estimate qty from mid price
        mid = contract.get("mid_price", 1.0)
        if mid <= 0:
            mid = 1.0
        qty = max(1, int(alloc / (mid * 100)))  # 1 contract = 100 shares

        if trade_type == "buy":
            intent = PositionIntent.BUY_TO_OPEN
            side = OrderSide.BUY
        else:
            # Check we hold it first
            try:
                self.client.get_open_position(symbol)
                intent = PositionIntent.SELL_TO_CLOSE
                side = OrderSide.SELL
            except Exception:
                logger.info(f"No option position in {symbol} to sell, skipping")
                return None

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )

        order = self.client.submit_order(req)
        result = {
            "order_id": str(order.id),
            "ticker": ticker,
            "option_symbol": symbol,
            "asset_type": "option",
            "side": trade_type,
            "qty": qty,
            "mid_price": mid,
            "status": str(order.status),
            "copied_from": trade["politician"],
            "trade_id": trade.get("id"),
            "timestamp": datetime.now().isoformat(),
        }
        logger.info(f"Option order placed: {result}")
        return result

    def _find_atm_option(self, ticker: str, trade_type: str) -> Optional[dict]:
        """Find nearest ATM call (for buys) or put (for sells) expiring in ~30 days."""
        try:
            # Get current stock price
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest

            data_client = StockHistoricalDataClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
            )
            trade_data = data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=ticker)
            )
            current_price = float(trade_data[ticker].price)
        except Exception as e:
            logger.warning(f"Could not get price for {ticker}: {e}")
            return None

        # Target expiry ~30 days out
        target_expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        expiry_end = (datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d")

        # We use calls for buys (politician bullish), puts for sells (politician bearish)
        contract_type = ContractType.CALL if trade_type == "buy" else ContractType.PUT

        try:
            contracts_req = GetOptionContractsRequest(
                underlying_symbols=[ticker],
                expiration_date_gte=target_expiry,
                expiration_date_lte=expiry_end,
                strike_price_gte=str(round(current_price * 0.90, 2)),
                strike_price_lte=str(round(current_price * 1.10, 2)),
                type=contract_type,
                limit=50,
            )
            contracts = self.client.get_option_contracts(contracts_req)
        except Exception as e:
            logger.warning(f"Option contract lookup failed for {ticker}: {e}")
            return None

        if not contracts or not contracts.option_contracts:
            logger.warning(f"No option contracts found for {ticker}")
            return None

        # Pick the contract with strike closest to ATM
        best = min(
            contracts.option_contracts,
            key=lambda c: abs(float(c.strike_price) - current_price),
        )

        # Try to get mid price from snapshot
        mid_price = 1.0
        try:
            opt_client = OptionHistoricalDataClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
            )
            snap = opt_client.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=best.symbol)
            )
            if best.symbol in snap:
                q = snap[best.symbol].latest_quote
                if q and q.ask_price and q.bid_price:
                    mid_price = (float(q.ask_price) + float(q.bid_price)) / 2
        except Exception:
            pass

        return {"symbol": best.symbol, "mid_price": mid_price, "strike": float(best.strike_price)}

    def get_positions(self) -> list:
        return self.client.get_all_positions()

    def get_portfolio_value(self) -> float:
        acct = self.client.get_account()
        return float(acct.portfolio_value)
