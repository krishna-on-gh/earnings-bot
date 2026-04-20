import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

CAPITOL_TRADES_BASE = "https://www.capitoltrades.com"

# How many top politicians to follow
TOP_N_POLITICIANS = 5

# Hard cap on total capital this bot is allowed to deploy (stocks only)
TRADE_CAPITAL_LIMIT_USD = 50_000.0

# Dollar amount to allocate per copied trade
TRADE_ALLOCATION_USD = 500.0

# Trailing stop percentage for stock positions (e.g. 0.07 = 7% trail)
TRAILING_STOP_PCT = 0.07

# Only consider trades filed within this many days
TRADE_LOOKBACK_DAYS = 30

# Score politicians based on trades in the last N days
SCORING_WINDOW_DAYS = 90

# Minimum trades in scoring window to be considered active
MIN_TRADES_FOR_CONSIDERATION = 3

# How often the scheduler polls for new trades (minutes)
POLL_INTERVAL_MINUTES = 30

# State file paths
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
COPIED_TRADES_FILE = os.path.join(DATA_DIR, "copied_trades.json")
POLITICIAN_SCORES_FILE = os.path.join(DATA_DIR, "politician_scores.json")
TRADE_LOG_FILE = os.path.join(DATA_DIR, "trade_log.json")
SEEN_TRADES_FILE = os.path.join(DATA_DIR, "seen_trades.json")
