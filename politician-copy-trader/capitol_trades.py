"""
Scrapes www.capitoltrades.com for recent politician trades.
Returns normalized trade dicts ready for scoring and copying.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url: str, params: dict = None, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.warning(f"GET {url} attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return None


def _parse_amount(raw: str) -> int:
    """Convert Capitol Trades amount range string to midpoint integer.
    Handles formats like '100K–250K', '$1,001 - $15,000', 'Undisclosed'.
    """
    raw = raw.strip().replace(",", "").replace("$", "").replace("\u2013", "-").replace("\u2014", "-")

    def _to_int(s: str) -> int:
        s = s.strip().upper()
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))

    if "-" in raw:
        parts = raw.split("-", 1)
        try:
            return (_to_int(parts[0]) + _to_int(parts[1])) // 2
        except (ValueError, IndexError):
            pass
    try:
        return _to_int(raw)
    except ValueError:
        return 0


def _parse_date(raw: str) -> Optional[str]:
    """Parse dates including Capitol Trades relative formats."""
    raw = raw.strip()
    now = datetime.now()

    # Relative: "Today", "Yesterday"
    if "today" in raw.lower():
        return now.strftime("%Y-%m-%d")
    if "yesterday" in raw.lower():
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Strip time prefix like "13:02Yesterday" → "Yesterday"
    import re
    raw = re.sub(r"^\d{1,2}:\d{2}", "", raw).strip()
    if "today" in raw.lower():
        return now.strftime("%Y-%m-%d")
    if "yesterday" in raw.lower():
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # "7 Apr2026" → "7 Apr 2026"
    raw = re.sub(r"([A-Za-z])(\d)", r"\1 \2", raw)
    raw = re.sub(r"(\d)([A-Za-z])", r"\1 \2", raw)

    for fmt in ("%d %b %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fetch_recent_trades(pages: int = 5) -> list[dict]:
    """
    Fetch recent trades from Capitol Trades /trades page.
    Returns a list of trade dicts.
    """
    trades = []
    cutoff = datetime.now() - timedelta(days=config.TRADE_LOOKBACK_DAYS)

    for page in range(1, pages + 1):
        url = f"{config.CAPITOL_TRADES_BASE}/trades"
        soup = _get(url, params={"page": page, "pageSize": 100})
        if not soup:
            logger.error(f"Failed to fetch trades page {page}")
            break

        rows = soup.select("table tbody tr")
        if not rows:
            # Try alternative card-based layout
            rows = soup.select("[data-trade-id], .trade-row, .q-table__row")

        if not rows:
            logger.warning(f"No trade rows found on page {page}, stopping pagination")
            break

        page_trades = []
        stop_early = False

        for row in rows:
            trade = _parse_trade_row(row)
            if not trade:
                continue

            trade_date = trade.get("filed_date") or trade.get("trade_date")
            if trade_date:
                try:
                    dt = datetime.strptime(trade_date, "%Y-%m-%d")
                    if dt < cutoff:
                        stop_early = True
                        break
                except ValueError:
                    pass

            page_trades.append(trade)

        trades.extend(page_trades)
        logger.info(f"Page {page}: fetched {len(page_trades)} trades (total {len(trades)})")

        if stop_early:
            logger.info("Reached trades older than lookback window, stopping")
            break

        time.sleep(1.5)  # polite delay

    logger.info(f"Total recent trades fetched: {len(trades)}")
    return trades


def _parse_trade_row(row) -> Optional[dict]:
    """Parse a Capitol Trades table row using exact CSS selectors from live HTML.

    Cell layout (confirmed from live scrape):
      0: politician cell — name in <a href="/politicians/ID">
      1: issuer cell    — ticker in <span class="q-field issuer-ticker">VZ:US</span>
      2: filed date     — two divs: time + "Yesterday"/"Today"/date
      3: trade date     — two divs: "7 Apr" + "2026"
      4: reporting gap
      5: owner/asset type
      6: trade type     — <span class="q-field tx-type tx-type--sell">sell</span>
      7: amount         — <span class="...">100K–250K</span>
      8: price
      9: detail link
    """
    try:
        cells = row.find_all("td")
        if len(cells) < 7:
            return None

        # --- Politician ---
        pol_link = cells[0].select_one("a[href*='/politicians/']")
        if not pol_link:
            return None
        politician_name = pol_link.get_text(" ", strip=True)
        politician_name = " ".join(politician_name.split())  # collapse whitespace
        politician_id_match = pol_link.get("href", "").split("/politicians/")
        politician_id = politician_id_match[-1].strip("/") if len(politician_id_match) > 1 else _slugify(politician_name)

        # --- Ticker ---
        ticker_span = cells[1].select_one(".issuer-ticker, [class*='issuer-ticker']")
        if ticker_span:
            raw_ticker = ticker_span.get_text(strip=True)
            ticker = raw_ticker.split(":")[0].strip().upper()
        else:
            return None

        if not ticker or ticker in ("N/A", "NA", ""):
            return None

        # --- Filed date (cell 2) ---
        # Two sub-divs: time ("13:02") and label ("Yesterday" / "17 Apr" / "Today")
        filed_divs = cells[2].find_all("div", recursive=True)
        filed_texts = [d.get_text(strip=True) for d in filed_divs if d.get_text(strip=True)]
        filed_date = None
        for ft in filed_texts:
            d = _parse_date(ft)
            if d:
                filed_date = d
                break

        # --- Trade date (cell 3): "7 Apr" + "2026" in two sibling divs ---
        trade_divs = cells[3].find_all("div", recursive=True)
        trade_texts = [d.get_text(strip=True) for d in trade_divs if d.get_text(strip=True)]
        trade_date = None
        # Combine adjacent short pieces like ["7 Apr", "2026"]
        combined = " ".join(trade_texts)
        trade_date = _parse_date(combined)
        if not trade_date:
            for tt in trade_texts:
                d = _parse_date(tt)
                if d:
                    trade_date = d
                    break

        # --- Asset type (cell 5) ---
        asset_text = cells[5].get_text(" ", strip=True).lower()
        if any(k in asset_text for k in ["option", "call", "put"]):
            asset_type = "option"
        else:
            asset_type = "stock"

        # --- Trade type (cell 6) ---
        tx_span = cells[6].select_one("[class*='tx-type']")
        trade_raw = (tx_span.get_text(strip=True) if tx_span else cells[6].get_text(strip=True)).lower()
        if "buy" in trade_raw or "purchase" in trade_raw:
            trade_type = "buy"
        elif "sell" in trade_raw or "sale" in trade_raw:
            trade_type = "sell"
        else:
            return None

        # --- Amount (cell 7) ---
        # Text is deep inside SVG-heavy HTML; grab the last visible text node
        amount_span = cells[7].select_one(".text-txt-dimmer, [class*='trade-size'] span")
        amount_text = amount_span.get_text(strip=True) if amount_span else cells[7].get_text(strip=True)
        amount_usd = _parse_amount(amount_text)

        return {
            "id": f"{politician_id}_{ticker}_{filed_date}_{trade_type}",
            "politician": politician_name,
            "politician_id": politician_id,
            "ticker": ticker,
            "asset_type": asset_type,
            "trade_type": trade_type,
            "filed_date": filed_date,
            "trade_date": trade_date,
            "amount_usd": amount_usd,
        }
    except Exception as e:
        logger.debug(f"Row parse error: {e}")
        return None


def _extract_politician_name(cell) -> Optional[str]:
    link = cell.find("a")
    if link:
        return link.get_text(strip=True)
    text = cell.get_text(strip=True)
    return text if text else None


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace(".", "").replace(",", "")


def fetch_politician_trades(politician_id: str, pages: int = 3) -> list[dict]:
    """Fetch all trades for a specific politician by their slug/id."""
    trades = []
    for page in range(1, pages + 1):
        url = f"{config.CAPITOL_TRADES_BASE}/politicians/{politician_id}/trades"
        soup = _get(url, params={"page": page})
        if not soup:
            break
        rows = soup.select("table tbody tr")
        for row in rows:
            trade = _parse_trade_row(row)
            if trade:
                trade["politician_id"] = politician_id
                trades.append(trade)
        time.sleep(1.0)
    return trades


def fetch_all_politicians() -> list[dict]:
    """Fetch the list of politicians from Capitol Trades."""
    politicians = []
    url = f"{config.CAPITOL_TRADES_BASE}/politicians"
    soup = _get(url)
    if not soup:
        return politicians

    for link in soup.select("a[href*='/politicians/']"):
        href = link.get("href", "")
        parts = [p for p in href.split("/") if p]
        if len(parts) >= 2 and parts[-1] != "politicians":
            slug = parts[-1]
            name = link.get_text(strip=True)
            if name and slug not in ("trades", "politicians"):
                politicians.append({"id": slug, "name": name})

    seen = set()
    unique = []
    for p in politicians:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    logger.info(f"Found {len(unique)} politicians")
    return unique
