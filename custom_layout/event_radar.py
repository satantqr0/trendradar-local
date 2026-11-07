"""Local headline comparison and a bounded, persistent history of public reports."""
from collections import Counter
from datetime import datetime, timedelta
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
CATEGORY_ORDER = ('local', 'society', 'tech', 'digital', 'other')
WATCHES = (
    ('local', '本地动态'),
    ('society', '社会民生'),
    ('tech', '科技'),
    ('digital', '数码'),
)
# Group sister sites and keep portals/indexes separate from originating outlets.
MEDIA = {
    'nfnews.com': ('南方日报 / 南方+', 'nfmedia'),
    'southcn.com': ('南方网', 'nfmedia'),
    'nfncb.cn': ('南方都市报', 'nfmedia'),
    'yzwb.net': ('扬子晚报', 'yangtse'),
    'chinanews.com.cn': ('中国新闻网', 'chinanews'),
    'cnr.cn': ('央广网', 'cnr'),
    'xinhuanet.com': ('新华社', 'xinhua'),
    'news.cn': ('新华社', 'xinhua'),
    'people.com.cn': ('人民网', 'people'),
    'thepaper.cn': ('澎湃新闻', 'thepaper'),
    '21jingji.com': ('21世纪经济报道', 'nfmedia'),
    'ithome.com': ('IT之家', 'ithome'),
    'solidot.org': ('Solidot', 'solidot'),
    'sspai.com': ('少数派', 'sspai'),
    'macrumors.com': ('MacRumors', 'macrumors'),
}
COPY = re.compile(r'转载|转发|综合自|据.{1,12}(?:报道|消息)|供稿|来源[:：]')
PHASES = {
    'correction': re.compile(r'辟谣|系谣言|不实|否认|澄清|撤销|不会|取消|延期|暂停'),
    'approval': re.compile(r'获批|已批复|正式批准'),
    'consultation': re.compile(r'批前公示|征求意见|拟审批|规划公示|拟建|公示'),
    'tender': re.compile(r'招标|中标|竞得|出让'),
    'build': re.compile(r'开工|动工|封顶|主体完工'),
    'launch': re.compile(r'开业|通车|投用|开通|发布|上市|开售|启用'),
}
PARTS = re.compile(r'[a-z]+[a-z0-9.\-]*|\d+(?:[./-]\d+)*|[\u3400-\u9fff]+')
DISTINCT = re.compile(r'\d+(?:[./-]\d+)*|\b(?:pro|max|ultra|plus|mini|lite|se)\b')
NUMBERED_PLACE = re.compile(r'[一二三四五六七八九十百]+(?:号线|号地块|期|路|栋|站|号通道)')
NOTICE_PATH = re.compile(r'/(?:[A-Za-z0-9_-]+/)*content/m?post_\d+\.html')
ARTICLE_PATH = re.compile(r'(?:post_\d+|article/|news/|/c/|/content/|/a/|/p/|\d{5,}|\.s?html?$)')
PORTALS = {'qq.com','sina.com.cn','sohu.com','163.com','baidu.com','toutiao.com','ifeng.com'}
TOPIC_TO_CATEGORY = {'local': 'local', 'digital': 'digital'}
MAX_INPUT = 650
MAX_HISTORY = 360
MAX_EVIDENCE = 12
MAX_FEED = 110


def write_json(path, body, mode):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(body, stream, ensure_ascii=False, separators=(',', ':'))
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def web_link(value):
    if not isinstance(value, str) or not value or len(value) > 2400:
        return ''
    if re.search(r'[\x00-\x20\x7f\\]', value):
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in ('https','http') or not host or parsed.netloc.lower() != host
                or not re.fullmatch(r'(?:[a-z0-9-]+\.)+[a-z]{2,24}', host)
                or host.endswith(('.local','.localhost','.internal','.test','.example','.lan','.onion'))):
            return ''
        if host.endswith('.gov.cn') or host == 'news.google.com' or host == 't.me':
            return urlunsplit(('https',host,parsed.path,'',''))
        return urlunsplit((parsed.scheme,host,parsed.path,parsed.query,''))
    except ValueError:
        return ''


