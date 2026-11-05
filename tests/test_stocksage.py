import copy
import csv
import json
import math
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from stocksage.data import PRICE_FIELDS, generate_demo, load_dataset, timestamp, write_csv
from stocksage.forecast import evaluate
from stocksage.fusion import align_news, build_features
from stocksage.pipeline import run_pipeline
from stocksage.server import make_handler


def article(identifier, available):
    return dict(id=identifier, title="订单增长", body="需求改善", source="测试", url="",
                published_at=available, available_at=available, symbols=["T"], event_type="订单")


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.prices = [dict(date=day) for day in ("2025-01-03", "2025-01-06", "2025-01-07")]

    def test_after_close_weekend_timezone_and_pending(self):
        news = [article("close", "2025-01-03T15:00:00+08:00"),
                article("late", "2025-01-03T15:00:01+08:00"),
                article("weekend", "2025-01-04T10:00:00+08:00"),
                article("utc", "2025-01-06T06:00:00+00:00"),
                article("future", "2025-01-07T18:00:00+08:00")]
        buckets, pending = align_news(self.prices, news)
        self.assertEqual([n["id"] for n in buckets["2025-01-03"]], ["close"])
        self.assertEqual([n["id"] for n in buckets["2025-01-06"]], ["late", "weekend", "utc"])
        self.assertEqual([n["id"] for n in pending], ["future"])

    def test_uses_availability_rather_than_publication(self):
        news = article("delayed", "2025-01-06T09:00:00+08:00")
        news["published_at"] = "2025-01-03T09:00:00+08:00"
        buckets, _ = align_news(self.prices, [news])
        self.assertFalse(buckets["2025-01-03"])
        self.assertEqual(buckets["2025-01-06"][0]["id"], "delayed")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            timestamp("2025-01-03T10:00:00")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.report = run_pipeline(output_dir=cls.root / "output")
        cls.data_dir = cls.root / "output" / "demo_data"

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_report_and_database_have_consistent_records(self):
        report = self.report
        self.assertTrue(report["dataset"]["synthetic"])
        self.assertEqual(len(report["stocks"]), 3)
        exported = json.loads((self.root / "output" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(exported, report)
        with closing(sqlite3.connect(self.root / "output" / "stocksage.sqlite3")) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM prices").fetchone()[0], 330)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(json.loads(conn.execute("SELECT report_json FROM snapshots").fetchone()[0]), report)
        for analysis in report["reports"].values():
            self.assertEqual(len(analysis["prices"]), 110)
            self.assertEqual(len(analysis["features"]), 105)
            forecast = analysis["forecast"]
            self.assertTrue(math.isfinite(forecast["next_return"]))
            self.assertAlmostEqual(forecast["next_return"], sum(forecast["contributions"]) + forecast["intercept"])
            for trace in forecast["backtest"]:
                self.assertLess(trace["train_end"], trace["date"])
                self.assertLessEqual(trace["train_label_end"], trace["date"])
                self.assertGreater(trace["target_date"], trace["date"])
            self.assertEqual(len({m["samples"] for m in forecast["metrics"].values()}), 1)
            observable_ids = {n["id"] for n in analysis["news"]}
            self.assertTrue(set(analysis["report"]["evidence_ids"]) <= observable_ids)
            self.assertFalse(observable_ids & {n["id"] for n in analysis["pending_news"]})
            for edge in analysis["graph"]["edges"]:
                if edge["evidence_id"]:
                    self.assertIn(edge["evidence_id"], observable_ids)

    def test_csv_reimport_is_equivalent_and_does_not_change_inputs(self):
        before = {path.name: path.read_bytes() for path in self.data_dir.iterdir()}
        imported = run_pipeline(self.data_dir, self.root / "imported")
        self.assertEqual(imported["reports"], self.report["reports"])
        after = {path.name: path.read_bytes() for path in self.data_dir.iterdir()}
        self.assertEqual(before, after)

    def test_future_changes_cannot_change_earlier_features_or_predictions(self):
        stocks, all_prices, news, _ = load_dataset(self.data_dir)
        symbol = stocks[0]["symbol"]
        prices = [p for p in all_prices if p["symbol"] == symbol]
        news = [n for n in news if symbol in n["symbols"]]
        buckets, _ = align_news(prices, news)
        features = build_features(prices, buckets)
        changed_prices, changed_news = copy.deepcopy(prices), copy.deepcopy(news)
        cutoff = prices[85]["date"]
        for p in changed_prices:
            if p["date"] > cutoff:
                p["close"] *= 1.3
                p["volume"] *= 10
        for n in changed_news:
            if timestamp(n["available_at"]).date().isoformat() > cutoff:
                n["body"] = "风险风险风险"
        changed_buckets, _ = align_news(changed_prices, changed_news)
        changed_features = build_features(changed_prices, changed_buckets)
        original_backtest = evaluate(features)["backtest"]
        changed_backtest = evaluate(changed_features)["backtest"]
        for before, after in zip(features, changed_features):
            if before["date"] <= cutoff:
                self.assertEqual(before["features"], after["features"])
        for before, after in zip(original_backtest, changed_backtest):
            if before["date"] <= cutoff:
                self.assertEqual(before["fused"], after["fused"])
                self.assertEqual(before["price_only"], after["price_only"])

    def test_future_news_does_not_change_summary_graph_or_forecast(self):
        directory = self.root / "future_news"
        generate_demo(directory)
        path = directory / "news.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        new = dict(rows[0])
        new.update(id="future", title="未来重大增长", published_at="2030-01-01T10:00:00+08:00", available_at="2030-01-01T10:00:00+08:00")
        write_csv(path, fields, rows + [new])
        changed = run_pipeline(directory, self.root / "future_result")
        symbol = new["symbols"]
        for key in ("graph", "forecast", "report"):
            self.assertEqual(self.report["reports"][symbol][key], changed["reports"][symbol][key])
        before_summary = {key: value for key, value in self.report["reports"][symbol]["summary"].items() if key != "pending_count"}
        after_summary = {key: value for key, value in changed["reports"][symbol]["summary"].items() if key != "pending_count"}
        self.assertEqual(before_summary, after_summary)
        self.assertEqual(changed["reports"][symbol]["summary"]["pending_count"], self.report["reports"][symbol]["summary"]["pending_count"] + 1)
        self.assertIn("future", {n["id"] for n in changed["reports"][symbol]["pending_news"]})

    def test_invalid_price_and_duplicate_rejected(self):
        directory = self.root / "invalid"
        generate_demo(directory)
        _, prices, _, _ = load_dataset(directory)
        prices[0]["close"] = float("nan")
        write_csv(directory / "prices.csv", PRICE_FIELDS, prices)
        with self.assertRaisesRegex(ValueError, "数值无效"):
            load_dataset(directory)
        generate_demo(directory)
        _, prices, _, _ = load_dataset(directory)
        write_csv(directory / "prices.csv", PRICE_FIELDS, prices + [prices[0]])
        with self.assertRaisesRegex(ValueError, "日期重复"):
            load_dataset(directory)

    def test_unknown_symbols_and_unavailable_time_rejected(self):
        directory = self.root / "bad_news"
        generate_demo(directory)
        path = directory / "news.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        original = rows[0]["symbols"]
        rows[0]["symbols"] = "UNKNOWN"
        write_csv(path, fields, rows)
        with self.assertRaisesRegex(ValueError, "股票"):
            load_dataset(directory)
        rows[0]["symbols"] = original
        rows[0]["available_at"] = "2020-01-01T00:00:00+08:00"
        write_csv(path, fields, rows)
        with self.assertRaisesRegex(ValueError, "可用时间早于"):
            load_dataset(directory)

    def test_local_http_api_and_static_files(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.report))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/", "/style.css", "/app.js", "/api/health", "/api/stocks", "/api/dashboard?symbol=DEMO02"):
                with urlopen(base + path, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read())
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            for path in ("/api/dashboard?symbol=UNKNOWN", "/../run.py", "/run.py"):
                with self.assertRaises(HTTPError) as context:
                    urlopen(base + path, timeout=5)
                self.assertEqual(context.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
