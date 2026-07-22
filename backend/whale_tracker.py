import requests
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

POLYMARKET_DATA_API = "https://data-api.polymarket.com/v1"

def fetch_leaderboard(time_period: str = "ALL", category: str = "OVERALL") -> List[Dict[str, Any]]:
    """
    Fetches the top traders from Polymarket's Data API.
    """
    try:
        url = f"{POLYMARKET_DATA_API}/leaderboard"
        params = {
            "period": time_period,
            "category": category,
            "orderBy": "PNL",
            "limit": 20
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        # Normalize Data API fields to what the frontend expects
        return [{
            **r,
            "username": r.get("userName") or r.get("username") or "",
            "proxyAddress": r.get("proxyWallet") or r.get("proxyAddress") or "",
            "volume": r.get("vol") or r.get("volume") or 0,
            "pnl": r.get("pnl", 0),
        } for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch Polymarket leaderboard: {e}")
        return []

def fetch_whale_activity(address: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fetches recent trade activity for a specific wallet address.
    """
    try:
        url = f"{POLYMARKET_DATA_API}/activity"
        params = {
            "user": address,
            "limit": limit
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch activity for whale {address}: {e}")
        return []

def build_market_pools(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    PredictIt and IBKR do not publish trader identities, so their "whales"
    are represented as money concentration: cached markets ranked by volume.
    """
    def section(platform: str) -> Dict[str, Any]:
        rows = [m for m in markets if m.get("platform", "").lower() == platform]
        rows.sort(key=lambda m: float(m.get("volume") or 0), reverse=True)
        top = [{
            "id": m.get("id"),
            "title": m.get("title"),
            "volume": float(m.get("volume") or 0),
            "yesPrice": m.get("yesPrice"),
            "endDate": m.get("endDate"),
            "marketUrl": m.get("marketUrl"),
        } for m in rows[:10]]
        return {
            "biggest": top[0] if top else None,
            "rows": top,
            "hasVolume": any(r["volume"] > 0 for r in top),
        }

    return {"predictit": section("predictit"), "ibkr": section("ibkr")}

def get_whale_market_overlap() -> List[Dict[str, Any]]:
    """
    Aggregates the most active markets among the top whales.
    """
    leaderboard = fetch_leaderboard(time_period="WEEK")
    if not leaderboard:
        return []
    
    market_counts = {}
    
    # Just take the top 5 for activity to avoid rate limits
    for whale in leaderboard[:5]:
        activity = fetch_whale_activity(whale.get("proxyAddress"))
        for act in activity:
            m_id = act.get("marketId")
            if m_id:
                if m_id not in market_counts:
                    market_counts[m_id] = {
                        "marketId": m_id,
                        "title": act.get("title"),
                        "whaleCount": 0,
                        "totalWhaleVolume": 0
                    }
                market_counts[m_id]["whaleCount"] += 1
                # Estimate volume if available
                market_counts[m_id]["totalWhaleVolume"] += float(act.get("amount", 0))
                
    sorted_markets = sorted(market_counts.values(), key=lambda x: x["whaleCount"], reverse=True)
    return sorted_markets[:10]
