"""Read collector outcomes without opening a source or changing its data."""
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("Asia/Shanghai")
STALE_AFTER = timedelta(minutes=90)
SOURCE_ID = re.compile(r"[A-Za-z0-9_.-]{1,72}")
DAY_DB = re.compile(r"\d{4}-\d{2}-\d{2}\.db")
CLOCK = re.compile(r"(?:[01]\d|2[0-3])[:-][0-5]\d")
AGGREGATE = re.compile(r"\d{1,5}\s*/\s*\d{1,5}")
PRIVATE_TEXT = re.compile(
    r"://|[<>]|\bsk-(?:api-|cp-)?[a-z0-9_-]{12,}|\bbearer\s+|"
    r"\b(?:api[_ -]?key|authorization|password)\s*[:=]",
    re.IGNORECASE,
)
DB_SCHEMA = {
    "hot": ("news", "platforms", "crawl_records", "crawl_source_status", "news_items", "platform_id"),
    "rss": ("rss", "rss_feeds", "rss_crawl_records", "rss_crawl_status", "rss_items", "feed_id"),
}
STATE = {
    "live": "live", "ok": "live", "retained": "cached", "cached": "cached",
    "failed": "error", "error": "error", "unavailable": "unavailable",
}


def _label(value, source_id):
    if not isinstance(value, str) or len(value) > 96:
        return source_id
    value = unicodedata.normalize("NFKC", value)
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    value = " ".join(value.split())
    return value if value and not PRIVATE_TEXT.search(value) else source_id


def _id(value):
    return isinstance(value, str) and SOURCE_ID.fullmatch(value) and not PRIVATE_TEXT.search(value)


def _count(value):
    return value if type(value) is int and 0 <= value <= 100_000 else None


def _aggregate(value):
    if isinstance(value, str) and AGGREGATE.fullmatch(value.strip()):
        return "".join(value.split())
    return ""


def _days(folder, until):
    try:
        paths = sorted(folder.glob("????-??-??.db"), reverse=True)
    except OSError:
        return
    selected = 0
    for path in paths:
        if not DAY_DB.fullmatch(path.name):
            continue
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day <= until and path.is_file():
            yield day, path
            selected += 1
            if selected == 2:
                break


def _moment(day, clock):
    if isinstance(clock, str) and CLOCK.fullmatch(clock):
        normalized = clock.replace("-", ":")
        return datetime.fromisoformat(f"{day.isoformat()}T{normalized}").replace(tzinfo=ZONE)
    return None


def _read_day(path, schema, before):
    _, registry, records, statuses, items, source_column = schema
    labels, attempts, counts = [], [], {}
    latest = None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
            connection.execute("BEGIN")
            try:
                labels = connection.execute(
                    f"SELECT id,name FROM {registry} WHERE is_active=1"
                ).fetchall()
            except sqlite3.Error:
                pass
            # Hot stores HH-MM and RSS stores HH:MM. Compare canonical clock text
            # so mixed historical formats within the same hour still sort correctly.
            clock = "REPLACE(crawl.crawl_time,'-',':')"
            cutoff = f"{clock} GLOB '[0-2][0-9]:[0-5][0-9]' AND {clock}<=?"
            latest = connection.execute(
                f"SELECT MAX({clock}) FROM {records} AS crawl WHERE {cutoff}", (before,)
            ).fetchone()[0]
            # At most two small daily status tables are aggregated, with a separate
            # index-backed count of the rows persisted during each last success.
            history = (
                f"FROM {statuses} AS status JOIN {records} AS crawl ON crawl.id=status.crawl_record_id "
                f"WHERE {cutoff}"
            )
            attempts = connection.execute(
                f"SELECT status.{source_column},status.status,MAX({clock}) "
                f"{history} GROUP BY status.{source_column},status.status",
                (before,),
            ).fetchall()
            try:
                counts = dict(connection.execute(
                    f"SELECT success.source,COUNT(item.id) FROM ("
                    f"SELECT status.{source_column} AS source,MAX({clock}) AS clock "
                    f"{history} AND status.status='success' GROUP BY status.{source_column}"
                    f") AS success LEFT JOIN {items} AS item "
                    f"ON item.{source_column}=success.source "
                    "AND item.last_crawl_time IN (success.clock, REPLACE(success.clock,':','-')) "
                    "GROUP BY success.source",
                    (before,),
                ))
            except sqlite3.Error:
                pass
    except (OSError, sqlite3.Error):
        pass
    return labels, latest, attempts, counts


def _stored_sources(root, group, now):
    schema = DB_SCHEMA[group]
    labels, successes, failures = {}, {}, {}
    latest_run = None
    for day, path in _days(root / schema[0], now.date()):
        cutoff = now.strftime("%H:%M") if day == now.date() else "23:59"
        registry, latest, attempts, counts = _read_day(path, schema, cutoff)
        latest_run = latest_run or _moment(day, latest)
        for source_id, name in registry:
            if _id(source_id) and (source_id not in labels or labels[source_id] == source_id):
                labels[source_id] = _label(name, source_id)
        for source_id, status, clock in attempts:
            attempted = _moment(day, clock)
            if not attempted or not _id(source_id):
                continue
            labels.setdefault(source_id, source_id)
            if status == "success":
                successes.setdefault(source_id, (attempted, _count(counts.get(source_id))))
            elif status == "failed":
                failures.setdefault(source_id, attempted)
    for source_id, label in labels.items():
        last_ok, count = successes.get(source_id, (None, None))
        last_error = failures.get(source_id)
        if last_ok is not None:
            live = last_ok == latest_run and now - last_ok <= STALE_AFTER and (not last_error or last_error < last_ok)
            state = "live" if live else "cached"
        else:
            state = "error" if last_error else "unavailable"
        yield {
            "id": source_id, "label": label, "state": state,
            "lastOk": last_ok.isoformat() if last_ok else "", "count": count, "group": group,
        }


def _extra_sources(rows, group, now):
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = row.get("id") or row.get("source")
        if not _id(source_id):
            continue
        stamp = row.get("lastOk") or row.get("fetched")
        last_ok = ""
        if isinstance(stamp, str) and 16 <= len(stamp) <= 48:
            try:
                value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                value = value.astimezone(ZONE) if value.tzinfo else value.replace(tzinfo=ZONE)
                if value <= now:
                    last_ok = value.isoformat(timespec="seconds")
            except (ValueError, OverflowError):
                pass
        status = row.get("state")
        state = STATE.get(status, "unavailable") if isinstance(status, str) else "unavailable"
        if state in ("live", "cached") and not last_ok:
            state = "unavailable"
        count = _count(row.get("count", row.get("matched"))) if last_ok else None
        yield {
            "id": source_id, "label": _label(row.get("label"), source_id), "state": state,
            "lastOk": last_ok, "count": count, "group": group,
        }


def source_health(root: Path, now: datetime, meta: dict, lead_sources: list, notice_sources: list):
    """Return source availability, last successful crawl and persisted item count."""
    root = Path(root)
    now = now.astimezone(ZONE) if now.tzinfo else now.replace(tzinfo=ZONE)
    sources = [
        *_stored_sources(root, "hot", now),
        *_stored_sources(root, "rss", now),
        *_extra_sources(lead_sources, "leads", now),
        *_extra_sources(notice_sources, "official", now),
    ]
    return {"hot": _aggregate(meta.get("热榜平台")), "rss": _aggregate(meta.get("RSS 源")), "sources": sources}
