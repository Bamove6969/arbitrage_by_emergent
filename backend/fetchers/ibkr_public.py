"""
IBKR ForecastEx public-data fetcher.

Tiered pricing:
  1. TWS/IB Gateway live pricing (backend/fetchers/ibkr.py) when a gateway is
     reachable at IB_GATEWAY_URL — the "universal container" 2FA flow.
  2. Public-data fallback (this module): REST contract discovery from the
     forecasttrader API + prices from ForecastEx's public S3 bucket
     (intraday pairs CSV, refreshed every 10 min, overlaid on daily closes).

IBKR_MODE env: auto (default) | tws | public | off
"""
import asyncio
import csv
import io
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

FORECAST_BASE = "https://forecasttrader.interactivebrokers.com/tws.proxy/public/forecasttrader"
DATA_BUCKET = "https://forecastex-public-data.s3.amazonaws.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
DISCOVERY_CONCURRENCY = 15


# ── discovery ────────────────────────────────────────────────────────────────

async def _fetch_underlyings(client: httpx.AsyncClient) -> List[Dict]:
    resp = await client.get(f"{FORECAST_BASE}/category/tree", headers=HEADERS)
    resp.raise_for_status()
    underlyings: List[Dict] = []

    def walk(cat: dict):
        for m in cat.get("markets", []):
            if m.get("conid"):
                underlyings.append(m)
        for sub in cat.get("subCategories", {}).values():
            walk(sub)

    for cat in resp.json().get("categories", {}).values():
        walk(cat)
    return underlyings


async def _fetch_contracts(client: httpx.AsyncClient, underlying: Dict) -> List[Dict]:
    try:
        resp = await client.get(
            f"{FORECAST_BASE}/contract/market",
            params={"underlyingConid": str(underlying["conid"]), "exchange": "FORECASTX"},
            headers=HEADERS,
        )
        resp.raise_for_status()
        contracts = resp.json().get("contracts", [])
        for c in contracts:
            c["event_name"] = underlying.get("name", "Unknown")
            c["event_symbol"] = underlying.get("symbol", "")
        return contracts
    except Exception as e:
        logger.debug(f"IBKR public: contracts for {underlying.get('conid')} failed: {e}")
        return []


# ── public price files ───────────────────────────────────────────────────────

