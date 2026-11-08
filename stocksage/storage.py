"""SQLite 保存规范化输入与可复现的分析快照。"""
import json
import sqlite3
from contextlib import closing


def persist(path, stocks, prices, news, report, calendar=None):
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
            CREATE TABLE IF NOT EXISTS dataset_metadata (id INTEGER PRIMARY KEY CHECK(id = 1), metadata_json TEXT);
            CREATE TABLE IF NOT EXISTS calendar_sessions (exchange TEXT, date TEXT, source TEXT,
                source_url TEXT, fetched_at TEXT, raw_ref TEXT, PRIMARY KEY(exchange, date));
            CREATE TABLE IF NOT EXISTS knowledge_triples (id TEXT PRIMARY KEY,
                news_id TEXT REFERENCES news(id), subject TEXT, predicate TEXT, object TEXT,
                backend TEXT, available_at TEXT, evidence_text TEXT, attributes_json TEXT);
        """)
        for table in ("stocks", "prices", "news"):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "attributes_json" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN attributes_json TEXT")
        for table in ("knowledge_triples", "news_symbols", "prices", "news", "stocks", "snapshots", "dataset_metadata", "calendar_sessions"):
            connection.execute(f"DELETE FROM {table}")
        connection.executemany("INSERT INTO stocks (symbol,company,industry,aliases) VALUES (:symbol,:company,:industry,:aliases)", stocks)
        connection.executemany("INSERT INTO prices (symbol,date,open,high,low,close,volume) VALUES (:symbol,:date,:open,:high,:low,:close,:volume)", prices)
        connection.executemany("INSERT INTO news (id,title,body,source,url,published_at,available_at,event_type) VALUES (:id,:title,:body,:source,:url,:published_at,:available_at,:event_type)", news)
        # 扩展字段随规范记录完整落库；兼容已有 demo 数据库，避免来源字段在持久化时丢失。
        encode = lambda row: json.dumps(row, ensure_ascii=False, allow_nan=False)
        connection.executemany("UPDATE stocks SET attributes_json=? WHERE symbol=?", [(encode(s), s["symbol"]) for s in stocks])
        connection.executemany("UPDATE prices SET attributes_json=? WHERE symbol=? AND date=?", [(encode(p), p["symbol"], p["date"]) for p in prices])
        connection.executemany("UPDATE news SET attributes_json=? WHERE id=?", [(encode(n), n["id"]) for n in news])
        connection.execute("INSERT INTO dataset_metadata VALUES (1,?)", (encode(report["dataset"]),))
        connection.executemany("INSERT INTO calendar_sessions VALUES (:exchange,:date,:source,:source_url,:fetched_at,:raw_ref)", calendar or [])
        connection.executemany("INSERT INTO news_symbols VALUES (?,?)", [(item["id"], symbol) for item in news for symbol in item["symbols"]])
        triples = [triple for item in news for triple in item.get("triples", [])]
        connection.executemany("INSERT INTO knowledge_triples VALUES (?,?,?,?,?,?,?,?,?)",
                               [(t["id"], t["evidence_id"], t["subject"], t["predicate"], t["object"],
                                 t["backend"], t["available_at"], t["evidence_text"], encode(t)) for t in triples])
        connection.execute("INSERT INTO snapshots VALUES (1,?)", (json.dumps(report, ensure_ascii=False, allow_nan=False),))
