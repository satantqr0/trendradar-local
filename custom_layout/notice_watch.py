"""Recognize direct government notices among configured RSS report entries."""
from datetime import timedelta


def collect(articles, now, rules, event_tools):
    notices = {}
    for article in articles:
        if article.get('cachedAt'):
            continue
        for row in article.get('_evidence',[]):
            url = event_tools.web_link(row.get('url'))
            title = rules.clean_title(row.get('title'))
            published = row.get('published')
            date = event_tools.publication(published,now)
            if (not url or not event_tools.authority_link(url) or rules.quarantine_headline(title)
                    or not date or not now-timedelta(days=30)<=date<=now+timedelta(minutes=15)):
                continue
            publisher, _, _ = event_tools.origin(url,'',title)
            notices.setdefault(url,{
                'url':url,'title':title,'publisher':publisher,'published':published,
                'category':article['category'],'_regions':article.get('_regions',[]),'kind':'notice','new':article.get('new') is True,
            })
    return {'articles':list(notices.values()),'sources':[]}
