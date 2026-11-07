"""Local presentation layer for pinned TrendRadar 6.11.0; collection is unchanged."""
from html.parser import HTMLParser
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
import base64, json, re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os, tempfile, logging
from importlib.util import spec_from_file_location, module_from_spec

VERSION = '2026-11-07.2'
_leads_spec = spec_from_file_location('_trendradar_public_leads', Path(__file__).with_name('leads.py'))
_leads = module_from_spec(_leads_spec)
_leads_spec.loader.exec_module(_leads)


def _load_extension(name):
    spec = spec_from_file_location('_trendradar_'+name, Path(__file__).with_name(name+'.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    _events = _load_extension('event_radar')
    _notices = _load_extension('notice_watch')
    _health = _load_extension('source_health')
    _social = _load_extension('social_watch')
    _focus = _load_extension('focus_watch')
except (ImportError, OSError):
    logging.exception('Optional intelligence extension unavailable')
    _events = None


def _intel(root, data, refresh, settings):
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    notices = _notices.collect(data['articles'],now,_leads.rules,_events)
    try:
        social = _social.get_social(root,now,refresh=refresh)
    except (OSError, ValueError):
        logging.exception('Optional social collector failed')
        social = {'items':[],'sources':[],'updated':''}
    health = _health.source_health(root,now,data['meta'],data['leads']['sources'],notices['sources'])
    focus = _focus.assemble(root,now,settings,data['articles'],notices['articles'],_leads.rules,refresh=refresh)
    return _events.assemble(root,now,data,notices,health,social,_leads.rules,persist=refresh,focus=focus)


def _settings():
    if _events is None:
        return None
    try:
        return _focus.load_settings()
    except (OSError,ValueError):
        logging.exception('Focus settings are unavailable')
        return {'revision':0,**_focus.DEFAULTS}


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
    def has(self, cls): return cls in self.attrs.get('class', '').split()
    def all(self, cls):
        for child in self.children:
            if isinstance(child, Node):
                if child.has(cls): yield child
                yield from child.all(cls)
    def first(self, cls): return next(self.all(cls), None)
    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()
class Tree(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.root = Node(); self.current = self.root
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current); self.current.children.append(node)
        if tag == 'br': node.children.append('\n')
        if tag not in self.VOID: self.current = node
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs); self.handle_endtag(tag)
    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag: self.current = node.parent; break
            node = node.parent
    def handle_data(self, text): self.current.children.append(text)

def text(node, cls):
    found = node.first(cls)
    return found.text() if found else ''

def extract(native, matches=None):
    tree = Tree(); tree.feed(native); root = tree.root
    meta = {text(n, 'info-label'): text(n, 'info-value') for n in root.all('info-item')}
    extra = {}
    def nodes(node):
        for child in node.children:
            if isinstance(child,Node):
                yield child
                yield from nodes(child)
    for node in nodes(root):
        if node.tag=='meta' and node.attrs.get('name','').startswith('trendradar-ai-'):
            extra[node.attrs['name']]=node.attrs.get('content','')
    records, by_title, by_url = [], {}, {}
    blocks = [('standalone-group','standalone-name','news-item'), ('word-group','word-name','news-item'), ('feed-group','feed-name','rss-item')]
    raw_count = 0
    for group_cls, label_cls, item_cls in blocks:
        for group in root.all(group_cls):
            label = text(group, label_cls)
            for item in group.all(item_cls):
                title = text(item, 'news-title') or text(item, 'rss-title')
                if not title: continue
                raw_count += 1
                link = item.first('news-link') or item.first('rss-link')
                url = link.attrs.get('href', '') if link else ''
                try:
                    if urlsplit(url).scheme not in ('http','https'): url = ''
                except ValueError:
                    url = ''
                source = text(item, 'source-name') or text(item, 'rss-author')
                standalone = group_cls == 'standalone-group'
                is_rss = item_cls == 'rss-item' or (standalone and ('新闻' in label or '政务' in label or 'IT之家' in label or '中新网' in label))
                if standalone and not source: source = label
                clean = title
                # Search feeds append the publisher to the headline. Keep it as source metadata.
                if (url and urlsplit(url).hostname=='news.google.com') and ' - ' in title:
                    clean, publisher = title.rsplit(' - ', 1)
                    source = publisher
                key = re.sub(r'\s+', '', clean).casefold()
                rec = by_title.get(key) or (by_url.get(url) if url else None)
                if rec is None:
                    rec = {'title': clean, 'url': url, 'sources': [], 'groups': [], 'published': '', 'observed': '', 'new': False, 'origins': [], '_evidence': []}
                    records.append(rec)
                by_title[key] = rec
                if url: by_url[url] = rec
                if not rec['url'] and url: rec['url'] = url
                for value, field in [(source,'sources'),(label,'groups'),('订阅' if is_rss else '热榜','origins')]:
                    if value and value not in rec[field]: rec[field].append(value)
                stamp = text(item, 'rss-time') or text(item, 'time-info')
                if is_rss and stamp:
                    if standalone and re.fullmatch(r'\d{2}-\d{2} \d{2}:\d{2}', stamp):
                        # Upstream standalone rendering prints feedparser's naive UTC directly.
                        stamp = (datetime.strptime('2000-'+stamp, '%Y-%m-%d %H:%M') + timedelta(hours=8)).strftime('%m-%d %H:%M')
                    if not rec['published'] or not standalone: rec['published'] = stamp
                elif stamp and not rec['observed']: rec['observed'] = stamp
                if url and len(rec['_evidence']) < 6:
                    evidence = {'title':clean,'url':url,'publisher':source,'published':stamp if is_rss else ''}
                    if evidence not in rec['_evidence']:
                        rec['_evidence'].append(evidence)
                rec['new'] = rec['new'] or item.has('new')
    digital = re.compile(r'数码|手机|耳机|相机|显卡|显示器|平板|笔记本|路由器|固态硬盘|Windows|iPhone|Galaxy|MacBook|Android|游戏掌机|词典笔|头显|WATCH|手表|HarmonyOS|鸿蒙|网线|会员年卡|微信', re.I)
    for i, rec in enumerate(records):
        title, groups = rec['title'], ' '.join(rec['groups'])
        sources = ' '.join(rec['sources'])
        rec['sources'] = [urlsplit(v).netloc if v.startswith(('https://','http://')) else v for v in rec['sources']]
        rec['_regions'] = matches(title)['regions'] if matches else []
        if rec['_regions']: cat = 'local'
        elif digital.search(title) or '数码产品' in groups or any(x in sources for x in ['少数派','小众软件','酷安']): cat = 'digital'
        elif re.search(r'WSBK|机车|燃油轿车|汽车预售|第三代秦|Ray7', title, re.I): cat = 'other'
        elif any(x in groups for x in ['科技前沿','DeepSeek','OpenAI','Claude','AI 相关','微软 / 谷歌','字节跳动','自动驾驶']) or any(x in sources for x in ['Hacker News','Solidot']): cat = 'tech'
        elif '社会民生' in groups or '社会民生' in sources: cat = 'society'
        else: cat = 'other'
        rec['category'], rec['id'] = cat, i
        rec['official'] = bool(_events and any(
            _events.authority_link(_events.web_link(row['url'])) for row in rec['_evidence']))
    ai = [{'title': text(n,'ai-block-title'), 'body': text(n,'ai-block-content')} for n in root.all('ai-block')]
    warning = text(root,'ai-warning') or text(root,'ai-info')
    errors = [n.text() for n in root.all('error-item')]
    return {'version': VERSION, 'meta': meta, 'articles': records, 'ai': ai, 'aiWarning': warning, 'errors': errors, 'rawCount': raw_count, 'aiTime':extra.get('trendradar-ai-time',''), 'aiIsCached':extra.get('trendradar-ai-cached')=='true', 'aiCacheReason':extra.get('trendradar-ai-cache-reason',''),
            'aiModel':extra.get('trendradar-ai-model') or ('DeepSeek Flash' if ai else ''),
            'aiActiveModel':extra.get('trendradar-ai-active-model','')}

def prepare_data(native, cache_path=None, matches=None):
    data = extract(native,matches)
    data.update(retainedCount=0, aiTime=data.get('aiTime') or data['meta'].get('生成时间',''), aiIsCached=data.get('aiIsCached',False), aiCacheReason=data.get('aiCacheReason',''))
    if not cache_path: return data
    cache_path = Path(cache_path)
    now = datetime.now(timezone.utc)
    rss_health = data['meta'].get('RSS 源','').split('/')
    complete = len(rss_health)==2 and rss_health[0]==rss_health[1] and rss_health[0].isdigit() and int(rss_health[0])>0
    try:
        if complete:
            saved = {'saved_at': now.isoformat(), 'data': {
                **data,'articles':[{k:v for k,v in article.items() if k not in ('_evidence','_regions')} for article in data['articles']]
            }}
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=cache_path.parent,delete=False) as f:
                json.dump(saved,f,ensure_ascii=False); temporary=f.name
            os.replace(temporary,cache_path)
        elif cache_path.exists():
            saved=json.loads(cache_path.read_text(encoding='utf-8'))
            age=now-datetime.fromisoformat(saved['saved_at'])
            if age.total_seconds()<0 or age>timedelta(days=3): return data
            cached=saved['data']; existing={a['url'] for a in data['articles'] if a['url']}
            titles={a['title'] for a in data['articles']}
            for previous in cached['articles']:
                if not matches or not matches(previous['title'])['regions']: continue
                if previous['url'] in existing or previous['title'] in titles: continue
                article=dict(previous,category='local'); article['cachedAt']=cached['meta'].get('生成时间','')
                data['articles'].append(article); data['retainedCount']+=1
                existing.add(article['url']); titles.add(article['title'])
            newer_cached_analysis = data['aiIsCached'] and cached.get('aiTime','') > data['aiTime']
            if data['retainedCount'] and cached.get('ai') and (not data['ai'] or newer_cached_analysis):
                data['ai']=cached['ai']; data['aiModel']=cached.get('aiModel') or 'DeepSeek Flash'; data['aiTime']=cached.get('aiTime') or cached['meta'].get('生成时间',''); data['aiIsCached']=True; data['aiCacheReason']='本轮本地检索中断，保留最近完整分析'
    except (OSError, ValueError, KeyError, TypeError):
        logging.exception('Could not retain the last complete local news view')
    return data

