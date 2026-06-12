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


async def fetch_polymarket_markets(limit: Optional[int] = None, on_progress=None) -> List[Dict[str, Any]]:
    """
    Fetch active Polymarket markets. limit=None (default) means UNLIMITED —
    pull every active market the API has.

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


# ── CLOB cursor-based (parallel) ──────────────────────────────────────────────
# CLOB cursors are base64-encoded integer offsets ("MTAwMA==" == "1000",
# "LTE=" == "-1" == end), so pages can be fetched CONCURRENTLY with computed
# cursors instead of waiting for each page to reveal the next. The server also
# serves ~1000 markets/page regardless of smaller limits. Together: ~140
# requests in waves of 8 (~1 min) vs the old 1400 sequential pages (~20 min).

CLOB_CONCURRENCY = 8

async def _fetch_via_clob(client: httpx.AsyncClient, limit: int, on_progress) -> List[Dict]:
    import base64

    def _cursor_for(offset: int) -> str:
        return base64.b64encode(str(offset).encode()).decode()

    async def _get_page(offset: int) -> list:
        params: dict = {"limit": 1000}
        if offset > 0:
            params["next_cursor"] = _cursor_for(offset)
        resp = await client.get(f"{CLOB_API}/markets", params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("data", data) if isinstance(data, dict) else data
        return raw if isinstance(raw, list) else []

    # First page also tells us the server's true page size (stride)
    first = await _get_page(0)
    if not first:
        return []
    stride = len(first)

    markets: List[Dict] = []
    seen_ids: set = set()
    pages = 0

    def _absorb(raw: list) -> None:
        nonlocal pages
        for m in raw:
            if m.get("closed") or m.get("archived") or not m.get("active", True):
                continue
            parsed = _parse_clob_market(m)
            if parsed and parsed["id"] not in seen_ids:
                seen_ids.add(parsed["id"])
                markets.append(parsed)
        pages += 1
        if on_progress:
            on_progress(pages, len(markets))

    _absorb(first)
    logger.info(f"[CLOB] page size {stride}; fetching in waves of {CLOB_CONCURRENCY}...")

    offset = stride
    done = False
    while not done and (limit is None or len(markets) < limit):
        offsets = [offset + i * stride for i in range(CLOB_CONCURRENCY)]
        offset += CLOB_CONCURRENCY * stride
        results = await asyncio.gather(*[_get_page(o) for o in offsets],
                                       return_exceptions=True)
        for r in results:  # in offset order — stop at the first short/empty page
            if isinstance(r, Exception):
                logger.warning(f"[CLOB] page fetch error ({r}); stopping pagination")
                done = True
                break
            if not r:
                done = True
                break
            _absorb(r)
            if len(r) < stride:
                done = True
                break
        logger.info(f"[CLOB] {pages} pages → {len(markets)} active markets")
        await asyncio.sleep(0.1)

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


async def _fetch_via_gamma_multisort(client: httpx.AsyncClient, limit: Optional[int], on_progress) -> List[Dict]:
    per_sort_limit = min(12000, limit) if limit else 12000

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
