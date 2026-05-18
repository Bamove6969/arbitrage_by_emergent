import httpx
import asyncio
import logging
import json
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100   # Gamma API hard-caps at 100 per page; higher values silently truncate
MAX_PAGES = 300   # 100 * 300 = 30k market ceiling


async def fetch_polymarket_markets(limit: int = 50000, on_progress: callable = None) -> List[Dict[str, Any]]:
    markets = []
    offset = 0
    pages_fetched = 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            while len(markets) < limit and pages_fetched < MAX_PAGES:
                params = {
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "active": True,
                    "closed": False,
                    "order": "volume",
                    "ascending": False,
                }
                resp = await client.get(f"{POLYMARKET_GAMMA_API}/markets", params=params)
                resp.raise_for_status()
                raw_markets = resp.json()

                if not raw_markets:
                    break

                for m in raw_markets:
                    try:
                        outcomes = []
                        outcome_prices = m.get("outcomePrices", "")
                        outcome_labels = m.get("outcomes", "")

                        if isinstance(outcome_prices, str) and outcome_prices:
                            prices = json.loads(outcome_prices)
                            labels = json.loads(outcome_labels) if isinstance(outcome_labels, str) else outcome_labels
                        elif isinstance(outcome_prices, list):
                            prices = outcome_prices
                            labels = outcome_labels if isinstance(outcome_labels, list) else []
                        else:
                            prices = []
                            labels = []

                        yes_price = float(prices[0]) if len(prices) > 0 else 0.5
                        no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price

                        for i, label in enumerate(labels):
                            price = float(prices[i]) if i < len(prices) else 0.5
                            outcomes.append({
                                "label": label,
                                "yesPrice": price,
                                "noPrice": 1.0 - price,
                                "volume": 0,
                            })

                        volume_str = m.get("volume", "0")
                        try:
                            volume = float(volume_str) if volume_str else 0
                        except (ValueError, TypeError):
                            volume = 0

                        event_slug = None
                        events_list = m.get("events", [])
                        if events_list and isinstance(events_list, list):
                            for ev in events_list:
                                if isinstance(ev, dict) and ev.get("slug"):
                                    event_slug = ev["slug"]
                                    break
                        market_url = f"https://polymarket.com/event/{event_slug}" if event_slug else None

                        market = {
                            "id": f"poly_{m.get('id', '')}",
                            "platform": "Polymarket",
                            "title": m.get("question", m.get("title", "Unknown")),
                            "category": m.get("groupItemTitle", m.get("category", "")),
                            "yesPrice": yes_price,
                            "noPrice": no_price,
                            "volume": volume,
                            "lastUpdated": datetime.utcnow().isoformat(),
                            "endDate": m.get("endDate", None),
                            "marketUrl": market_url,
                            "isBinary": len(outcomes) <= 2,
                            "outcomeCount": max(len(outcomes), 2),
                            "contractLabel": labels[0] if labels and len(labels) > 0 else "Yes",
                            "outcomes": outcomes if len(outcomes) > 2 else None,
                        }
                        markets.append(market)
                    except Exception as e:
                        logger.warning(f"Skipping Polymarket market: {e}")
                        continue

                pages_fetched += 1
                offset += PAGE_SIZE
                
                # Report progress
                if on_progress:
                    on_progress(pages_fetched, len(markets))

                logger.info(f"Polymarket page {pages_fetched}: fetched {len(raw_markets)} markets (total: {len(markets)})")

                if len(raw_markets) < PAGE_SIZE:
                    break

                if pages_fetched % 20 == 0:
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(0.05)

    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")

    logger.info(f"Polymarket total: {len(markets)} markets from {pages_fetched} pages")
    return markets