def publication(value, now):
    if not isinstance(value, str) or not value:
        return None
    if re.fullmatch(r'\d\d-\d\d \d\d:\d\d', value):
        value = f'{now.year}-{value}'
        try:
            result = datetime.fromisoformat(value).replace(tzinfo=ZONE)
            if result > now + timedelta(days=100):
                result = result.replace(year=result.year - 1)
            return result
        except ValueError:
            return None
    try:
        result = datetime.fromisoformat(value.replace('Z','+00:00'))
        return result.astimezone(ZONE) if result.tzinfo else result.replace(tzinfo=ZONE)
    except ValueError:
        return None


def title_features(title):
    words = PARTS.findall(title.casefold())
    key = ''.join(words)
    grams = set()
    for word in words:
        if re.search(r'[\u3400-\u9fff]',word):
            grams.update(word[i:i+2] for i in range(len(word)-1))
        else:
            grams.add(word)
    phases = frozenset(name for name, pattern in PHASES.items() if pattern.search(key))
    distinct = {*DISTINCT.findall(' '.join(words)), *NUMBERED_PLACE.findall(key)}
    return key, grams, phases, frozenset(distinct)


def similar(a, b, *, lead=False):
    ak, ag, ap, an = a
    bk, bg, bp, bn = b
    if ak == bk:
        return len(ak) >= 9
    if min(len(ak),len(bk)) < 12 or min(len(ak),len(bk))/max(len(ak),len(bk)) < .65:
        return False
    if an and bn and an != bn:
        return False
    if not lead and (('correction' in ap) != ('correction' in bp) or ap and bp and not ap & bp):
        return False
    common = len(ag & bg)
    return common >= 7 and common / len(ag | bg) >= (.75 if lead else .65)


def authority_link(url):
    parsed = urlsplit(url)
    return bool(parsed.hostname and parsed.hostname.endswith('.gov.cn')
                and NOTICE_PATH.fullmatch(parsed.path))


def origin(url, publisher, title):
    parsed = urlsplit(url)
    host = parsed.hostname
    if authority_link(url):
        return publisher or host, host, 'authority'
    if host == 'news.google.com':
        return publisher or 'Google 新闻索引', '', 'index'
    if any(host == d or host.endswith('.'+d) for d in PORTALS) or COPY.search(title):
        return publisher or host, '', 'repost'
    for domain, (label,owner) in MEDIA.items():
        if (host == domain or host.endswith('.'+domain)) and ARTICLE_PATH.search(parsed.path):
            return label, owner, 'media'
    return publisher or host, '', 'other'


def observations(articles, notices, now, rules):
    for article in [*notices, *articles][:MAX_INPUT]:
        if article.get('cachedAt'):
            continue
        raw = article.get('_evidence') or [article]
        for row in raw[:6]:
            title = rules.clean_title(row.get('title',''))
            url = web_link(row.get('url'))
            if not url or len(title) < 9 or len(title) > 250 or rules.quarantine_headline(title):
                continue
            publisher = rules.clean_title(row.get('publisher',''))
            if rules.INJECT.search(publisher) or rules.PII.search(publisher):
                publisher = ''
            label, owner, kind = origin(url,publisher[:75],title)
            category = article.get('category','other')
            if category not in CATEGORY_ORDER:
                category = 'other'
            date_text = row.get('published')
            published = publication(date_text,now)
            date_only = isinstance(date_text,str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}',date_text))
            # Articles observed now may still be older reports; preserve the actual publication date.
            age = timedelta(days=30 if kind=='authority' else 7)
            if published and not now-age <= published <= now+timedelta(minutes=15):
                continue
            yield {'title':title,'url':url,'publisher':label,'owner':owner,
                   'published':published.isoformat() if published else '', 'dateOnly':date_only,
                   'official':kind=='authority','kind':kind,'category':category,'regions':article.get('_regions',[]),
                   'seen':now.isoformat(),'notice':article.get('kind')=='notice' and kind=='authority',
                   'newNotice':article.get('new') is True and article.get('kind')=='notice' and kind=='authority'}


