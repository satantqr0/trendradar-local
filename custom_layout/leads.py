"""Isolated public headlines for the local reader. No API keys, model calls or news DB writes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs, urlunsplit
from zoneinfo import ZoneInfo
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from importlib.util import spec_from_file_location, module_from_spec
_rules_spec = spec_from_file_location('_trendradar_lead_rules', Path(__file__).with_name('lead_rules.py'))
rules = module_from_spec(_rules_spec)
_rules_spec.loader.exec_module(rules)
classify, clean_title = rules.classify, rules.clean_title

ZONE = ZoneInfo('Asia/Shanghai')
CACHE = '.leads-cache-v1.json'
SCHEMA = 1
LOCAL = {
    'weibo': ('微博热搜', 'news', 'weibo', 'community'),
    'tieba': ('贴吧热议', 'news', 'tieba', 'community'),
    'coolapk': ('酷安热帖', 'news', 'coolapk', 'digital'),
    'ithome': ('IT之家订阅', 'rss', 'ithome', 'digital'),
}
def google(q):
    return 'https://news.google.com/rss/search?'+urlencode({'q':q,'hl':'zh-CN','gl':'CN','ceid':'CN:zh-Hans'})
REMOTE = {
    'hupu': ('虎扑热帖（NewsNow）','json','community','https://newsnow.busiyi.world/api/s?id=hupu&latest'),
    'pcbeta': ('远景论坛（NewsNow）','json','digital','https://newsnow.busiyi.world/api/s?id=pcbeta-windows11&latest'),
    'macrumors': ('MacRumors','rss','macrumors','https://feeds.macrumors.com/MacRumors-All'),
    'ent-rss': ('文娱风声搜索','rss','ent',google('(演唱会 OR 综艺 OR 剧集 OR 影视 OR 电影) (网传 OR 爆料 OR 路透 OR 传闻 OR 阵容 OR 选角) when:3d')),
}
SOURCES = {k:(v[0],v[3]) for k,v in LOCAL.items()}|{k:(v[0],v[2]) for k,v in REMOTE.items()}
INDEXED = {'ent-rss'}
PUBLISHERS = (
    'nfnews.com','southcn.com','nfncb.cn','cnr.cn','chinanews.com.cn',
    'xinhuanet.com','news.cn','people.com.cn','thepaper.cn','ifeng.com','sina.com.cn','qq.com','163.com',
    'sohu.com','bjnews.com.cn','bjd.com.cn','gz-cmc.com','21jingji.com','hk01.com','yangchengnews.com','yzwb.net','huanqiu.com'
)
LINK_RULES = {
    'weibo': ('s.weibo.com',r'/weibo','q'),
    'tieba': ('tieba.baidu.com',r'/hottopic/browse/hottopic','topic_id'),
    'coolapk': ('www.coolapk.com',r'/feed/[0-9]{3,20}',None),
    'ithome': ('www.ithome.com',r'/[0-9]/[0-9]{3}/[0-9]{3}\.htm',None),
    'hupu': ('bbs.hupu.com',r'/[0-9]{3,20}\.html',None),
    'pcbeta': ('bbs.pcbeta.com',r'/viewthread-[0-9]+-[0-9]+-[0-9]+\.html',None),
    'macrumors': ('www.macrumors.com',r'/20[0-9]{2}/[0-9]{2}/[0-9]{2}/[a-z0-9-]+/?',None),
}
ORDER = ['local','ent','digital','community']
WEIGHT = {'planning':0,'correction':1,'rumor':2,'discussion':3}

def stamp(value):
    if isinstance(value,datetime):
        d=value
    elif not isinstance(value,str) or not value:
        return None
    else:
        try:d=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:
            try:d=parsedate_to_datetime(value)
            except (TypeError,ValueError,OverflowError):return None
    return d.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZONE) if not d.tzinfo else d.astimezone(ZONE)

def safe_link(source, raw):
    if not isinstance(raw,str) or len(raw)>2500 or re.search(r'[\x00-\x20\x7f\\]',raw):
        return ''
    host,pattern,qkey = LINK_RULES.get(source,('news.google.com',r'/rss/articles/[A-Za-z0-9_-]{24,2000}',None))
    if source not in LINK_RULES and source not in INDEXED:
        return ''
    try:
        p=urlsplit(raw)
        if p.scheme!='https' or p.netloc.lower()!=host or not re.fullmatch(pattern,p.path):
            return ''
        query=''
        if qkey:
            values=parse_qs(p.query,max_num_fields=24).get(qkey,[])
            if len(values)!=1 or len(values[0])>300 or (qkey=='topic_id' and not re.fullmatch(r'\d{3,20}',values[0])):
                return ''
            if qkey=='q' and (not clean_title(values[0]) or re.search(r'://|[<>{}]',values[0])):
                return ''
            query=urlencode({qkey:values[0]})
        return urlunsplit(('https',host,p.path,query,''))
    except (ValueError,UnicodeError):
        return ''

def publisher_allowed(url):
    if not url or len(url)>400:return False
    try:
        p=urlsplit(url)
        return p.scheme in ('https','http') and p.netloc.lower()==p.hostname and any(
            p.hostname==h or p.hostname.endswith('.'+h) for h in PUBLISHERS)
    except (ValueError,AttributeError):
        return False

def screen(source, title, url, at, now, *, published=False, suffix=''):
    label,context=SOURCES[source]
    title=clean_title(title)
    if suffix and title.endswith(' - '+suffix):title=title[:-len(suffix)-3].strip()
    kind,reason=classify(title,context)
    if not kind:return None,reason
    if not at or now-at>timedelta(days=7 if published else 1) or at-now>timedelta(minutes=30):
        return None,'stale_or_undated'
    link=safe_link(source,url)
    if not link:return None,'unsafe_link'
    if len(title)>220:return None,'oversize_title'
    return {'title':title,'url':link,'source':source,'source_label':label,'topic':kind[0],
            'status':kind[1],'at':at.isoformat(),'published':published,'indexed':source in INDEXED},'accepted'

def fetch_bytes(endpoint):
    import requests
    started=time.monotonic()
    with requests.get(endpoint,headers={'User-Agent':'Mozilla/5.0 (compatible; TrendRadar public leads reader)',
            'Accept':'application/json, application/rss+xml, application/xml, */*'},timeout=(3,4),
            allow_redirects=False,stream=True) as res:
        if res.status_code!=200:raise ValueError('http_status')
        body=bytearray()
        for chunk in res.iter_content(16384):
            if time.monotonic()-started>8 or len(body)+len(chunk)>768*1024:raise ValueError('response_limit')
            body.extend(chunk)
        return bytes(body)

def parse_remote(source, kind, body, now):
    if kind=='json':
        expected='pcbeta-windows11' if source=='pcbeta' else source
        data=json.loads(body)
        if data.get('status') not in ('success','cache') or data.get('id')!=expected or not isinstance(data.get('items'),list):
            raise ValueError('unexpected_newsnow_payload')
        # NewsNow reports an aggregation timestamp, not the post's publication time.
        updated=data.get('updatedTime')
        try:at=datetime.fromtimestamp(updated/1000, ZONE) if type(updated) in (int,float) else None
        except (ValueError,OverflowError,OSError):at=None
        if at is None:
            if data['status']=='cache':raise ValueError('undated_cached_snapshot')
            at=now
        return [(x.get('title'),x.get('url'),at,False,'') for x in data['items'][:80] if isinstance(x,dict)]
    xml=body.decode('utf-8-sig')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)',xml,re.I):raise ValueError('xml_declaration_not_allowed')
    root=ET.fromstring(xml)
    if root.tag!='rss' or root.find('channel') is None:raise ValueError('not_rss')
    result=[]
    for item in root.findall('./channel/item')[:100]:
        publisher=item.find('source')
        if source in INDEXED and (publisher is None or not publisher_allowed(publisher.get('url',''))):continue
        suffix=clean_title(publisher.text) if publisher is not None else ''
        result.append((item.findtext('title'),item.findtext('link'),stamp(item.findtext('pubDate')),True,suffix))
    return result

def read_local(root, source, now):
    _,section,id,_=LOCAL[source]
    dbpath=root/section/(now.date().isoformat()+'.db')
    if not dbpath.is_file(): dbpath=root/section/((now.date()-timedelta(days=1)).isoformat()+'.db')
    with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True,timeout=.3) as db:
        if section=='news':
            rows=db.execute('SELECT title,url,last_crawl_time,NULL FROM news_items WHERE platform_id=? AND rank>0 ORDER BY last_crawl_time DESC, rank ASC LIMIT 120',(id,)).fetchall()
        else:
            rows=db.execute('SELECT title,url,last_crawl_time,published_at FROM rss_items WHERE feed_id=? ORDER BY last_crawl_time DESC,published_at DESC LIMIT 120',(id,)).fetchall()
    raw=[]
    for title,url,hour,published in rows:
        if section=='rss':
            raw.append((title,url,stamp(published),True,''))
        elif re.fullmatch(r'\d\d[:\-]\d\d',hour or ''):
            d=stamp(dbpath.stem+'T'+hour.replace('-',':')+':00+08:00')
            raw.append((title,url,d,False,''))
    return raw

def collect_source(root, source, now):
    try:
        if source in LOCAL: rows=read_local(root,source,now)
        else:
            _,kind,_,endpoint=REMOTE[source]
            rows=parse_remote(source,kind,fetch_bytes(endpoint),now)
        found=[]; reasons=Counter()
        for title,url,at,published,suffix in rows:
            item,reason=screen(source,title,url,at,now,published=published,suffix=suffix)
            reasons[reason]+=1
            if item:found.append(item)
        return {'state':'ok','fetched':now.isoformat(),'items':found[:40],'read':len(rows),'reasons':dict(reasons)}
    except Exception as exc:
        return {'state':'failed','error':type(exc).__name__,'fetched':'','items':[],'read':0,'reasons':{}}

def read_snapshot(root):
    try:
        p=root/CACHE
        if p.stat().st_size>1024*1024:return {}
        state=json.loads(p.read_text())
        if not isinstance(state,dict) or state.get('schema')!=SCHEMA or not isinstance(state.get('sources'),dict):
            return {}
        for value in state['sources'].values():
            if (not isinstance(value,dict) or not isinstance(value.get('items'),list)
                or any(not isinstance(x,dict) for x in value['items'])
                or type(value.get('read',0)) is not int or not isinstance(value.get('reasons',{}),dict)
                or any(type(v) is not int for v in value.get('reasons',{}).values())):
                return {}
        return state
    except (OSError,ValueError,TypeError):
        return {}

def fresh_enough(at, now, age):
    at=stamp(at)
    return at is not None and timedelta(0)<=now-at<=age

def gather(root, now, previous, source_fn=collect_source):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=dict(zip(SOURCES,pool.map(lambda k:source_fn(root,k,now),SOURCES)))
    for key,current in list(results.items()):
        old=previous.get('sources',{}).get(key,{})
        if current['state']=='failed' and fresh_enough(old.get('fetched'),now,timedelta(hours=6)):
            results[key]={**old,'state':'retained','error':current.get('error','source_failed')}
    return {'schema':SCHEMA,'attempted':now.isoformat(),'sources':results}

def refresh(root,now):
    with (root/'.leads-refresh.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        state=read_snapshot(root)
        if fresh_enough(state.get('attempted'),now,timedelta(minutes=20)):return
        next_state=gather(root,now,state)
        tmp=''
        try:
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=root,delete=False) as f:
                tmp=f.name;json.dump(next_state,f,ensure_ascii=False,separators=(',',':'))
            os.replace(tmp,root/CACHE)
        finally:
            if tmp and os.path.exists(tmp):os.unlink(tmp)

def signature(title):
    s=re.sub(r'^(?:网传|网络传言|据传|据称|传闻|消息称|爆料|小道消息|据爆料)[\s:：、!！]*','',title,flags=re.I)
    return ''.join(c for c in s.casefold() if c.isalnum())

def numeric(title):
    return tuple(re.findall(r'\d+(?:[.:/-]\d+)*|\b(?:pro|max|ultra|plus|mini|lite|se)\b',title.casefold()))

DIFFERENT = re.compile(r'不会|不再|没有|无缘|已取消|取消|延期|撤回|将改|澄清|否认|不实|小米|荣耀|华为|苹果|三星|英特尔|高通|联发科|谷歌|尼康|佳能|索尼|比亚迪|\b(?:apple|samsung|google|nikon|canon|sony|intel|amd|qualcomm|nvidia|xiaomi|honor)\b',re.I)

def similar(a,b):
    aa,bb=signature(a),signature(b)
    if not aa or not bb or numeric(a)!=numeric(b) or set(DIFFERENT.findall(a.casefold()))!=set(DIFFERENT.findall(b.casefold())):return False
    if aa==bb:return True
    if min(len(aa),len(bb))<12 or min(len(aa),len(bb))/max(len(aa),len(bb))<.78:return False
    ag,bg=({v[i:i+2] for i in range(len(v)-1)} for v in (aa,bb))
    return len(ag&bg)/len(ag|bg)>=.80

def merge(items):
    out=[]
    for item in sorted(items,key=lambda x:(ORDER.index(x['topic']),x['cached'],WEIGHT[x['status']],-stamp(x['at']).timestamp())):
        group=next((x for x in out if item['topic']==x['topic'] and item['status']==x['status'] and
                    (item['url'] in x['links'] or similar(item['title'],x['title']))),None)
        if group:
            group['links'].add(item['url'])
            continue
        out.append({**item,'links':{item['url']}})
    per_topic=Counter();per_source=Counter();result=[]
    for item in out:
        if per_topic[item['topic']]>=10 or per_source[item['source']]>=8:continue
        per_topic[item['topic']]+=1;per_source[item['source']]+=1
        links=item.pop('links')
        result.append({**item,'related_links':len(links)-1})
    return result[:45]

def view_state(state,now):
    statuses=[];items=[];reasons=Counter();seen=0
    attempted=stamp(state.get('attempted'))
    overdue=not fresh_enough(attempted,now,timedelta(minutes=45))
    for source,(label,_) in SOURCES.items():
        current=state.get('sources',{}).get(source,{})
        fetched=current.get('fetched')
        live=current.get('state') if fresh_enough(fetched,now,timedelta(hours=6)) else 'failed'
        if overdue and live=='ok':live='retained'
        accepted=0
        if live in ('ok','retained'):
            for x in current.get('items',[])[:40]:
                item,_=screen(source,x.get('title'),x.get('url'),stamp(x.get('at')),now,published=x.get('published') is True)
                if item:items.append({**item,'cached':live=='retained'});accepted+=1
        statuses.append({'source':source,'label':label,'state':live,'fetched':fetched if live!='failed' else '', 'matched':accepted})
        seen+=current.get('read',0)
        reasons.update(current.get('reasons',{}))
    return {'items':merge(items),'sources':statuses,'updated':attempted.isoformat() if attempted else '',
            'diagnostics':{'screened':seen,'reasons':dict(reasons)}}

def for_report(root, *, run_refresh=True):
    root=Path(root);now=datetime.now(ZONE)
    if run_refresh and not fresh_enough(read_snapshot(root).get('attempted'),now,timedelta(minutes=20)):
        try:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'collect',str(root)],
                timeout=12,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.SubprocessError):
            pass
    return view_state(read_snapshot(root),datetime.now(ZONE))

if __name__=='__main__' and sys.argv[1:2]==['collect']:
    refresh(Path(sys.argv[2]).resolve(),datetime.now(ZONE))
