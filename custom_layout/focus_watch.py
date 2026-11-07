"""Persisted watch terms and bounded, optional public news discovery."""
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import fcntl
import json
from pathlib import Path
import re
from unicodedata import normalize
from urllib.parse import urlencode, urlsplit
import time
import xml.etree.ElementTree as ET

from importlib.util import spec_from_file_location, module_from_spec
import requests

_events_spec = spec_from_file_location('_focus_event_helpers',Path(__file__).with_name('event_radar.py'))
_events = module_from_spec(_events_spec)
_events_spec.loader.exec_module(_events)
web_link, write_json = _events.web_link, _events.write_json

DEFAULTS = {'keywords':[], 'regions':[], 'discover':False}
STORE = Path('/app/focus_private')
FIELDS = {'keywords':10,'regions':8}
DISALLOWED = re.compile(r'\bsk-(?:cp-|api-)?[a-z0-9_-]{12,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dx](?!\d)', re.I)
MAX_RESULTS = 72


class Conflict(ValueError):
    pass


def settings_values(data):
    if not isinstance(data,dict) or set(data) != {'keywords','regions','discover'} or type(data['discover']) is not bool:
        raise ValueError('设置格式无效')
    result = {'discover':data['discover']}
    for field, limit in FIELDS.items():
        raw = data[field]
        name = '关键词' if field == 'keywords' else '区域'
        if not isinstance(raw,list) or len(raw)>limit:
            raise ValueError(f'{name}最多 {limit} 项')
        terms = []
        seen = set()
        for term in raw:
            if not isinstance(term,str) or len(term)>80 or any(not c.isprintable() for c in term):
                raise ValueError(f'{name}包含不支持的字符')
            term = ' '.join(normalize('NFKC',term).split())
            if not (2<=len(term)<=36 and (sum(c.isalnum() for c in term)>=2 or bool(re.fullmatch(r'[A-Za-z](?:#|\+\+)',term)))
                    and all(c.isalnum() or c in ' -._+#·&' for c in term) and not DISALLOWED.search(term)):
                raise ValueError(f'{name}每项须为 2–36 个字符，可用文字、数字及常用连接符')
            key = term.casefold()
            if key not in seen:
                terms.append(term)
                seen.add(key)
        result[field] = terms
    if result['discover'] and not (result['keywords'] or result['regions']):
        raise ValueError('开启扩展采集时，至少添加一个关键词或区域')
    return result


def load_settings(directory=STORE):
    file = directory/'settings.json'
    try:
        with file.open(encoding='utf-8') as stream:
            body = json.loads(stream.read(12000))
    except FileNotFoundError:
        return {'revision':0,**DEFAULTS}
    revision = body.pop('revision',None) if isinstance(body,dict) else None
    if type(revision) is not int or revision<1:
        raise ValueError('关注配置无效')
    return {'revision':revision,**settings_values(body)}


def save_settings(directory, revision, data):
    values = settings_values(data)
    with (directory/'write.lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        current = load_settings(directory)
        if type(revision) is not int or current['revision']!=revision:
            raise Conflict('设置已被其他页面更新，请刷新后再试')
        if revision and all(current[key]==value for key,value in values.items()):
            return current
        saved = {'revision':revision+1,**values}
        write_json(directory/'settings.json',saved,0o600)
        return saved


def matcher(settings):
    patterns = {}
    for field in FIELDS:
        patterns[field] = []
        for term in settings[field]:
            value = re.escape(term.casefold())
            if term.isascii():
                value = rf'(?<![a-z0-9]){value}(?![a-z0-9])'
            patterns[field].append((term,re.compile(value)))

    def match(title):
        title = normalize('NFKC',title).casefold()
        return {field:[term for term, pattern in group if pattern.search(title)]
                for field,group in patterns.items()}
    return match


def search_url(settings):
    # A single OR query per scheduled cycle. Quoting treats user entries as literal phrases.
    terms = list(dict.fromkeys(settings['regions']+settings['keywords']))
    q = ' OR '.join('"'+term+'"' for term in terms)
    return 'https://news.google.com/rss/search?'+urlencode(
        {'q':'('+q+') when:7d','hl':'zh-CN','gl':'CN','ceid':'CN:zh-Hans'})


def fetch(settings):
    started = time.monotonic()
    with requests.get(search_url(settings), headers={'User-Agent':'TrendRadar-LAN/1.0 public news watch',
                      'Accept':'application/rss+xml, application/xml'},timeout=(3,4),allow_redirects=False,stream=True) as response:
        if response.status_code!=200:
            raise ValueError('RSS request failed')
        body = bytearray()
        for part in response.iter_content(16384):
            if time.monotonic()-started>8 or len(body)+len(part)>600_000:
                raise ValueError('RSS response exceeds limit')
            body.extend(part)
    return body.decode('utf-8-sig')


def rss_link(url):
    safe = web_link(url)
    return safe if safe and urlsplit(safe).hostname=='news.google.com' and re.fullmatch(
        r'/rss/articles/[A-Za-z0-9_-]{20,2000}',urlsplit(safe).path) else ''


def parse_feed(xml, now, match, rules):
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)',xml,re.I):
        raise ValueError('unsafe XML')
    root = ET.fromstring(xml)
    channel = root.find('channel')
    if root.tag!='rss' or channel is None:
        raise ValueError('unsupported RSS')
    rows = []
    for item in channel.findall('item')[:120]:
        url = rss_link(item.findtext('link'))
        title = rules.clean_title(item.findtext('title'))
        source = rules.clean_title(item.findtext('source'))
        if source and title.endswith(' - '+source):
            title = title[:-len(source)-3]
        try:
            published = parsedate_to_datetime(item.findtext('pubDate')).astimezone(now.tzinfo)
        except (ValueError,TypeError,AttributeError,OverflowError):
            continue
        if (not url or not title or len(title)>220 or rules.quarantine_headline(title)
                or not now-timedelta(days=7)<=published<=now+timedelta(minutes=10)
                or not any(match(title).values())):
            continue
        rows.append({'title':title,'url':url,'published':published.isoformat(),
                     'publisher':source if source and not rules.ai_blocked(source) else 'Google 新闻索引',
                     'kind':'index'})
    return rows