def relayout(native, cache_path=None, *, lead_snapshot=None, intelligence_root=None, refresh_intelligence=True):
    settings = _settings()
    matches = _focus.matcher(settings) if settings else None
    data = prepare_data(native, cache_path, matches)
    if not data['articles'] and not data['ai']: return native
    # Existing obvious rumor/injection markers stay out of normal article lists and Markdown.
    # The original upstream archive is deliberately kept separate.
    data['articles'] = [a for a in data['articles'] if not _leads.rules.quarantine_headline(a['title'])]
    if lead_snapshot is None:
        try:
            lead_snapshot = _leads.for_report(Path(cache_path).parent) if cache_path else {'items':[],'sources':[],'updated':''}
        except Exception:
            logging.exception('Lead collection failed; normal news is unaffected')
            lead_snapshot = {'items':[],'sources':[],'updated':''}
    data['leads'] = lead_snapshot
    if _events and (intelligence_root is not None or cache_path):
        root = Path(intelligence_root) if intelligence_root is not None else Path(cache_path).parent
        try:
            data['intelligence'] = _intel(root,data,refresh_intelligence,settings)
        except Exception:
            logging.exception('Event radar failed; normal news and leads remain available')
    for article in data['articles']:
        article.pop('_evidence',None)
        article.pop('_regions',None)
    folder = Path(__file__).parent
    template = (folder/'page.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False, separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    values = {'STYLE':(folder/'style.css').read_text(), 'DATA':payload,
              'SCRIPT':(folder/'app.js').read_text(), 'ORIGINAL':base64.b64encode(native.encode()).decode()}
    # Substitute exactly once. A title containing a template marker must not cause a second pass.
    return re.sub(r'@@(STYLE|DATA|SCRIPT|ORIGINAL)@@',lambda match:values[match[1]],template)

if __name__ == '__main__':
    import sys
    source, target = map(Path, sys.argv[1:3])
    result = relayout(source.read_text(encoding='utf-8'))
    target.write_text(result, encoding='utf-8')
    data = extract(source.read_text(encoding='utf-8'))
    from collections import Counter
    print(json.dumps({'unique':len(data['articles']), 'before':data['rawCount'], 'categories':dict(Counter(a['category'] for a in data['articles'])), 'aiBlocks':len(data['ai'])}, ensure_ascii=False))
