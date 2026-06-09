import httpx
import asyncio
import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
PAGE_SIZE = 100   # gamma: hard cap 100/page
CLOB_SIZE = 100   # CLOB: use 100 to stay within documented limits


async def fetch_polymarket_markets(limit: int = 50000, on_progress=None) -> List[Dict[str, Any]]:
    """
    Fetch active Polymarket markets.

    Primary:  CLOB API cursor-based pagination — no offset ceiling, gets all markets.
    Fallback: gamma API with 4 parallel sort orders to bypass the 10k offset cap.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            markets = await _fetch_via_clob(client, limit, on_progress)
            if len(markets) >= 10000:
                logger.info(f"Polymarket CLOB: {len(markets)} markets")
                return markets
            logger.info(f"Polymarket CLOB returned {len(markets)} — falling back to gamma multi-sort")
        except Exception as exc:
            logger.warning(f"Polymarket CLOB failed ({exc}), using gamma multi-sort")

        return await _fetch_via_gamma_multisort(client, limit, on_progress)


# ── CLOB cursor-based ─────────────────────────────────────────────────────────

async def _fetch_via_clob(client: httpx.AsyncClient, limit: int, on_progress) -> List[Dict]:
    markets: List[Dict] = []
    next_cursor: Optional[str] = None
    pages = 0

    while len(markets) < limit:
        params: dict = {"limit": CLOB_SIZE}
        if next_cursor:
            params["next_cursor"] = next_cursor

        resp = await client.get(f"{CLOB_API}/markets", params=params, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()

        raw: list = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(raw, list) or not raw:
            break

        next_cursor = (data.get("next_cursor") or "") if isinstance(data, dict) else ""

        for m in raw:
            if m.get("closed") or m.get("archived") or not m.get("active", True):
                continue
            parsed = _parse_clob_market(m)
            if parsed:
                markets.append(parsed)

        pages += 1
        if on_progress:
            on_progress(pages, len(markets))
        logger.info(f"[CLOB pg {pages}] +{len(raw)} raw → {len(markets)} total")

        if not next_cursor or next_cursor in ("LTE=", ""):
            break
        await asyncio.sleep(0.05)

    return markets


def _parse_clob_market(m: dict) -> Optional[Dict]:
    try:
        tokens = m.get("tokens", [])
        yes_tok = next((t for t in tokens if str(t.get("outcome", "")).upper() == "YES"),
                       tokens[0] if tokens else {})
        no_tok  = next((t for t in tokens if str(t.get("outcome", "")).upper() == "NO"),
                       tokens[1] if len(tokens) > 1 else {})
        yes_price = float(yes_tok.get("price", 0.5))
        no_price  = float(no_tok.get("price",  round(1 - yes_price, 4)))
        slug = m.get("market_slug", "")
        volume = 0.0
        try:
            volume = float(m.get("volume", 0) or 0)
        except (TypeError, ValueError):
            pass
        return {
            "id":          f"poly_{m.get('condition_id', m.get('id', ''))}",
            "platform":    "Polymarket",
            "title":       m.get("question", "Unknown"),
            "category":    m.get("category", ""),
            "yesPrice":    yes_price,
            "noPrice":     no_price,
            "bestBid":     yes_price,
            "bestAsk":     yes_price,
            "volume":      volume,
            "lastUpdated": datetime.utcnow().isoformat(),
            "endDate":     m.get("end_date_iso") or m.get("endDate"),
            "marketUrl":   f"https://polymarket.com/event/{slug}" if slug else None,
            "isBinary":    len(tokens) <= 2,
            "outcomeCount":max(len(tokens), 2),
            "contractLabel": "Yes",
        }
    except Exception:
        return None


# ── gamma multi-sort fallback ─────────────────────────────────────────────────

_SORT_CONFIGS = [
    {"order": "volume",    "ascending": False},  # top 10k by volume
    {"order": "volume",    "ascending": True},   # bottom 10k by volume (different markets)
    {"order": "endDate",   "ascending": True},   # soonest expiring 10k
    {"order": "startDate", "ascending": False},  # most recently created 10k
]


async def _fetch_via_gamma_multisort(client: httpx.AsyncClient, limit: int, on_progress) -> List[Dict]:
    per_sort_limit = min(12000, limit)

    async def one_sort(cfg: dict) -> List[Dict]:
        return await _fetch_gamma_single(client, per_sort_limit, cfg)

    batches = await asyncio.gather(*[one_sort(c) for c in _SORT_CONFIGS], return_exceptions=True)

    seen: set = set()
    merged: List[Dict] = []
    pages = 0
    for batch in batches:
        if isinstance(batch, Exception):
            logger.warning(f"gamma sort batch error: {batch}")
            continue
        for m in batch:
            if m["id"] not in seen:
                seen.add(m["id"])
                merged.append(m)
        pages += 1
        if on_progress:
            on_progress(pages * 100, len(merged))

    logger.info(f"Polymarket gamma multi-sort: {len(merged)} unique markets")
    return merged[:limit]


async def _fetch_gamma_single(client: httpx.AsyncClient, limit: int, extra: dict) -> List[Dict]:
    markets: List[Dict] = []
    offset = 0
    max_pages = limit // PAGE_SIZE

    for page in range(max_pages):
        params = {
            "limit":    PAGE_SIZE,
            "offset":   offset,
            "active":   True,
            "closed":   False,
            **extra,
        }
        try:
            resp = await client.get(f"{GAMMA_API}/markets", params=params, timeout=20.0)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            logger.warning(f"gamma page {page} ({extra}) error: {e}")
            break

        if not raw:
            break

        for m in raw:
            parsed = _parse_gamma_market(m)
            if parsed:
                markets.append(parsed)

        offset += PAGE_SIZE
        logger.info(f"[gamma {extra.get('order','?')} asc={extra.get('ascending')}] pg {page+1}: +{len(raw)} → {len(markets)}")

        if len(raw) < PAGE_SIZE:
            break
        if page % 20 == 19:
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0.05)

    return markets


def _parse_gamma_market(m: dict) -> Optional[Dict]:
    try:
        outcome_prices = m.get("outcomePrices", "")
        outcome_labels = m.get("outcomes", "")

        if isinstance(outcome_prices, str) and outcome_prices:
            prices = json.loads(outcome_prices)
            labels = json.loads(outcome_labels) if isinstance(outcome_labels, str) else outcome_labels
        elif isinstance(outcome_prices, list):
            prices = outcome_prices
            labels = outcome_labels if isinstance(outcome_labels, list) else []
        else:
            prices, labels = [], []

        yes_price = float(prices[0]) if prices else 0.5
        no_price  = float(prices[1]) if len(prices) > 1 else round(1 - yes_price, 4)

        outcomes = []
        for i, label in enumerate(labels):
            price = float(prices[i]) if i < len(prices) else 0.5
            outcomes.append({"label": label, "yesPrice": price, "noPrice": round(1 - price, 4), "volume": 0})

        volume = 0.0
        try:
            volume = float(m.get("volume", "0") or 0)
        except (TypeError, ValueError):
            pass

        event_slug = None
        for ev in (m.get("events") or []):
            if isinstance(ev, dict) and ev.get("slug"):
                event_slug = ev["slug"]
                break

        return {
            "id":           f"poly_{m.get('id', '')}",
            "platform":     "Polymarket",
            "title":        m.get("question", m.get("title", "Unknown")),
            "category":     m.get("groupItemTitle", m.get("category", "")),
            "yesPrice":     yes_price,
            "noPrice":      no_price,
            "bestBid":      yes_price,
            "bestAsk":      yes_price,
            "volume":       volume,
            "lastUpdated":  datetime.utcnow().isoformat(),
            "endDate":      m.get("endDate"),
            "marketUrl":    f"https://polymarket.com/event/{event_slug}" if event_slug else None,
            "isBinary":     len(outcomes) <= 2,
            "outcomeCount": max(len(outcomes), 2),
            "contractLabel":labels[0] if labels else "Yes",
            "outcomes":     outcomes if len(outcomes) > 2 else None,
        }
    except Exception:
        return None
