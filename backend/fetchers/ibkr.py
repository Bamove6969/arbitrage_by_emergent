import asyncio
import httpx
import logging
import random
import os
from typing import List, Dict, Any
from datetime import datetime
from ib_async import IB, Contract, util

logger = logging.getLogger(__name__)

# Suppress ib_async/ibapi internal wire-level logging — these flood the log
# buffer with thousands of low-level messages and cause measurable I/O overhead.
# TWS API logging level should also be set to "Error" in TWS Global Config.
for _noisy in ("ib_async", "ibapi", "ib_async.client", "ib_async.wrapper", "ib_async.decoder"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

FORECAST_EVENT_URL = "https://forecasttrader.interactivebrokers.com/tws.proxy/public/forecasttrader/event"
FORECAST_MARKET_URL = "https://forecasttrader.interactivebrokers.com/tws.proxy/public/forecasttrader/contract/market"

async def _fetch_events_and_contracts(on_progress: callable = None) -> List[Dict]:
    """Fetches exact YES/NO contracts from the public APIs with full recursion."""
    contracts_data = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Fetch the category tree to discover all active event underlyings
            tree_url = "https://forecasttrader.interactivebrokers.com/tws.proxy/public/forecasttrader/category/tree"
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            
            logger.info("Fetching ForecastEx category tree...")
            tree_resp = await client.get(tree_url, headers=headers)
            tree_resp.raise_for_status()
            
            categories = tree_resp.json().get("categories", {})
            
            # Extract all underlying conids from all categories recursively
            known_underlyings = []
            
            def extract_markets_from_cat(cat_data):
                if "markets" in cat_data:
                    for market in cat_data["markets"]:
                        if "conid" in market:
                            known_underlyings.append((market["conid"], market.get("name", "Unknown")))
                if "subCategories" in cat_data:
                    for sub_id, sub_data in cat_data["subCategories"].items():
                        extract_markets_from_cat(sub_data)
                        
            for cat_id, cat_data in categories.items():
                extract_markets_from_cat(cat_data)
                        
            logger.info(f"Discovered {len(known_underlyings)} total event underlyings in category tree.")
            
            # For each underlying, fetch its specific YES/NO contracts
            logger.info(f"Fetching specific contracts for {len(known_underlyings)} events...")
            
            for i, (underlying_conid, event_name) in enumerate(known_underlyings):
                if on_progress:
                    on_progress(f"discovering {i+1}/{len(known_underlyings)}")
                try:
                    params = {
                        "underlyingConid": str(underlying_conid),
                        "exchange": "FORECASTX"
                    }
                    market_resp = await client.get(FORECAST_MARKET_URL, params=params, headers=headers)
                    market_resp.raise_for_status()
                    market_data = market_resp.json()
                    
                    market_contracts = market_data.get("contracts", [])
                    
                    for mc in market_contracts:
                        mc["event_name"] = event_name
                        mc["underlyingConid"] = underlying_conid
                        contracts_data.append(mc)
                        
                except Exception as e:
                    logger.debug(f"Failed to fetch market data for event {underlying_conid}: {e}")
                    
    except Exception as e:
        logger.error(f"Error fetching from ForecastEx API: {e}")
        
    return contracts_data


async def fetch_ibkr_markets(on_progress: callable = None) -> List[Dict[str, Any]]:
    """
    Combines REST discovery with TWS live pricing to generate the standard markets feed.
    """
    logger.info("Discovering IBKR ForecastEx markets via REST...")
    raw_contracts = await _fetch_events_and_contracts(on_progress=on_progress)
    
    if not raw_contracts:
        logger.warning("No IBKR contracts found from public API.")
        return []

    logger.info(f"Discovered {len(raw_contracts)} IBKR contracts. Connecting to TWS/Gateway...")
    if on_progress:
        on_progress(f"connecting to TWS...")
    
    ib = IB()
    markets = []
    
    # Determine the correct host for TWS connection
    # Inside Docker: use service name 'ibga', outside Docker: use localhost
    gateway_url = os.environ.get("IB_GATEWAY_URL", "http://127.0.0.1:4000")
    if "://" in gateway_url:
        # Extract host from URL like http://ibga:4000
        host = gateway_url.split("://")[1].split(":")[0].split("/")[0]
    else:
        host = gateway_url
    
    # Determine configured port; auto-detect actual listening port as fallback
    if ":" in gateway_url:
        try:
            port = int(gateway_url.split(":")[-1].split("/")[0])
        except:
            port = 4001
    else:
        port = 4001

    # Build ordered port list: configured port first, then the other common gateway port
    _other = 4000 if port != 4000 else 4001
    ports_to_try = [port, _other]

    try:
        client_id = random.randint(10, 999)
        connected = False
        for try_port in ports_to_try:
            if connected:
                break
            for attempt in range(1, 3):
                try:
                    logger.info(f"Connecting to IBKR TWS/Gateway ({host}:{try_port}), clientId={client_id}, attempt {attempt}/2...")
                    await asyncio.wait_for(ib.connectAsync(host, try_port, clientId=client_id), timeout=15.0)
                    connected = True
                    port = try_port
                    break
                except Exception as conn_err:
                    logger.warning(f"IBKR connection attempt {attempt} on port {try_port} failed: {conn_err}")
                    if attempt < 2:
                        await asyncio.sleep(5)
                        client_id = random.randint(10, 999)

        if not connected:
            logger.error("IBKR: Could not connect on ports %s. Skipping IBKR markets.", ports_to_try)
            return []
        
        # We need to map the raw contracts back to our uniform Market structure
        # A single 'Market' in our system represents a Yes/No pair.
        # ForecastEx has a contract for YES and a contract for NO.
        # Sometimes one conid represents YES and another represents NO. Let's see how to bundle them.
        
        # Group YES and NO contracts by their strike
        grouped_markets = {}
        
        for rc in raw_contracts:
            strike = rc.get("strike_label", rc.get("strike", "0"))
            side = rc.get("side", "Y")  # 'Y' or 'N'
            conid = rc.get("conid")
            event_name = rc.get("event_name", "Unknown Event")
            
            group_key = f"{event_name}_{strike}"
            
            if group_key not in grouped_markets:
                grouped_markets[group_key] = {
                    "event_name": event_name,
                    "strike": strike,
                    "yes_conid": None,
                    "no_conid": None,
                    "description": rc.get("description", event_name)
                }
                
            if side == "Y":
                grouped_markets[group_key]["yes_conid"] = conid
            elif side == "N":
                grouped_markets[group_key]["no_conid"] = conid

        # Prepare IB Contracts
        ib_contracts = []
        conid_to_market_key = {}
        
        for k, v in grouped_markets.items():
            if v["yes_conid"]:
                c = Contract(conId=int(v["yes_conid"]), exchange="FORECASTX")
                ib_contracts.append(c)
                conid_to_market_key[v["yes_conid"]] = (k, "Y")
            if v["no_conid"]:
                c = Contract(conId=int(v["no_conid"]), exchange="FORECASTX")
                ib_contracts.append(c)
                conid_to_market_key[v["no_conid"]] = (k, "N")

        # Request market data using streaming (reqMktData) rather than slow snapshots.
        # reqTickersAsync waits up to 11s per batch. reqMktData returns data as it
        # arrives from TWS (typically <1s) — we subscribe, wait a short window, then
        # cancel to free up TWS data lines (default limit: 100 simultaneous subscriptions).
        batch_size = 80  # Stay well under TWS's 100 simultaneous data line limit
        tickers = []
        
        logger.info(f"Streaming {len(ib_contracts)} contracts via reqMktData (cancelling after collection)...")
        for i in range(0, len(ib_contracts), batch_size):
            batch = ib_contracts[i:i+batch_size]
            batch_tickers = []
            try:
                # Subscribe to live streaming data for each contract in the batch.
                # Stagger the requests: IBKR drops messages beyond ~50/sec, and
                # dropped subscriptions mean zero prices + Error 300 on cancel.
                for contract in batch:
                    ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
                    batch_tickers.append(ticker)
                    await asyncio.sleep(0.025)  # ~40 req/sec, under the pacing limit

                # Adaptive wait: poll until most tickers have data, then move on —
                # instead of a blind fixed wait. This adds NO message traffic (just
                # reads already-pushed ticks), so it can't violate pacing limits; it
                # only removes idle waiting. Most ForecastEx quotes arrive <1s.
                def _has_data(t):
                    return ((t.bid and t.bid > 0) or (t.ask and t.ask > 0)
                            or (t.last and t.last > 0))
                _min_wait, _max_wait, _step = 0.6, 4.0, 0.25
                _waited_b = 0.0
                await asyncio.sleep(_min_wait); _waited_b += _min_wait
                while _waited_b < _max_wait:
                    if sum(1 for t in batch_tickers if _has_data(t)) >= 0.9 * len(batch_tickers):
                        break  # 90% populated — stragglers aren't worth the wait
                    await asyncio.sleep(_step); _waited_b += _step

                tickers.extend(batch_tickers)

            except Exception as e:
                logger.warning(f"Error streaming batch starting at {i}: {e}")
            finally:
                # Always cancel subscriptions to free TWS data lines
                for contract in batch:
                    try:
                        ib.cancelMktData(contract)
                    except Exception:
                        pass

            if on_progress:
                on_progress(f"discovering {min(i+batch_size, len(ib_contracts))}/{len(ib_contracts)} (TWS)")
            
            # Brief pause between batches to respect pacing limits
            await asyncio.sleep(0.2)

        
        # Sanity check: if NOTHING returned a price, the gateway session is
        # almost certainly half-dead (API port open but market data auth gone —
        # happens after IBKR's nightly restart if 2FA wasn't re-approved).
        n_priced = sum(1 for t in tickers
                       if (t.bid and t.bid > 0) or (t.ask and t.ask > 0) or (t.last and t.last > 0))
        if tickers and n_priced == 0:
            logger.error(
                "IBKR streaming collected 0 prices across all contracts — "
                "gateway session likely needs re-authentication (fresh 2FA). "
                "Restart the IB Gateway / container and approve the IBKey push.")

        # Extract prices
        group_prices = {}
        for k in grouped_markets:
            group_prices[k] = {"yes_price": 0.5, "no_price": 0.5}

        for t in tickers:
            conid = t.contract.conId
            if conid in conid_to_market_key:
                k, side = conid_to_market_key[conid]
                
                # Best approximation of current price
                # We use the midpoint of bid/ask, fallback to last, fallback to 0.5
                bid = t.bid if t.bid and t.bid > 0 else None
                ask = t.ask if t.ask and t.ask > 0 else None
                
                price = None
                if bid is not None and ask is not None:
                    # ForecastEx prices usually quoted between 0 and 1, or 0 and 100
                    # Assuming 0.0 to 1.0 based on ibkr_forecast.py reference code
                    price = (bid + ask) / 2.0
                elif t.last and t.last > 0:
                    price = t.last
                elif bid is not None:
                    price = bid
                elif ask is not None:
                    price = ask
                
                if price is not None:
                    # In ibkr_forecast.py: "if side == 'Y' yes = price / no = 1-price"
                    # We'll use the precise fetched prices if both available
                    if side == "Y":
                        group_prices[k]["yes_price"] = price
                    else:
                        group_prices[k]["no_price"] = price
        
        # Build final uniform market list
        for k, gm in grouped_markets.items():
            prices = group_prices[k]
            
            # If we couldn't get real market data, skip
            if prices["yes_price"] == 0.5 and prices["no_price"] == 0.5:
                continue

            # In some FORECASTX setups, the YES contract price implies the NO price implicitly (1 - YES),
            # but usually there are explicit Yes/No contracts with their own order books.
            yes_p = prices["yes_price"]
            no_p = prices["no_price"]
            
            title = f"{gm['event_name']} - {gm['strike']}" if str(gm["strike"]) != "0" else gm["event_name"]
            
            market = {
                "id": f"ibkr_{gm['yes_conid']}_{gm['no_conid']}",
                "platform": "IBKR",
                "title": title,
                "category": gm['event_name'],
                "yesPrice": round(yes_p, 4),
                "noPrice": round(no_p, 4),
                "volume": 0,  # IB ticker volume can be added if mapped (t.volume)
                "lastUpdated": datetime.utcnow().isoformat(),
                "endDate": None, # Usually embedded in contract details, left null for now
                "marketUrl": "https://forecasttrader.interactivebrokers.com", 
                "isBinary": True,
                "outcomeCount": 2,
                "contractLabel": "Yes",
                "outcomes": None,
            }
            markets.append(market)

    except Exception as e:
        logger.error(f"IBKR TWS fetch error: {e}")
    finally:
        ib.disconnect()

    logger.info(f"Successfully constructed {len(markets)} IBKR markets.")
    return markets

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = asyncio.run(fetch_ibkr_markets())
    for m in res[:5]:
        print(m)