async def _load_price_index(client: httpx.AsyncClient) -> Tuple[Dict[str, Dict[str, float]], Optional[str]]:
    """Returns ({event_contract: {"YES": p, "NO": p}}, price_date)."""
    index: Dict[str, Dict[str, float]] = {}
    price_date = None

    today = datetime.now(timezone.utc).date()
    for delta in range(0, 8):
        d = (today - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            resp = await client.get(f"{DATA_BUCKET}/prices/daily_prices_{d}.csv")
            if resp.status_code != 200:
                continue
            for row in csv.DictReader(io.StringIO(resp.text)):
                key = row.get("event_contract", "")
                side = row.get("subtype", "")
                try:
                    price = float(row.get("end_price") or 0)
                except ValueError:
                    continue
                if key and side and 0 < price < 1:
                    index.setdefault(key, {})[side] = price
            price_date = d
            logger.info(f"IBKR public: daily prices {d} — {len(index)} contracts")
            break
        except Exception as e:
            logger.debug(f"IBKR public: daily prices {d} failed: {e}")

    # Overlay intraday pair trades (fresher, refreshed every ~10 min)
    for delta in range(0, 3):
        d = (today - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            resp = await client.get(f"{DATA_BUCKET}/pairs/pairs_{d}.csv")
            if resp.status_code != 200:
                continue
            fresh = 0
            for row in csv.DictReader(io.StringIO(resp.text)):
                key = row.get("event_contract", "")
                try:
                    yes_p = float(row.get("yes_price") or 0)
                    no_p = float(row.get("no_price") or 0)
                except ValueError:
                    continue
                if key and 0 < yes_p < 1:
                    # rows are chronological — later rows overwrite with the latest trade
                    index[key] = {"YES": yes_p, "NO": no_p}
                    fresh += 1
            if fresh:
                logger.info(f"IBKR public: overlaid intraday pairs {d} ({fresh} rows)")
                break
        except Exception as e:
            logger.debug(f"IBKR public: pairs {d} failed: {e}")

    return index, price_date


def _csv_keys(symbol: str, expiration: Any, strike: Any, strike_label: str) -> List[str]:
    """Candidate event_contract codes for a REST contract (formats vary)."""
    exp = str(expiration or "")
    codes = []
    if len(exp) == 8:  # YYYYMMDD
        codes = [exp[4:6] + exp[6:8] + exp[2:4], exp[4:6] + exp[2:4]]  # MMDDYY, MMYY

    strikes: List[str] = []
    try:
        f = float(strike)
        strikes.append(str(int(f)) if f == int(f) else str(f))
        strikes.append(str(f))
    except (TypeError, ValueError):
        pass
    if strike_label:
        initials = "".join(w[0] for w in re.split(r"[\s.-]+", str(strike_label).strip()) if w).upper()
        if 2 <= len(initials) <= 4:
            strikes.append(initials)

    keys = []
    for e in codes:
        for s in dict.fromkeys(strikes):
            keys.append(f"{symbol}_{e}_{s}")
    return keys


# ── main entry ───────────────────────────────────────────────────────────────

async def fetch_ibkr_public_markets(on_progress: callable = None) -> List[Dict[str, Any]]:
    markets: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        if on_progress:
            on_progress("discovering events...")
        underlyings = await _fetch_underlyings(client)
        logger.info(f"IBKR public: {len(underlyings)} event underlyings discovered")

        price_index, price_date = await _load_price_index(client)
        if not price_index:
            logger.warning("IBKR public: no price data available from public bucket")
            return []

        sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        done = 0

        async def bounded(u):
            nonlocal done
            async with sem:
                res = await _fetch_contracts(client, u)
                done += 1
                if on_progress and done % 50 == 0:
                    on_progress(f"discovering {done}/{len(underlyings)}")
                return res

        results = await asyncio.gather(*[bounded(u) for u in underlyings])

    all_contracts = [c for batch in results for c in batch]
    logger.info(f"IBKR public: {len(all_contracts)} contracts discovered")

    # Group YES/NO by (event, expiration, strike) and price via CSV keys
    grouped: Dict[str, Dict] = {}
    for c in all_contracts:
        gkey = f"{c['event_symbol']}_{c.get('expiration')}_{c.get('strike')}"
        g = grouped.setdefault(gkey, {
            "event_name": c["event_name"],
            "symbol": c["event_symbol"],
            "expiration": c.get("expiration"),
            "strike": c.get("strike"),
            "strike_label": c.get("strike_label", ""),
            "yes_conid": None,
            "no_conid": None,
        })
        if c.get("side") == "Y":
            g["yes_conid"] = c.get("conid")
        elif c.get("side") == "N":
            g["no_conid"] = c.get("conid")

    matched = 0
    for g in grouped.values():
        prices = None
        for key in _csv_keys(g["symbol"], g["expiration"], g["strike"], g["strike_label"]):
            if key in price_index:
                prices = price_index[key]
                break
        if not prices:
            continue

        yes_p = prices.get("YES")
        no_p = prices.get("NO")
        if yes_p is None and no_p is not None:
            yes_p = round(1.0 - no_p, 4)
        if yes_p is None or not (0 < yes_p < 1):
            continue
        if no_p is None:
            no_p = round(1.0 - yes_p, 4)
        matched += 1

        label = g["strike_label"]
        title = f"{g['event_name']} - {label}" if label and str(label) not in ("0", "0.0", "1.0") else g["event_name"]
        exp = str(g.get("expiration") or "")
        end_date = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}" if len(exp) == 8 else None

        markets.append({
            "id": f"ibkr_{g['yes_conid']}_{g['no_conid']}",
            "platform": "IBKR",
            "title": title,
            "category": g["event_name"],
            "yesPrice": round(yes_p, 4),
            "noPrice": round(no_p, 4),
            "volume": 0,
            "lastUpdated": datetime.utcnow().isoformat(),
            "endDate": end_date,
            "marketUrl": "https://forecasttrader.interactivebrokers.com",
            "isBinary": True,
            "outcomeCount": 2,
            "contractLabel": "Yes",
            "outcomes": None,
            "priceSource": f"public-data ({price_date})",
        })

    logger.info(f"IBKR public: priced {matched}/{len(grouped)} contract groups from public data")
    return markets


async def _tws_reachable() -> bool:
    gateway_url = os.environ.get("IB_GATEWAY_URL", "")
    if not gateway_url:
        return False
    host = gateway_url.split("://")[-1].split(":")[0].split("/")[0]
    try:
        port = int(gateway_url.split(":")[-1].split("/")[0])
    except ValueError:
        port = 4000
    for p in (port, 4000 if port != 4000 else 4001):
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, p), timeout=3.0)
            writer.close()
            return True
        except Exception:
            continue
    return False


async def fetch_ibkr_combined(on_progress: callable = None) -> List[Dict[str, Any]]:
    """TWS live pricing when the gateway is up, public-data fallback otherwise."""
    mode = os.environ.get("IBKR_MODE", "auto").lower()
    if mode == "off":
        return []

    if mode in ("tws", "auto") and await _tws_reachable():
        logger.info("IBKR: gateway reachable — using live TWS pricing")
        try:
            from backend.fetchers.ibkr import fetch_ibkr_markets
            markets = await fetch_ibkr_markets(on_progress=on_progress)
            if markets:
                return markets
            logger.warning("IBKR TWS returned 0 markets — falling back to public data")
        except Exception as e:
            logger.error(f"IBKR TWS path failed ({e}) — falling back to public data")
        if mode == "tws":
            return []

    if mode == "tws":
        logger.warning("IBKR_MODE=tws but gateway unreachable at IB_GATEWAY_URL — skipping IBKR")
        return []

    logger.info("IBKR: using public ForecastEx data (set IB_GATEWAY_URL for live TWS pricing)")
    return await fetch_ibkr_public_markets(on_progress=on_progress)
