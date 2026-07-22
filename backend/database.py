import aiosqlite
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "arbitrage.db")

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db

async def init_db():
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS markets (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                yes_price REAL NOT NULL DEFAULT 0.5,
                no_price REAL NOT NULL DEFAULT 0.5,
                volume REAL DEFAULT 0,
                last_updated TEXT,
                end_date TEXT,
                market_url TEXT,
                is_binary INTEGER DEFAULT 1,
                outcome_count INTEGER DEFAULT 2,
                contract_label TEXT,
                outcomes_json TEXT,
                is_weather INTEGER DEFAULT 0,
                fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS matched_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a_id TEXT NOT NULL,
                market_b_id TEXT NOT NULL,
                market_c_id TEXT,
                match_score REAL NOT NULL DEFAULT 0,
                match_reason TEXT,
                combined_yes_cost REAL,
                potential_profit REAL,
                roi REAL,
                combo_type TEXT DEFAULT 'pair',
                leg_count INTEGER DEFAULT 2,
                legs_json TEXT,
                fees REAL DEFAULT 0,
                earliest_resolution TEXT,
                scenario TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (market_a_id) REFERENCES markets(id),
                FOREIGN KEY (market_b_id) REFERENCES markets(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a_id TEXT NOT NULL,
                market_a_title TEXT,
                market_a_platform TEXT,
                market_b_id TEXT NOT NULL,
                market_b_title TEXT,
                market_b_platform TEXT,
                match_score REAL,
                match_reason TEXT,
                verdict TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id TEXT PRIMARY KEY,
                watchlist_id TEXT,
                market_name TEXT NOT NULL,
                maker_roi REAL NOT NULL DEFAULT 0,
                taker_roi REAL NOT NULL DEFAULT 0,
                site_a_yes_price REAL,
                site_b_yes_price REAL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS embeddings_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_input TEXT NOT NULL UNIQUE,
                embedding_json TEXT NOT NULL,
                model TEXT DEFAULT 'simple',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sms_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'disabled',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id TEXT PRIMARY KEY,
                market_name TEXT NOT NULL,
                site_a_name TEXT NOT NULL,
                site_b_name TEXT NOT NULL,
                site_a_yes_price REAL NOT NULL DEFAULT 0.5,
                site_b_yes_price REAL NOT NULL DEFAULT 0.5,
                investment REAL NOT NULL DEFAULT 500,
                alert_threshold REAL NOT NULL DEFAULT 3.0,
                is_active INTEGER DEFAULT 1,
                last_checked TEXT,
                last_maker_roi REAL,
                last_taker_roi REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scan_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_scan_time TEXT,
                next_scan_time TEXT,
                is_scanning INTEGER DEFAULT 0,
                scan_progress REAL DEFAULT 0,
                scan_phase TEXT DEFAULT 'idle',
                scan_message TEXT DEFAULT '',
                total_markets INTEGER DEFAULT 0,
                total_opportunities INTEGER DEFAULT 0
            );

            INSERT OR IGNORE INTO scan_state (id) VALUES (1);

            CREATE TABLE IF NOT EXISTS arbitrage_history (
                id TEXT PRIMARY KEY,
                market_name TEXT NOT NULL,
                site_a_name TEXT NOT NULL,
                site_b_name TEXT NOT NULL,
                site_a_yes_price REAL NOT NULL,
                site_b_yes_price REAL NOT NULL,
                investment REAL NOT NULL,
                order_mode TEXT DEFAULT 'Maker',
                gross_roi REAL,
                net_roi REAL,
                net_profit REAL,
                shares INTEGER,
                is_profitable INTEGER DEFAULT 0,
                scenario TEXT,
                leg_count INTEGER DEFAULT 2,
                legs_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_hash TEXT NOT NULL,
                platform TEXT NOT NULL,
                yes_price REAL NOT NULL,
                volume REAL DEFAULT 0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_price_history_hash ON market_price_history(match_hash, timestamp);
        """)
        await db.commit()

        await _migrate_tables(db)
    finally:
        await db.close()


async def _migrate_tables(db):
    try:
        cursor = await db.execute("PRAGMA table_info(matched_pairs)")
        cols = {row[1] for row in await cursor.fetchall()}
        migrations = {
            "market_c_id": "ALTER TABLE matched_pairs ADD COLUMN market_c_id TEXT",
            "combo_type": "ALTER TABLE matched_pairs ADD COLUMN combo_type TEXT DEFAULT 'pair'",
            "leg_count": "ALTER TABLE matched_pairs ADD COLUMN leg_count INTEGER DEFAULT 2",
            "legs_json": "ALTER TABLE matched_pairs ADD COLUMN legs_json TEXT",
            "fees": "ALTER TABLE matched_pairs ADD COLUMN fees REAL DEFAULT 0",
            "earliest_resolution": "ALTER TABLE matched_pairs ADD COLUMN earliest_resolution TEXT",
            "scenario": "ALTER TABLE matched_pairs ADD COLUMN scenario TEXT",
        }
        for col, sql in migrations.items():
            if col not in cols:
                await db.execute(sql)

        cursor = await db.execute("PRAGMA table_info(arbitrage_history)")
        cols = {row[1] for row in await cursor.fetchall()}
        history_migrations = {
            "is_profitable": "ALTER TABLE arbitrage_history ADD COLUMN is_profitable INTEGER DEFAULT 0",
            "scenario": "ALTER TABLE arbitrage_history ADD COLUMN scenario TEXT",
            "leg_count": "ALTER TABLE arbitrage_history ADD COLUMN leg_count INTEGER DEFAULT 2",
            "legs_json": "ALTER TABLE arbitrage_history ADD COLUMN legs_json TEXT",
        }
        for col, sql in history_migrations.items():
            if col not in cols:
                await db.execute(sql)

        # Markets migrations
        cursor = await db.execute("PRAGMA table_info(markets)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "is_weather" not in cols:
            await db.execute("ALTER TABLE markets ADD COLUMN is_weather INTEGER DEFAULT 0")

        await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Migration warning: {e}")
