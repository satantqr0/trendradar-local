"""Exercise source status against the pinned collector schema, with no network."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from source_health import ZONE, source_health


SCHEMA = Path(__file__).resolve().parent / "fixtures"


def create_database(root, kind, day, names, crawls, items=()):
    folder = root / kind
    folder.mkdir(exist_ok=True)
    path = folder / f"{day}.db"
    rss = kind == "rss"
    registry, records, statuses, items_table, source_column = (
        ("rss_feeds", "rss_crawl_records", "rss_crawl_status", "rss_items", "feed_id") if rss
        else ("platforms", "crawl_records", "crawl_source_status", "news_items", "platform_id")
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript((SCHEMA / ("rss_schema.sql" if rss else "schema.sql")).read_text())
        connection.executemany(f"INSERT INTO {registry}(id,name) VALUES(?,?)", names.items())
        for time, statuses_at_time in crawls.items():
            record = connection.execute(f"INSERT INTO {records}(crawl_time) VALUES(?)", (time,)).lastrowid
            connection.executemany(
                f"INSERT INTO {statuses}(crawl_record_id,{source_column},status) VALUES(?,?,?)",
                ((record, source_id, state) for source_id, state in statuses_at_time.items()),
            )
        rank_column, rank_value = ("", "") if rss else (",rank", ",1")
        connection.executemany(
            f"INSERT INTO {items_table}(title,url,{source_column},first_crawl_time,last_crawl_time{rank_column}) "
            f"VALUES('标题',?,?,?,?{rank_value})",
            items,
        )
    return path


class SourceHealthTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def report(self, now="2026-11-07T00:31:00", leads=(), notices=()):
        meta = {"热榜平台": "15 / 15", "RSS 源": "10/11"}
        return source_health(self.root, datetime.fromisoformat(now).replace(tzinfo=ZONE), meta, leads, notices)

    def test_midnight_failure_success_zero_and_never_attempted(self):
        yesterday = create_database(
            self.root, "news", "2026-11-06", {"retry": "有历史"},
            {"23-30": {"retry": "success"}},
            [("https://example.com/post", "retry", "23-30", "23-30")],
        )
        today = create_database(
            self.root, "news", "2026-11-07",
            {"retry": "有历史", "empty": "正常为空", "error": "首次失败", "unknown": "未运行", "skipped": "本轮缺失"},
            {"00-00": {"skipped": "success"},
             "00-30": {"retry": "failed", "empty": "success", "error": "failed"},
             "12-30": {"error": "success"}},
        )
        create_database(
            self.root, "rss", "2026-11-07", {"rx": "RSS"},
            {"00:29": {"rx": "success"}},
            [("https://example.com/rss", "rx", "00:29", "00:29")],
        )
        before = [path.read_bytes() for path in (today, yesterday)]
        view = self.report()
        self.assertEqual((view["hot"], view["rss"]), ("15/15", "10/11"))
        sources = {row["id"]: row for row in view["sources"]}
        self.assertEqual((sources["retry"]["state"], sources["retry"]["count"]), ("cached", 1))
        self.assertEqual(sources["retry"]["lastOk"], "2026-11-06T23:30:00+08:00")
        self.assertEqual((sources["empty"]["state"], sources["empty"]["count"]), ("live", 0))
        self.assertEqual((sources["error"]["state"], sources["error"]["count"]), ("error", None))
        self.assertEqual(sources["unknown"]["state"], "unavailable")
        self.assertEqual(sources["skipped"]["state"], "cached")
        self.assertEqual((sources["rx"]["state"], sources["rx"]["count"]), ("live", 1))
        self.assertEqual(before, [path.read_bytes() for path in (today, yesterday)])

    def test_mixed_clock_formats_use_actual_latest_minute_and_exclude_future(self):
        create_database(
            self.root, "news", "2026-11-07",
            {"current": "本轮源", "previous": "前轮源", "future": "未来源"},
            {"02:15": {"current": "success", "previous": "success"},
             "02-30": {"current": "success"},
             "02-55": {"future": "success"},
             "02:xx": {"future": "success"}},
            [("https://example.com/old", "current", "02:15", "02:15"),
             ("https://example.com/hot1", "current", "02-30", "02-30"),
             ("https://example.com/hot2", "current", "02:30", "02:30")],
        )
        result = {row["id"]: row for row in self.report("2026-11-07T02:31:00")["sources"]}
        self.assertEqual((result["current"]["state"], result["current"]["count"]), ("live", 2))
        self.assertEqual(result["current"]["lastOk"], "2026-11-07T02:30:00+08:00")
        self.assertEqual((result["previous"]["state"], result["previous"]["count"]), ("cached", 0))
        self.assertEqual(result["future"]["state"], "unavailable")

    def test_old_success_and_missing_tables(self):
        create_database(self.root, "rss", "2026-11-07", {"stale": "历史源"}, {"00:00": {"stale": "success"}})
        with closing(sqlite3.connect(self.root / "rss" / "2026-11-06.db")):
            pass
        view = self.report("2026-11-07T02:31:00")
        self.assertEqual([(row["id"], row["state"]) for row in view["sources"]], [("stale", "cached")])
        self.assertFalse((self.root / "news").exists())

    def test_extra_state_is_normalized_and_untrusted_metadata_dropped(self):
        sources = self.report(
            leads=[{"source": "leads", "label": "关键词订阅", "state": "retained",
                    "fetched": "2026-11-07T00:00:00+08:00", "matched": 0, "error": "raw secret"}],
            notices=[{"id": "notice", "label": "Bearer fake-secret", "state": "live",
                      "lastOk": "2026-11-06T16:00:00Z", "count": 1, "url": "http://private"},
                     {"id": "future", "label": "时钟错误", "state": "live",
                      "lastOk": "2026-11-07T00:45:00+08:00", "count": 4},
                     {"id": "https://example.com/secret", "label": "malformed"}],
        )["sources"]
        self.assertEqual(sources, [
            {"id": "leads", "label": "关键词订阅", "state": "cached",
             "lastOk": "2026-11-07T00:00:00+08:00", "count": 0, "group": "leads"},
            {"id": "notice", "label": "notice", "state": "live",
             "lastOk": "2026-11-07T00:00:00+08:00", "count": 1, "group": "official"},
            {"id": "future", "label": "时钟错误", "state": "unavailable",
             "lastOk": "", "count": None, "group": "official"},
        ])


if __name__ == "__main__":
    unittest.main()