def decorate(event, now):
    evidence = sorted(event['evidence'],key=lambda row: (
        {'authority':0,'media':1,'index':2,'repost':3,'other':4}[row['kind']],
        -(datetime.fromisoformat(row['published'] or row['seen']).timestamp()),
        row['url']))
    owners_by_title = {}
    for row in evidence:
        if row['owner']:
            copy_key = title_features(row['title'])[0]
            owners_by_title.setdefault(copy_key,set()).add(row['owner'])
    # A publisher with only shared headlines cannot raise the independent count.
    distinct = {next(iter(owners)) for owners in owners_by_title.values() if len(owners)==1}
    source_count = max(len(distinct),bool(owners_by_title))
    official = any(row['official'] for row in evidence)
    state = 'official' if official else 'multiple' if source_count>=2 else 'single' if source_count else 'unverified'
    recent = (now-timedelta(hours=1)).isoformat()
    new_notice = any(row.get('newNotice') and row['seen']>=recent for row in evidence)
    visible = []
    for row in evidence[:8]:
        public = {key:row[key] for key in ('title','url','publisher','published','official','kind')}
        if row.get('dateOnly') or (row.get('notice') and 'dateOnly' not in row):
            public['published'] = public['published'][:10]
        visible.append(public)
    return {'id':event['id'],'title':evidence[0]['title'],'category':event['category'],
            'state':state,'sources':source_count,'firstSeen':event['firstSeen'],'lastSeen':event['lastSeen'],
            'newNotice':new_notice,
            'watches':[event['category']] if event['category'] != 'other' else [],'evidence':visible}


def build_events(previous, articles, notices, now, rules):
    cutoff = (now-timedelta(days=7)).isoformat()
    events = [event for event in previous if event['category'] in CATEGORY_ORDER and event['lastSeen'] >= cutoff][:MAX_HISTORY]
    index, anchors = {}, {}
    for event in events:
        anchors[event['id']] = title_features(event['anchor'])
        for row in event['evidence']:
            index[row['url']] = event
    rows = list(observations(articles,notices,now,rules))
    rows.sort(key=lambda row:(CATEGORY_ORDER.index(row['category']),
                              not row['notice'],not row['official'],row['kind']=='index',row['url']))
    for row in rows:
        event = index.get(row['url'])
        if event:
            old = next(item for item in event['evidence'] if item['url']==row['url'])
            if old['title'] != row['title'] and not similar(title_features(old['title']),title_features(row['title'])):
                # An index may repurpose a URL. It cannot turn the old event into a new one.
                continue
            continue
        features = title_features(row['title'])
        moment = row['published'] or row['seen']
        eligible = []
        for candidate in events:
            if candidate['category'] != row['category']:
                continue
            regions = candidate.get('regions',[])
            if regions and row['regions'] and set(regions).isdisjoint(row['regions']):
                continue
            event_moment = datetime.fromisoformat(candidate['anchorTime'])
            if abs(datetime.fromisoformat(moment) - event_moment) > timedelta(days=3):
                continue
            if similar(features,anchors[candidate['id']]):
                eligible.append(candidate)
                if len(eligible)>1:
                    break
        if len(eligible)==1 and len(eligible[0]['evidence']) < MAX_EVIDENCE:
            event = eligible[0]
            event['evidence'].append(row)
            event['lastSeen'] = row['seen']
        elif eligible:
            continue
        else:
            if len(events) >= MAX_HISTORY * 2:
                break
            identifier = 'ev-'+sha256((row['url']+'\n'+features[0]).encode()).hexdigest()[:16]
            event = {'id':identifier,'category':row['category'],'regions':row['regions'],'anchor':row['title'],'anchorTime':moment,
                     'firstSeen':row['seen'],'lastSeen':row['seen'],'evidence':[row]}
            anchors[identifier] = features
            events.append(event)
        index[row['url']] = event

    def priority(event):
        tier = 0 if any(row['official'] for row in event['evidence']) else 1
        return (CATEGORY_ORDER.index(event['category']), tier,
                -datetime.fromisoformat(event['lastSeen']).timestamp(),event['id'])

    events.sort(key=priority)
    totals = Counter()
    kept = []
    # Reserve capacity for every monitored topic so a noisy city feed cannot evict all tech.
    for event in events:
        category = event['category']
        if totals[category] < (85 if category=='local' else 47):
            totals[category] += 1
            kept.append(event)
    return kept[:MAX_HISTORY]


