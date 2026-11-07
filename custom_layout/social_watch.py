"""Title-only public social watch. Never sends social data to model providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("Asia/Shanghai")
CACHE_NAME = ".social-cache-v1.json"
MIN_INTERVAL = timedelta(minutes=20)
FETCH_FRESH = timedelta(minutes=90)
TELEGRAM_RETENTION = timedelta(hours=6)
POST_MAX_AGE = timedelta(hours=72)
X_MAX_AGE = timedelta(minutes=150)
SOURCE_ITEMS = 12
MAX_BODY = 768 * 1024
CHANNELS = {
    "ithomenews": "IT之家资讯（Telegram 转发频道）",
    "zaihuapd": "在花科技圈（Telegram 公开频道）",
    "solidot": "Solidot（Telegram 非官方 RSS 转发）",
}
SOURCES = {
    **{f"tg-{name}": ("telegram", label, f"https://t.me/s/{name}")
       for name, label in CHANNELS.items()},
    "x-world": ("x", "X 全球趋势（第三方 Snaplytics）", "https://twitter-trends.snaplytics.io/"),
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
DISALLOWED = re.compile(
    r"://|www\.|[<>]|@@(?:DATA|ORIGINAL|SCRIPT|STYLE)@@|\b(?:ignore previous|reveal.{0,16}secret)\b|"
    r"忽略.{0,16}(?:指令|规则|提示)|(?:泄露|输出|读取).{0,18}(?:密钥|密码|api[ _-]?key)|"
    r"(?<!\d)1[3-9](?:[ .-]*\d){9}(?!\d)|(?<!\d)\d{17}[0-9X](?!\d)|"
    r"\b[A-Z0-9._+-]{1,64}@[A-Z0-9.-]+\.[A-Z]{2,16}\b|"
    r"私信领取|加群领|返佣|(?:API[_ -]?KEY|password)\s*[:=]",
    re.I,
)


def _clean_title(value):
    if not isinstance(value, str) or len(value) > 240:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = "".join(c if not unicodedata.category(c).startswith("C") else " " if c.isspace() else "" for c in value)
    value = " ".join(value.split())
    return value if 2 <= len(value) <= 180 and not DISALLOWED.search(value) else ""


def _stamp(value):
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(ZONE) if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


class _Page(HTMLParser):
    """Track scoped regions without interpreting scripts or following links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag not in VOID:
            self.stack.append((tag, attrs))
        self.enter(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                self.leave()
                break

    def handle_data(self, value):
        if not any(tag in ("script", "style", "template") for tag, _ in self.stack):
            self.content(value)

    def enter(self, tag, attrs):
        pass

    def leave(self):
        pass

    def content(self, value):
        pass


class _Telegram(_Page):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
        self.message = None
        self.text_depth = 0
        self.views_depth = 0
        self.matches = []
        self.post_pattern = re.compile(re.escape(channel) + r"/([1-9][0-9]{0,14})", re.I)

    def enter(self, tag, attrs):
        classes = attrs.get("class", "").split()
        if tag == "div" and "tgme_widget_message" in classes:
            post = self.post_pattern.fullmatch(attrs.get("data-post", ""))
            if post:
                self.message = {"depth": len(self.stack), "number": post[1], "parts": [], "length": 0}
        if self.message is None:
            return
        if tag == "div" and "tgme_widget_message_text" in classes:
            self.text_depth = len(self.stack)
        if not self.text_depth and tag == "span" and "tgme_widget_message_views" in classes:
            self.views_depth = len(self.stack)
        if self.text_depth and tag == "br":
            self.content("\n")
        if tag == "time" and len(self.stack) >= 2:
            parent, link = self.stack[-2]
            expected = f"https://t.me/{self.channel}/{self.message['number']}"
            if parent == "a" and "tgme_widget_message_date" in link.get("class", "").split() and link.get("href", "").lower() == expected:
                self.message["date"] = _stamp(attrs.get("datetime"))

    def leave(self):
        if self.text_depth > len(self.stack):
            self.text_depth = 0
        if self.views_depth > len(self.stack):
            self.views_depth = 0
        if self.message and self.message["depth"] > len(self.stack):
            self.matches.append(self.message)
            self.message = None

    def content(self, value):
        if self.text_depth and self.message is not None and self.message["length"] < 320:
            self.message["parts"].append(value[:320-self.message["length"]])
            self.message["length"] += len(value)
        if self.views_depth and self.message is not None:
            self.message["views"] = (self.message.get("views", "") + value)[:24]


def _view_count(raw):
    match = re.fullmatch(r"([0-9]{1,10}(?:\.[0-9]{1,3})?)([KM]?)", raw.strip(), re.I)
    if match and (match[2] or "." not in match[1]):
        count = int(Decimal(match[1]) * {"": 1, "K": 1000, "M": 1_000_000}[match[2].upper()])
        if 0 <= count <= 2_000_000_000:
            return count
    return None


def _query_from_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.netloc == "x.com" and url.path == "/search":
            queries = parse_qs(url.query, max_num_fields=8).get("q", [])
            if len(queries) == 1:
                return _clean_title(queries[0])
    except ValueError:
        pass
    return ""


class _Snaplytics(_Page):
    def __init__(self):
        super().__init__()
        self.panel_depth = 0
        self.header_depth = 0
        self.timestamp_depth = 0
        self.heading = ""
        self.topics = []
        self.completed = False

    def enter(self, tag, attrs):
        if not self.completed and attrs.get("id") == "snapshot-panel-0":
            self.panel_depth = len(self.stack)
        if not self.panel_depth:
            return
        if tag == "header":
            self.header_depth = len(self.stack)
        if self.header_depth and tag == "span" and "tabular-nums" in attrs.get("class", "").split():
            self.timestamp_depth = len(self.stack)
        if tag == "a":
            href = attrs.get("href")
            if isinstance(href, str) and href.startswith("https://x.com/search?"):
                self.topics.append(_query_from_url(href))

    def leave(self):
        if self.timestamp_depth > len(self.stack):
            self.timestamp_depth = 0
        if self.header_depth > len(self.stack):
            self.header_depth = 0
        if self.panel_depth > len(self.stack):
            self.panel_depth = 0
            self.completed = True

    def content(self, value):
        if self.timestamp_depth and len(self.heading) < 150:
            self.heading += value[:150]


def _record(title, url, stamp, source_id, rank=None, views=None):
    platform, label, source_url = SOURCES[source_id]
    item = {"platform": platform, "title": title, "url": url, "sourceLabel": label,
            "published": stamp.isoformat(timespec="seconds"), "sourceUrl": source_url}
    if rank is not None:
        item["rank"] = rank
    if views is not None:
        item["views"] = views
    return item


def _parse(source_id, page, now):
    platform, _, source_url = SOURCES[source_id]
    if platform == "telegram":
        channel = source_url.rsplit("/", 1)[-1]
        parser = _Telegram(channel)
        parser.feed(page)
        parser.close()
        if not parser.matches:
            raise ValueError("unrecognized Telegram channel page")
        items = []
        counters = {_view_count(entry.get("views", "")) for entry in parser.matches}
        # Uniform counts (some mirrors always show 1) do not distinguish activity.
        informative_counts = len({count for count in counters if count is not None}) >= 2
        for entry in parser.matches:
            stamp = entry.get("date")
            if not stamp or not timedelta(0) <= now - stamp <= POST_MAX_AGE:
                continue
            lines = [line.strip() for line in "".join(entry["parts"]).splitlines()
                     if line.strip() and line.strip() != "IT之家"]
            title = _clean_title(lines[0]) if lines else ""
            if title:
                views = _view_count(entry.get("views", "")) if informative_counts else None
                items.append(_record(title, f"https://t.me/{channel}/{entry['number']}", stamp, source_id, views=views))
        if not any(entry.get("date") and now - POST_MAX_AGE <= entry["date"] <= now for entry in parser.matches):
            raise ValueError("Telegram latest public messages are stale")
        def priority(item):
            stamp = _stamp(item["published"])
            hours = (now - stamp).total_seconds() / 3600
            return item.get("views", 0) / (6 + hours), stamp

        return now, sorted(items, key=priority, reverse=True)[:SOURCE_ITEMS]

    parser = _Snaplytics()
    parser.feed(page)
    parser.close()
    match = re.fullmatch(r"\s*(.+) UTC\s*", parser.heading)
    if not match:
        raise ValueError("missing X snapshot timestamp")
    observed = datetime.strptime(match[1], "%b %d, %Y %H:%M").replace(tzinfo=timezone.utc).astimezone(ZONE)
    if not timedelta(0) <= now - observed <= X_MAX_AGE:
        raise ValueError("stale X snapshot")
    items = []
    seen = set()
    for rank, title in enumerate(parser.topics, 1):
        if title and title.casefold() not in seen:
            seen.add(title.casefold())
            items.append(_record(title, "https://x.com/search?" + urlencode({"q": title}), observed, source_id, rank))
        if len(items) == SOURCE_ITEMS:
            break
    if not items:
        raise ValueError("X snapshot has no ranked topics")
    return observed, items


def _fetch(source_id, now):
    import requests
    endpoint = SOURCES[source_id][2]
    started = time.monotonic()
    with requests.get(endpoint, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                      allow_redirects=False, timeout=(3, 4), stream=True) as response:
        if response.status_code != 200:
            raise ValueError("social source HTTP error")
        chunks = bytearray()
        for chunk in response.iter_content(16384):
            if len(chunks) + len(chunk) > MAX_BODY or time.monotonic() - started > 8:
                raise ValueError("social response exceeded limits")
            chunks.extend(chunk)
    return _parse(source_id, chunks.decode("utf-8", errors="strict"), now)


def _read(root):
    try:
        path = root / CACHE_NAME
        if path.stat().st_size <= 80_000:
            state = json.loads(path.read_text())
            if (isinstance(state, dict) and isinstance(state.get("sources"), dict)
                    and isinstance(state.get("attempted"), str)
                    and _stamp(state["attempted"])):
                return state
    except (OSError, ValueError):
        pass
    return {"attempted": "", "sources": {}}


def _age(now, value):
    stamp = _stamp(value)
    return now - stamp if stamp and stamp <= now else timedelta.max


def _rebuild(source_id, cached, now):
    platform, _, source_url = SOURCES[source_id]
    age = X_MAX_AGE if platform == "x" else POST_MAX_AGE
    results = []
    for item in cached[:SOURCE_ITEMS] if isinstance(cached, list) else []:
        if not isinstance(item, dict):
            continue
        stamp = _stamp(item.get("published"))
        title, url = _clean_title(item.get("title")), item.get("url")
        if not stamp or not timedelta(0) <= now - stamp <= age or not title or not isinstance(url, str):
            continue
        views = None
        if platform == "x":
            rank = item.get("rank")
            if _query_from_url(url) != title or type(rank) is not int or not 1 <= rank <= 50:
                continue
            url = "https://x.com/search?" + urlencode({"q": title})
        else:
            channel = source_url.rsplit("/", 1)[-1]
            if not re.fullmatch(rf"https://t\.me/{re.escape(channel)}/[1-9][0-9]{{0,14}}", url):
                continue
            rank = None
            candidate = item.get("views")
            if type(candidate) is int and 0 <= candidate <= 2_000_000_000:
                views = candidate
        results.append(_record(title, url, stamp, source_id, rank, views))
    return results


def _view(state, now):
    items, sources = [], []
    for source_id, (platform, label, url) in SOURCES.items():
        entry = state["sources"].get(source_id, {})
        if not isinstance(entry, dict):
            entry = {}
        last_ok = _stamp(entry.get("lastOk"))
        age = _age(now, entry.get("lastOk"))
        max_age = X_MAX_AGE if platform == "x" else TELEGRAM_RETENTION
        if age <= max_age:
            flag = "cached" if entry.get("failed") or _age(now, state["attempted"]) > FETCH_FRESH else "live"
            current = _rebuild(source_id, entry.get("items"), now)
            if platform == "x" and not current:
                flag = "error"
            items.extend(dict(item, cached=(flag == "cached")) for item in current)
        else:
            flag, current = ("error" if state["attempted"] else "unavailable"), []
        sources.append({"id": source_id, "label": label, "state": flag,
                        "lastOk": last_ok.isoformat(timespec="seconds") if last_ok and last_ok <= now else "",
                        "count": len(current) if flag in ("cached", "live") else None, "url": url})
    return {"updated": state["attempted"], "items": items, "sources": sources}


def _refresh(root, now, previous):
    def collect(source_id):
        try:
            observed, items = _fetch(source_id, now)
            return source_id, {"lastOk": observed.isoformat(timespec="seconds"), "items": items}
        except Exception:
            old = previous["sources"].get(source_id, {})
            return source_id, {**(old if isinstance(old, dict) else {}), "failed": True}

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as workers:
        results = dict(workers.map(collect, SOURCES))
    state = {"attempted": now.isoformat(timespec="seconds"), "sources": results}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, root / CACHE_NAME)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return state


def get_social(root, now, *, refresh=True):
    root = Path(root)
    now = now.astimezone(ZONE) if now.tzinfo else now.replace(tzinfo=ZONE)
    state = _read(root)
    if refresh and _age(now, state.get("attempted")) >= MIN_INTERVAL:
        try:
            with (root / ".social-watch.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = _read(root)
                if _age(now, state.get("attempted")) >= MIN_INTERVAL:
                    state = _refresh(root, now, state)
        except OSError:
            pass
    return _view(state, now)