def indexed(root, now, settings, match, rules, refresh, fetcher=fetch):
    if not settings['discover']:
        return [],'off'
    path = root/'.focus-search-v1.json'
    slot = now.replace(minute=now.minute//30*30,second=0,microsecond=0).isoformat()

    def read():
        try:
            with path.open(encoding='utf-8') as stream:
                saved = json.loads(stream.read(400000))
            return saved if saved['revision']==settings['revision'] else {}
        except (OSError,ValueError,TypeError,KeyError):
            return {}

    def view(saved):
        try:
            age = now-datetime.fromisoformat(saved['lastOk'])
            if timedelta(0)<=age<=timedelta(hours=6):
                return saved['rows'], ('live' if saved['slot']==slot and not saved['failed'] else 'cached')
        except (ValueError,TypeError,KeyError):
            pass
        return [],'unavailable' if saved else 'pending'

    if not refresh:
        return view(read())
    with (root/'.focus-search.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return view(read())
        saved = read()
        if saved.get('slot')!=slot:
            try:
                rows = parse_feed(fetcher(settings),now,match,rules)
                saved = {'lastOk':now.isoformat(),'rows':rows,'failed':False}
            except (requests.RequestException, ET.ParseError, OSError, ValueError):
                saved['failed'] = True
            saved.update(revision=settings['revision'],slot=slot)
            write_json(path,saved,0o600)
        return view(saved)


def assemble(root, now, settings, articles, notices, rules, *, refresh, fetcher=fetch):
    match = matcher(settings)
    extra,status = indexed(root,now,settings,match,rules,refresh,fetcher)
    entries = []
    seen_urls, seen_titles = set(), set()
    rows = [(row,False) for row in (*notices,*articles)] + [(row,status=='cached') for row in extra]
    for row, index_cached in rows:
        title = rules.clean_title(row.get('title'))
        url = web_link(row.get('url'))
        if not url or not title or rules.quarantine_headline(title):
            continue
        kind = row.get('kind','news')
        hits = match(title)
        if not any(hits.values()):
            continue
        fingerprint = ''.join(c for c in title.casefold() if c.isalnum())
        if url in seen_urls or fingerprint in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(fingerprint)
        if urlsplit(url).hostname=='news.google.com':
            kind = 'index'
        source = row.get('publisher') or ' · '.join(row.get('sources',[])[:2])
        entries.append({'title':title,'url':url,'kind':kind,'publisher':source,
                        'published':row.get('published',''),'cached':bool(row.get('cachedAt') or index_cached),
                        **hits})
    def rank(row):
        return (
            not (row['keywords'] and row['regions']), row['cached'], row['kind']=='index',
            min((settings['keywords'].index(k) for k in row['keywords']),default=99),
            min((settings['regions'].index(k) for k in row['regions']),default=99),
        )
    entries.sort(key=lambda row:_events.publication(row['published'],now) or datetime.min.replace(tzinfo=now.tzinfo),reverse=True)
    entries.sort(key=rank)
    return {'revision':settings['revision'],'settings':settings,'updated':now.isoformat(),
            'status':status,'count':len(entries),'items':entries[:MAX_RESULTS]}