def match_leads(items, events, rules):
    result = []
    features = {event['id']:title_features(event['title']) for event in events}
    for item in items[:45]:
        title = rules.clean_title(item.get('title',''))
        category = TOPIC_TO_CATEGORY.get(item.get('topic'))
        status = item.get('status')
        url = web_link(item.get('url'))
        if (not url or not category or status not in ('planning','rumor','discussion','correction')
                or not title or rules.PII.search(title) or rules.INJECT.search(title) or rules.SENSITIVE.search(title)):
            continue
        if urlsplit(url).hostname not in {'news.google.com','www.ithome.com','www.macrumors.com',
                                             'www.coolapk.com','s.weibo.com','tieba.baidu.com'}:
            continue
        cleaned = re.sub(r'^(?:网传|据传|据称|传闻|爆料|辟谣|澄清)[\s:：、！!]*','',title)
        options = [event for event in events if event['category']==category
                   and similar(title_features(cleaned),features[event['id']],lead=True)]
        if len(options)==1:
            event = options[0]
            result.append({'title':title,'status':status,'url':url,'category':category,
                           'eventId':event['id'],'eventTitle':event['title']})
    return result


def assemble(root, now, data, notices, health, social, rules, *, persist=True, focus=None):
    root = Path(root)
    history = root/'.event-radar-v1.json'
    lock = (root/'.event-radar.lock').open('a') if persist else None
    try:
        if lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
        previous = []
        if history.is_file() and history.stat().st_size <= 6*1024*1024:
            try:
                saved = json.loads(history.read_text())
                if saved.get('version')==1 and isinstance(saved.get('events'),list):
                    previous = saved['events'][:MAX_HISTORY]
            except (OSError, ValueError, AttributeError):
                pass
        try:
            events = build_events(previous,data['articles'],notices['articles'],now,rules)
        except (KeyError,TypeError,ValueError):
            if not previous:
                raise
            events = build_events([],data['articles'],notices['articles'],now,rules)
        if persist:
            write_json(history,{'version':1,'events':events},0o600)
        # Fair presentation across topics, with local and authority reports ranked first.
        caps = {'local':30,'society':18,'tech':18,'digital':18,'other':15}
        counts = Counter()
        visible = []
        for event in events:
            cat = event['category']
            if counts[cat] < caps[cat]:
                counts[cat] += 1
                visible.append(decorate(event,now))
        watches = [{'id':cat,'label':label,'count':counts[cat],'newCount':sum(
            item['category']==cat and datetime.fromisoformat(item['firstSeen'])>=now-timedelta(hours=1)
            for item in visible)} for cat,label in WATCHES]
        result = {'schema':1,'updated':now.isoformat(),'events':visible[:MAX_FEED],'watches':watches,
                  'health':health,'social':social,
                  'matchedLeads':match_leads(data['leads']['items'],visible,rules)}
        if focus is not None:
            result['focus'] = focus
        if persist:
            write_json(root/'intelligence.json',result,0o644)
        return result
    finally:
        if lock:
            lock.close()
