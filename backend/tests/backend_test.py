"""
Backend regression test suite for Prediction-Market Arbitrage Scanner.
Covers: health, scan-status, market-stats, arbitrage-opportunities, markets search,
watchlist CRUD, arbitrage-history CRUD, whales leaderboard.
DOES NOT trigger POST /api/scan (5+ min, already complete).
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read from frontend/.env (test env)
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"

TIMEOUT = 30


@pytest.fixture(scope="session")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------------- health ----------------
class TestHealth:
    def test_health_ok(self, api):
        r = api.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok"


# ---------------- scan-status ----------------
class TestScanStatus:
    def test_scan_status_complete(self, api):
        r = api.get(f"{BASE_URL}/api/scan-status", timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") == "complete"
        assert d.get("progress") == 100
        assert d.get("is_scanning") is False
        assert d.get("total_markets", 0) > 20000
        assert d.get("total_opportunities", 0) > 0


# ---------------- market-stats ----------------
class TestMarketStats:
    def test_market_stats_shape(self, api):
        r = api.get(f"{BASE_URL}/api/market-stats", timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        for key in ("polymarket", "predictit", "ibkr", "total"):
            assert key in d, f"missing key {key}"
        assert d["polymarket"] > 20000
        assert d["predictit"] > 500
        assert d["ibkr"] > 500
        assert d["total"] > 20000


# ---------------- arbitrage-opportunities ----------------
class TestArbitrageOpps:
    def test_arb_opps_shape(self, api):
        r = api.get(
            f"{BASE_URL}/api/arbitrage-opportunities?limit=10&minRoi=1",
            timeout=TIMEOUT,
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        item = data[0]
        for key in ("marketA", "marketB", "legs"):
            assert key in item, f"missing key {key}: {list(item.keys())}"
        assert item["marketA"].get("title")
        assert item["marketB"].get("title")
        assert isinstance(item["legs"], list) and len(item["legs"]) >= 2


# ---------------- markets search ----------------
class TestMarkets:
    def test_markets_search_cpi(self, api):
        r = api.get(f"{BASE_URL}/api/markets?q=cpi", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        titles = " ".join(m.get("title", "").lower() for m in data[:20])
        assert "cpi" in titles


# ---------------- watchlist CRUD ----------------
class TestWatchlist:
    created_id = None

    def test_create_watchlist(self, api):
        payload = {
            "marketName": "TEST_market_cpi_2p5",
            "siteAName": "Polymarket",
            "siteBName": "IBKR",
            "siteAYesPrice": 0.45,
            "siteBYesPrice": 0.48,
            "investment": 100,
            "alertThreshold": 5,
            "isActive": True,
        }
        r = api.post(f"{BASE_URL}/api/watchlist", json=payload, timeout=TIMEOUT)
        assert r.status_code in (200, 201), f"got {r.status_code}: {r.text[:400]}"
        d = r.json()
        assert d.get("marketName") == "TEST_market_cpi_2p5" or d.get("market_name") == "TEST_market_cpi_2p5"
        wid = d.get("id") or d.get("_id")
        assert wid is not None
        TestWatchlist.created_id = wid

    def test_get_watchlist(self, api):
        r = api.get(f"{BASE_URL}/api/watchlist", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        names = [
            (m.get("marketName") or m.get("market_name")) for m in data
        ]
        assert "TEST_market_cpi_2p5" in names

    def test_patch_watchlist(self, api):
        assert TestWatchlist.created_id is not None
        r = api.patch(
            f"{BASE_URL}/api/watchlist/{TestWatchlist.created_id}",
            json={"investment": 250, "isActive": False},
            timeout=TIMEOUT,
        )
        assert r.status_code in (200, 204), f"{r.status_code}: {r.text[:400]}"

    def test_delete_watchlist(self, api):
        assert TestWatchlist.created_id is not None
        r = api.delete(
            f"{BASE_URL}/api/watchlist/{TestWatchlist.created_id}",
            timeout=TIMEOUT,
        )
        assert r.status_code in (200, 204)
        # verify removal
        r = api.get(f"{BASE_URL}/api/watchlist", timeout=TIMEOUT)
        names = [(m.get("marketName") or m.get("market_name")) for m in r.json()]
        assert "TEST_market_cpi_2p5" not in names


# ---------------- arbitrage-history CRUD ----------------
class TestArbitrageHistory:
    def test_history_flow(self, api):
        payload = {
            "marketName": "TEST_hist_market",
            "siteAName": "Polymarket",
            "siteBName": "Kalshi",
            "siteAYesPrice": 0.5,
            "siteBYesPrice": 0.52,
            "investment": 200,
        }
        r = api.post(f"{BASE_URL}/api/arbitrage-history", json=payload, timeout=TIMEOUT)
        assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:400]}"

        r = api.get(f"{BASE_URL}/api/arbitrage-history", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        names = [(m.get("marketName") or m.get("market_name")) for m in data]
        assert "TEST_hist_market" in names

        r = api.delete(f"{BASE_URL}/api/arbitrage-history", timeout=TIMEOUT)
        assert r.status_code in (200, 204)


# ---------------- whales ----------------
class TestWhales:
    def test_whales_leaderboard(self, api):
        r = api.get(f"{BASE_URL}/api/whales/leaderboard", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        w = data[0]
        # Accept either 'username' or 'userName'
        uname = w.get("username") or w.get("userName")
        assert uname is not None
        assert "proxyAddress" in w or "proxyWallet" in w
        assert "volume" in w or "vol" in w
        assert "pnl" in w
        # Ensure not everyone is "Anonymous"
        unames = [(x.get("username") or x.get("userName") or "") for x in data]
        anon = sum(1 for n in unames if n.lower() in ("anonymous", ""))
        assert anon < len(unames), "All whales are Anonymous"
