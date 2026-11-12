"""追加式 SQLite 知识库：原始请求、规范记录及带证据的关系。"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .models import date_available, day, digest, instant, json_text, now, symbol

KINDS = {"security", "company", "alias", "calendar", "price", "adjustment", "shareholding", "action", "relation"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kb_runs (
    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
    status TEXT NOT NULL, config_json TEXT NOT NULL, summary_json TEXT);
CREATE TABLE IF NOT EXISTS kb_requests (
    request_id TEXT PRIMARY KEY, api_name TEXT NOT NULL, params_json TEXT NOT NULL,
    observed_at TEXT NOT NULL, raw_ref TEXT NOT NULL, sha256 TEXT NOT NULL, row_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kb_run_requests (
    run_id TEXT REFERENCES kb_runs(run_id), request_id TEXT REFERENCES kb_requests(request_id),
    PRIMARY KEY(run_id, request_id));
CREATE TABLE IF NOT EXISTS kb_tasks (
    run_id TEXT REFERENCES kb_runs(run_id), task_key TEXT NOT NULL, status TEXT NOT NULL,
    rows_received INTEGER NOT NULL, records_written INTEGER NOT NULL, error TEXT,
    PRIMARY KEY(run_id, task_key));
CREATE TABLE IF NOT EXISTS kb_records (
    observation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, natural_key TEXT NOT NULL,
    ts_code TEXT NOT NULL, effective_date TEXT, disclosed_date TEXT, observed_at TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES kb_requests(request_id), payload_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kb_record_lookup ON kb_records(ts_code, kind, effective_date);
CREATE INDEX IF NOT EXISTS kb_record_versions ON kb_records(kind, natural_key, observed_at);
"""


