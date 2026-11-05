"""SQLite 保存规范化输入与可复现的分析快照。"""
import json
import sqlite3
from contextlib import closing


def persist(path, stocks, prices, news, report):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS stocks (symbol TEXT PRIMARY KEY, company TEXT, industry TEXT, aliases TEXT);
            CREATE TABLE IF NOT EXISTS prices (symbol TEXT REFERENCES stocks(symbol), date TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, date));
            CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, title TEXT, body TEXT, source TEXT,
                url TEXT, published_at TEXT, available_at TEXT, event_type TEXT);
            CREATE TABLE IF NOT EXISTS news_symbols (news_id TEXT REFERENCES news(id),
                symbol TEXT REFERENCES stocks(symbol), PRIMARY KEY(news_id, symbol));
            CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY CHECK(id = 1), report_json TEXT);
        """)
        for table in ("news_symbols", "prices", "news", "stocks", "snapshots"):
            connection.execute(f"DELETE FROM {table}")
        connection.executemany("INSERT INTO stocks VALUES (:symbol,:company,:industry,:aliases)", stocks)
        connection.executemany("INSERT INTO prices VALUES (:symbol,:date,:open,:high,:low,:close,:volume)", prices)
        connection.executemany("INSERT INTO news VALUES (:id,:title,:body,:source,:url,:published_at,:available_at,:event_type)", news)
        connection.executemany("INSERT INTO news_symbols VALUES (?,?)", [(item["id"], symbol) for item in news for symbol in item["symbols"]])
        connection.execute("INSERT INTO snapshots VALUES (1,?)", (json.dumps(report, ensure_ascii=False, allow_nan=False),))
