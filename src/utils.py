"""
Shared utilities: Alpaca client, logging, email, JSON I/O, safety checks.
"""
import os
import json
import logging
import smtplib
import sys
from pathlib import Path
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.data import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
KILL_SWITCH = BASE_DIR / "STOP_TRADING"

ET = ZoneInfo("America/New_York")


def get_logger(name: str) -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    fh = logging.FileHandler(LOGS_DIR / f"{name}.log")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def load_settings() -> Dict[str, Any]:
    with open(CONFIG_DIR / "settings.json") as f:
        settings = json.load(f)
    weights_path = DATA_DIR / "learning_weights.json"
    if weights_path.exists():
        with open(weights_path) as f:
            weights = json.load(f)
        settings["scoring"].update({
            k: v for k, v in weights.items()
            if k in settings["scoring"] and isinstance(v, (int, float))
        })
    return settings


def load_json(filename: str) -> Any:
    path = DATA_DIR / filename
    if not path.exists():
        return [] if filename != "positions.json" else {}
    with open(path) as f:
        return json.load(f)


def save_json(filename: str, data: Any) -> None:
    path = DATA_DIR / filename
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)


def _load_dotenv() -> None:
    """Load .env file into os.environ if present (no extra packages needed)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()


def get_alpaca_keys() -> tuple[str, str]:
    """Read keys from .env / env vars (GitHub Actions) or fall back to settings.json."""
    s = load_settings()
    api_key    = os.environ.get("ALPACA_API_KEY")    or s["alpaca"]["api_key"]
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or s["alpaca"]["secret_key"]
    return api_key, secret_key


def get_trading_client() -> TradingClient:
    api_key, secret_key = get_alpaca_keys()
    return TradingClient(api_key=api_key, secret_key=secret_key, paper=True)


def get_data_client() -> StockHistoricalDataClient:
    api_key, secret_key = get_alpaca_keys()
    return StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def get_option_data_client() -> OptionHistoricalDataClient:
    api_key, secret_key = get_alpaca_keys()
    return OptionHistoricalDataClient(api_key=api_key, secret_key=secret_key)


def now_et() -> datetime:
    return datetime.now(ET)


def today_et() -> date:
    return now_et().date()


def send_email(subject: str, body: str, html: Optional[str] = None) -> bool:
    settings = load_settings()
    cfg = settings.get("email", {})
    if not cfg.get("enabled"):
        return False
    log = get_logger("email")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[EarningsBot] {subject}"
        msg["From"] = cfg["username"]
        msg["To"] = cfg["recipient"]
        msg.attach(MIMEText(body, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False


def check_kill_switch() -> bool:
    """Returns True if trading should stop."""
    return KILL_SWITCH.exists()


def check_circuit_breakers(log: logging.Logger) -> Optional[str]:
    """Returns reason string if circuit breaker tripped, else None."""
    settings = load_settings()
    thresholds = settings["thresholds"]
    pos_data = load_json("positions.json")
    if isinstance(pos_data, dict):
        daily_pnl_pct = pos_data.get("daily_pnl_pct", 0)
        weekly_pnl_pct = pos_data.get("weekly_pnl_pct", 0)
        if daily_pnl_pct <= -thresholds["circuit_breaker_daily_loss_pct"]:
            reason = f"Daily loss {daily_pnl_pct:.1f}% exceeds -{thresholds['circuit_breaker_daily_loss_pct']}% limit"
            log.warning(f"CIRCUIT BREAKER: {reason}")
            return reason
        if weekly_pnl_pct <= -thresholds["circuit_breaker_weekly_loss_pct"]:
            reason = f"Weekly loss {weekly_pnl_pct:.1f}% exceeds -{thresholds['circuit_breaker_weekly_loss_pct']}% limit"
            log.warning(f"CIRCUIT BREAKER: {reason}")
            return reason
    return None


def check_budget(amount: float, log: logging.Logger) -> tuple[bool, str]:
    """Returns (can_trade, reason). Checks both caps."""
    budget = load_json("budget_tracker.json")
    if budget.get("halted"):
        return False, f"Trading halted: {budget.get('halt_reason', 'unknown')}"
    settings = load_settings()
    two_week_end = date.fromisoformat(settings["budget"]["two_week_end"])
    in_initial_period = today_et() <= two_week_end
    if in_initial_period:
        remaining = budget["two_week_cap"] - budget["two_week_deployed"]
        if amount > remaining:
            return False, f"2-week budget exhausted: ${remaining:.0f} remaining of ${budget['two_week_cap']}"
    remaining_total = budget["total_cap"] - budget["total_deployed"]
    if amount > remaining_total:
        return False, f"Total budget exhausted: ${remaining_total:.0f} remaining of ${budget['total_cap']}"
    return True, "ok"


def update_budget(amount: float) -> None:
    budget = load_json("budget_tracker.json")
    settings = load_settings()
    two_week_end = date.fromisoformat(settings["budget"]["two_week_end"])
    budget["total_deployed"] += amount
    budget["available_total"] = budget["total_cap"] - budget["total_deployed"]
    if today_et() <= two_week_end:
        budget["two_week_deployed"] += amount
        budget["available_two_week"] = budget["two_week_cap"] - budget["two_week_deployed"]
    budget["trades_count"] = budget.get("trades_count", 0) + 1
    budget["last_updated"] = now_et().isoformat()
    save_json("budget_tracker.json", budget)


def halt_trading(reason: str, log: logging.Logger) -> None:
    budget = load_json("budget_tracker.json")
    budget["halted"] = True
    budget["halt_reason"] = reason
    budget["last_updated"] = now_et().isoformat()
    save_json("budget_tracker.json", budget)
    log.error(f"TRADING HALTED: {reason}")
    send_email("TRADING HALTED", f"Trading has been halted.\nReason: {reason}")


def is_market_open() -> bool:
    try:
        client = get_trading_client()
        clock = client.get_clock()
        return clock.is_open
    except Exception:
        return False


def is_weekday() -> bool:
    return today_et().weekday() < 5