class MarketKnowledgeBase:
    """与演示报告数据库分开保存，采集不会清空现有快照。每次方法调用关闭连接。"""

    def __init__(self, path="data/knowledge_base/tushare.sqlite3"):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.executescript(SCHEMA)
            version = connection.execute("SELECT value FROM kb_metadata WHERE key='schema_version'").fetchone()
            if version and version[0] != "1":
                raise ValueError("不支持的市场知识库版本")
            connection.execute("INSERT OR IGNORE INTO kb_metadata VALUES ('schema_version', '1')")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def start_run(self, config):
        run_id = uuid4().hex
        with closing(self.connect()) as connection, connection:
            connection.execute("INSERT INTO kb_runs VALUES (?,?,NULL,'running',?,NULL)", (run_id, now(), json_text(config)))
        return run_id

    def add_request(self, run_id, result):
        with closing(self.connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO kb_requests VALUES (?,?,?,?,?,?,?)",
                               (result.request_id, result.api_name, json_text(result.params), result.observed_at,
                                result.raw_ref, result.sha256, len(result.rows)))
            connection.execute("INSERT OR IGNORE INTO kb_run_requests VALUES (?,?)", (run_id, result.request_id))

    def task(self, run_id, task_key, status, rows=0, written=0, error=None):
        with closing(self.connect()) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO kb_tasks VALUES (?,?,?,?,?,?)",
                               (run_id, task_key, status, rows, written, error))

    def add_records(self, records):
        """一个任务的全部规范记录原子提交；相同原始请求重放不重复写入。"""
        count = 0
        with closing(self.connect()) as connection, connection:
            for record in records:
                kind, key, code, effective, disclosed, result, payload = record
                if kind not in KINDS:
                    raise ValueError(f"未知知识记录类型：{kind}")
                encoded = json_text(payload)
                observation_id = digest([kind, key, result.request_id, payload])
                cursor = connection.execute("INSERT OR IGNORE INTO kb_records VALUES (?,?,?,?,?,?,?,?,?)",
                    (observation_id, kind, key, code, effective, disclosed, result.observed_at, result.request_id, encoded))
                count += cursor.rowcount
        return count

    def finish_run(self, run_id, summary):
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE kb_runs SET completed_at=?,status=?,summary_json=? WHERE run_id=?",
                               (now(), summary["status"], json_text(summary), run_id))

    def runs(self):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM kb_runs ORDER BY started_at")]

    def records(self, kind, *, ts_code=None, as_of=None, availability="observed", run_id=None):
        """默认只返回当时已实际采集的数据；披露日期回顾模式必须显式启用。"""
        if kind not in KINDS or availability not in {"observed", "disclosed_assumption"}:
            raise ValueError("记录类型或 availability 无效")
        cutoff = instant(as_of) if as_of else datetime.now(timezone.utc)
        sql, params = "SELECT * FROM kb_records WHERE kind=?", [kind]
        if ts_code:
            sql += " AND ts_code=?"
            params.append(symbol(ts_code))
        if run_id:
            sql += " AND request_id IN (SELECT request_id FROM kb_run_requests WHERE run_id=?)"
            params.append(run_id)
        sql += " ORDER BY observed_at, observation_id"
        latest = {}
        with closing(self.connect()) as connection:
            for row in connection.execute(sql, params):
                available = instant(row["observed_at"])
                if availability == "disclosed_assumption" and row["disclosed_date"]:
                    available = date_available(row["disclosed_date"])
                if available > cutoff:
                    continue
                payload = json.loads(row["payload_json"])
                payload["_provenance"] = dict(observation_id=row["observation_id"], request_id=row["request_id"],
                    observed_at=row["observed_at"], disclosed_date=row["disclosed_date"],
                    available_at=available.isoformat(), availability=availability)
                latest[row["natural_key"]] = payload
        return list(latest.values())

    def stock_context(self, ts_code, *, start_date=None, end_date=None, as_of=None, availability="observed"):
        """供新闻融合层调用；股东只取最近一个已可用的完整报告快照。"""
        code = symbol(ts_code)
        start, end = day(start_date) if start_date else "0001-01-01", day(end_date) if end_date else "9999-12-31"
        if start > end:
            raise ValueError("日期范围起点晚于终点")
        options = dict(ts_code=code, as_of=as_of, availability=availability)
        data = {kind: self.records(kind, **options) for kind in KINDS - {"calendar"}}
        # 查询指定分析区间时不能把更晚报告期的股东作为区间内的关系。
        holdings = [r for r in data["shareholding"] if r["report_date"] <= end and r["ann_date"] <= end]
        if holdings:
            snapshot = max((r["report_date"], r["ann_date"]) for r in holdings)
            holdings = [r for r in holdings if (r["report_date"], r["ann_date"]) == snapshot]
            latest = max(holdings, key=lambda r:r["_provenance"]["observed_at"])
            batch = latest.get("snapshot_batch")
            if batch:
                holdings = [r for r in holdings if r.get("snapshot_batch") == batch]
        holding_ids = {r["holding_id"] for r in holdings}
        relations = [r for r in data["relation"] if r["predicate"] != "holds_shares" or r["holding_id"] in holding_ids]
        actions = [r for r in data["action"] if any(start <= d <= end for d in r["event_dates"])]
        graph_nodes = {}
        for relation in relations:
            for side in ("subject", "object"):
                graph_nodes[relation[side]] = dict(id=relation[side], name=relation[f"{side}_name"])
        return dict(ts_code=code, as_of=as_of or now(), availability=availability,
            securities=data["security"], companies=data["company"], aliases=data["alias"],
            prices=sorted([r for r in data["price"] if start <= r["trade_date"] <= end], key=lambda r:r["trade_date"]),
            adjustment_factors=sorted([r for r in data["adjustment"] if start <= r["trade_date"] <= end], key=lambda r:r["trade_date"]),
            shareholdings=holdings, corporate_actions=actions,
            graph=dict(nodes=list(graph_nodes.values()), edges=relations),
            limitations=["主数据与行业是采集时快照；前十大股东不等于完整股权、控制或供应链关系。",
                          "disclosed_assumption 是按披露日期上界回顾，不证明历史首次可得，也不消除数据修订。"])

    def verify_raw(self):
        import hashlib
        failures = []
        with closing(self.connect()) as connection:
            for row in connection.execute("SELECT request_id,raw_ref,sha256 FROM kb_requests"):
                path = Path(row["raw_ref"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                    failures.append(row["request_id"])
        return dict(status="failed" if failures else "passed", failed_requests=failures)

    def run_counts(self, run_id):
        with closing(self.connect()) as connection:
            return {row[0]: row[1] for row in connection.execute("""
                SELECT kind, COUNT(DISTINCT natural_key) FROM kb_records
                WHERE request_id IN (SELECT request_id FROM kb_run_requests WHERE run_id=?) GROUP BY kind
                """, (run_id,))}
