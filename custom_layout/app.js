'use strict';
(() => {
const data=JSON.parse(document.getElementById('radar-data').textContent);
const categories=[['overview','今日概览','◫'],['focus','重点关注','☆'],['local','本地动态','⌖'],['society','社会民生','◌'],['tech','科技前沿','⌘'],['digital','数码与软件','▣'],['other','更多新闻','⋯'],['events','事件追踪','⊙'],['leads','路边社 · 待核','◇'],['social','Telegram / X','↗'],['ai','AI 分析','✧']];
const names=Object.fromEntries(categories.map(c=>[c[0],c[1]]));
const descriptions={local:'按关注设置中的区域整理',society:'社会事件、民生政策与公共议题',tech:'人工智能、科学研究与产业新进展',digital:'硬件新品、软件应用与数字生活',other:'保留的国际、财经、汽车及其他关注主题'};
const leadData=data.leads||{items:[],sources:[],updated:''};
const intelligence=data.intelligence||{};
const events=intelligence.events||[];
const watches=intelligence.watches||[];
const sourceHealth=intelligence.health||{};
const social=intelligence.social||{};
const socialItems=social.items||[];
const socialSources=social.sources||[];
const nonExportRoutes=['leads','events','social','focus'];
const counts=Object.fromEntries(categories.map(c=>[c[0],data.articles.filter(a=>a.category===c[0]).length]));
counts.leads=leadData.items.length;
counts.events=events.length;
counts.social=socialItems.length;
counts.focus=intelligence.focus?.count||0;
const $=s=>document.querySelector(s);
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function append(parent,...children){children.forEach(c=>c&&parent.append(c));return parent;}
let selected=Object.hasOwn(names,location.hash.slice(1))?location.hash.slice(1):'overview',limit=24,query='',leadTopic='all',eventWatch='all',socialPlatform='all';
const nav=$('#navigation'),mobile=$('#mobile-navigation');
categories.forEach(([id,name,icon],i)=>{
 if(id==='events')nav.append(el('div','nav-gap'));
 const a=el('a','nav-link');a.href='#'+id;a.dataset.category=id;
 append(a,el('span','nav-icon',icon),el('span','nav-name',name));
 if(id!=='overview'&&id!=='ai')a.append(el('span','nav-count',counts[id]));
 nav.append(a);const option=el('option','',name+(counts[id]?' · '+counts[id]:''));option.value=id;mobile.append(option);
});
const meta=data.meta;
$('#updated-time').textContent=meta['生成时间']||'等待更新';
const health=[meta['热榜平台'],meta['RSS 源']].filter(Boolean).map(x=>x.split('/').map(Number));
const ok=health.reduce((s,p)=>s+(p[0]||0),0),total=health.reduce((s,p)=>s+(p[1]||0),0);
[[data.articles.length,'篇精选新闻',data.retainedCount?`含 ${data.retainedCount} 篇上次采集`:'合并重复标题与链接'],[counts.local,'篇本地动态','按设置的区域匹配'],[`${ok}/${total}`,'个来源采集成功','每半小时自动采集']].forEach(([number,label,note])=>{
 const block=el('div','metric'),labelNode=el('div','metric-label',label);labelNode.append(el('small','',note));append(block,el('strong','',number),labelNode);$('#metrics').append(block);
});
if(data.errors.length||ok<total){$('#alerts').append(el('div','alert',data.retainedCount?`部分订阅源本轮连接中断，保留 ${data.retainedCount} 篇已采集的本地报道，并标注上次采集时间。`:'部分来源暂未更新：'+(data.errors.join('、')||'请查看原版报告的采集状态')+'。以下展示已成功采集的新闻。'));}
function navigate(id){if(location.hash==='#'+id){selected=id;query='';$('#search').value='';limit=24;render();window.scrollTo({top:0});}else location.hash=id;}
function categoryArticles(id){
 const list=data.articles.filter(a=>a.category===id);
 // Local articles retain publication dates.
 if(id==='local')list.sort((a,b)=>(Number(b.official)-Number(a.official))||(b.published||'').localeCompare(a.published||''));
 if(id==='society')list.sort((a,b)=>Number(b.origins.includes('热榜'))-Number(a.origins.includes('热榜')));
 if(id==='tech')list.sort((a,b)=>Number(b.groups.includes('科技前沿'))-Number(a.groups.includes('科技前沿')));
 return list;
}
function row(article,index){
 const li=el('li','article-row'),body=el('div','article-body');
 const headline=el(article.url?'a':'span','article-title',article.title);
 headline.title=article.title;
 if(article.url){headline.href=article.url;headline.target='_blank';headline.rel='noopener noreferrer';}
 const details=el('div','article-meta');
 if(article.official)details.append(el('span','official','政务'));
 if(article.sources.length)details.append(el('span','',article.sources.slice(0,2).join(' · ')));
 const stamp=article.published||article.observed;
 if(stamp){const time=el('time','',(article.published?'报道 ':'采集 ')+stamp);details.append(time);}
 if(article.cachedAt)details.append(el('span','retained','上次采集 '+article.cachedAt));
 if(!article.published&&article.origins.includes('热榜'))details.append(el('span','hot','热榜'));
 append(body,headline,details);return append(li,el('span','article-index',String(index+1).padStart(2,'0')),body);
}
function articleList(articles){const list=el('ol','article-list');articles.forEach((a,i)=>list.append(row(a,i)));if(!articles.length)list.append(el('li','empty','本轮暂无匹配新闻，等待下一次更新。'));return list;}
function moreButton(id,label){const b=el('button','card-more');b.dataset.category=id;b.setAttribute('aria-label','查看'+names[id]+'全部 '+counts[id]+' 条');append(b,el('span','',label||`查看全部 ${counts[id]} 条`),el('span','','↗'));return b;}
function card(id,max,cls=''){
 const articles=categoryArticles(id),box=el('section','card '+cls),heading=el('div','card-heading'),title=el('div');
 append(title,el('h3','',names[id]));
 if(id==='local')title.append(el('p','','政务与媒体报道 · 保留原始报道日期'));
 append(heading,title,el('span',id==='local'?'focus-label':'section-number',id==='local'?'重点关注':counts[id]+' 条'));
 append(box,heading,articleList(articles.slice(0,max)),moreButton(id));return box;
}
function sectionHeading(title,note){const h=el('div','section-heading');append(h,el('h2','',title),el('p','',note));return h;}
function aiPreview(){
 const box=el('section','card ai-preview');const heading=el('div','card-heading');append(heading,el('h3','','AI 阅读摘要'),el('span','ai-tag',data.aiModel||data.aiActiveModel||'AI'));
 let summary=data.ai[0]?.body||data.aiWarning||'本轮暂无 AI 分析。';
 if(summary.length>300)summary=summary.slice(0,300)+'…';
 append(box,heading,el('div','ai-summary',summary),el('p','ai-note',(data.aiIsCached?'复用分析 ':'分析生成 ')+(data.aiTime||'')+' · 每日预算 ¥0.20'));
 const b=el('button','card-more');b.dataset.category='ai';append(b,el('span','','阅读完整 AI 分析'),el('span','','↗'));box.append(b);return box;
}
function overview(){
 const out=el('div','overview');
 out.append(sectionHeading('今日重点','把关注的地方，放在前面'));
 const lead=el('div','lead-grid');append(lead,card('local',4,'local-card'),aiPreview());out.append(lead);
 const focusBrief=el('section','card event-brief'),focusCopy=el('div','');
 const focusAction=el('a','event-brief-link','查看和设置 ↗');focusAction.href='#focus';
 append(focusCopy,el('h3','','重点关注'),el('p','',intelligence.focus
  ? '本轮匹配 '+intelligence.focus.count+' 条新闻与公示。'
  : '设置你想跟踪的关键词和区域。'));
 out.append(append(focusBrief,focusCopy,focusAction));
 if(events.length){
  const eventBrief=el('section','card event-brief'),copy=el('div','');
  const action=el('a','event-brief-link','查看事件追踪 ↗');action.href='#events';
  append(copy,el('h3','','事件追踪'),el('p','',`本轮整理 ${events.length} 条事件，查看相关报道与信源状态。`));
  out.append(append(eventBrief,copy,action));
 }
 out.append(sectionHeading('拓展视野','社会 · 科技 · 数码'));
 const topics=el('div','topic-grid');['society','tech','digital'].forEach(id=>topics.append(card(id,4)));out.append(topics);
 const note=el('p','minor-note','还有 '+counts.other+' 篇其他关注新闻，');const a=el('a','','查看全部 →');a.href='#other';note.append(a);out.append(note);
 return out;
}
function feed(articles,isSearch=false){
 const out=el('div','feed-view'),heading=el('div','feed-header'),left=el('div');
 append(left,el('h2','',isSearch?'搜索结果':names[selected]),el('p','',isSearch?`“${query}” · 共 ${articles.length} 条匹配新闻`:(descriptions[selected]||'')+' · '+articles.length+' 条'));
 const back=el('button','back-button','← 返回概览');back.dataset.category='overview';append(heading,left,back);out.append(heading);
 const box=el('section','card list-card');box.append(articleList(articles.slice(0,limit)));out.append(box);
 if(limit<articles.length){const b=el('button','load-more',`再显示 ${Math.min(24,articles.length-limit)} 条 · 还剩 ${articles.length-limit} 条`);b.id='load-more';out.append(b);}
 return out;
}
const aiNames={'核心热点态势':'重点事件','舆论风向争议':'社会民生观察','异动与弱信号':'本地线索与进展','RSS 深度洞察':'科技与数码观察','研判策略建议':'后续关注事项','独立源点速览':'各来源内容速览'};
const leadTopics={all:'全部',local:'本地动态',ent:'文娱风声',digital:'数码爆料',community:'社区热议'};
const leadStatuses={rumor:'传闻 · 待核',discussion:'讨论 / 猜测',planning:'规划 / 筹备线索',correction:'辟谣 / 回应线索'};
const leadStamp=stamp=>stamp?stamp.slice(0,10)+' '+stamp.slice(11,16):'';
const eventStates={official:'主管部门发布',multiple:'多家来源报道',single:'单一来源',unverified:'来源待核',unknown:'来源待核对'};
const sourceStates={
 ok:['ok','本轮成功'],live:['ok','本轮成功'],success:['ok','本轮成功'],
 empty:['empty','本轮无新条目'],retained:['retained','沿用上次采集'],cached:['retained','沿用上次采集'],
 stale:['retained','数据滞后'],failed:['failed','本轮失败'],error:['failed','本轮失败'],
 unknown:['unknown','本轮状态待核对'],missing:['unknown','本轮状态待核对'],
};
const eventEvidenceKinds={direct:'原始来源',indexed:'搜索索引',index:'搜索索引',cached:'历史采集',hot:'热榜线索',rss:'RSS 订阅',repost:'转载或平台聚合',media:'媒体原站'};
const eventTimeFormat=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
function eventStamp(value){
 if(typeof value!=='string'||!value)return '';
 const date=new Date(value);
 return /^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&!Number.isNaN(date.valueOf())?eventTimeFormat.format(date):value.slice(0,25);
}
function publicUrl(url){
 if(typeof url!=='string'||!/^https?:\/\//i.test(url)||/[\x00-\x20\x7f\\]/.test(url))return null;
 try{
  const parsed=new URL(url),host=parsed.hostname.toLowerCase();
  if(!parsed.username&&!parsed.password&&!parsed.port&&!host.endsWith('.')&&!host.endsWith('.local')&&
     host.includes('.')&&!host.includes(':')&&!/^\d+(?:\.\d+){3}$/.test(host)&&!host.endsWith('.localhost'))return parsed;
 }catch{}
 return null;
}
function externalLink(title,link,cssClass='article-title'){
 const node=el(link?'a':'span',cssClass,title);
 if(link){node.href=link.href;node.target='_blank';node.rel='noopener noreferrer';}
 return node;
}
function sourcePanel(sources,caption,totals){
 const out=el('details','card event-health');
 const summary=el('summary','');
 append(summary,el('strong','',caption),el('span','',totals||'等待本轮数据'));
 out.append(summary);
 if(!sources.length){
  out.append(el('p','event-health-empty','本轮暂无线源明细。'));
  return out;
 }
 const list=el('ul','event-health-list');
 for(const source of sources){
  const [state,label]=Object.hasOwn(sourceStates,source.state)?sourceStates[source.state]:sourceStates.unknown;
  const row=el('li',''),details=el('div','event-health-info');
  const count=Number.isInteger(source.count)?source.count+' 条':'';
  append(details,el('strong','',source.label||source.id),el('small','',[
   count,source.lastOk&&'最近成功 '+eventStamp(source.lastOk),
  ].filter(Boolean).join(' · ')));
  const sourceUrl=publicUrl(source.url);
  if(sourceUrl)details.append(externalLink('打开公开来源',sourceUrl,'event-source-link'));
  list.append(append(row,details,el('span','event-health-state state-'+state,label)));
 }
 return append(out,list);
}
function eventCard(event,related){
 const card=el('li','card event-card'),headline=el('h3','',event.title),meta=el('div','article-meta event-meta');
 const state=Object.hasOwn(eventStates,event.state)?event.state:'unknown';
 append(meta,el('span','event-badge event-'+state,eventStates[state]),el('span','',names[event.category]||'其他动态'));
 if(event.newNotice)meta.append(el('span','official','新收录公示'));
 if(event.sources>0)meta.append(el('span','',event.sources+' 个可识别来源'));
 if(event.firstSeen)meta.append(el('time','','首次收录 '+eventStamp(event.firstSeen)));
 if(event.lastSeen&&event.lastSeen!==event.firstSeen)meta.append(el('time','','最近收录 '+eventStamp(event.lastSeen)));
 append(card,headline,meta);
 const evidence=event.evidence||[];
 if(!evidence.length&&!related.length)return card;
 const details=el('details','event-details');
 const labels=[];
 if(evidence.length)labels.push(evidence.length+' 条报道');
 if(related.length)labels.push(related.length+' 条待核线索');
 details.append(el('summary','','查看 '+labels.join(' · ')));
 if(evidence.length){
  const list=el('ul','event-evidence');
  for(const source of evidence){
   const item=el('li',''),meta=el('div','article-meta');
   append(meta,source.publisher&&el('span','',source.publisher),
    source.official&&el('span','official','主管部门'),
    Object.hasOwn(eventEvidenceKinds,source.kind)&&el('span','',eventEvidenceKinds[source.kind]),
    source.published&&el('time','','报道 '+eventStamp(source.published)));
   list.append(append(item,externalLink(source.title||event.title,publicUrl(source.url)),meta));
  }
  details.append(list);
 }
 if(related.length){
  const box=el('div','event-leads'),list=el('ul','event-evidence');
  append(box,el('h4','','相关线索 · 待核'),el('p','','按主题关联，线索的内容仍需核对。'));
  for(const lead of related){
   const label=Object.hasOwn(leadStatuses,lead.status)?leadStatuses[lead.status]:'线索 · 待核';
   list.append(append(el('li',''),externalLink(lead.title,publicUrl(lead.url)),el('div','article-meta',label)));
  }
  details.append(append(box,list));
 }
 return append(card,details);
}
function eventsView(){
 const out=el('section','feed-view events-view'),heading=el('div','feed-header'),left=el('div');
 const back=el('button','back-button','← 返回概览');back.dataset.category='overview';
 append(left,el('h2','','事件追踪'),el('p','',[
  '按事件整理相似报道，每半小时随新闻更新',intelligence.updated&&'本轮更新 '+eventStamp(intelligence.updated),
 ].filter(Boolean).join(' · ')));
 const totals=[sourceHealth.hot&&'热榜 '+sourceHealth.hot,sourceHealth.rss&&'RSS '+sourceHealth.rss].filter(Boolean).join(' · ');
 append(out,append(heading,left,back),sourcePanel(sourceHealth.sources||[],'信源监测',totals));
 const filters=el('div','lead-filters event-filters');filters.setAttribute('role','group');filters.setAttribute('aria-label','监测主题');
 for(const watch of [{id:'all',label:'全部事件',count:events.length},...watches]){
  const count=Number.isInteger(watch.count)?' '+watch.count:'';
  const fresh=watch.newCount>0?' · 新增 '+watch.newCount:'';
  const filter=el('button','lead-filter',watch.label+count+fresh);
  filter.type='button';filter.dataset.eventWatch=watch.id;
  filter.setAttribute('aria-pressed',String(eventWatch===watch.id));
  filters.append(filter);
 }
 out.append(filters);
 const visible=events.filter(event=>eventWatch==='all'||event.category===eventWatch||event.watches?.includes(eventWatch));
 const relatedById=new Map();
 for(const lead of intelligence.matchedLeads||[]){
  if(!lead.eventId)continue;
  if(!relatedById.has(lead.eventId))relatedById.set(lead.eventId,[]);
  relatedById.get(lead.eventId).push(lead);
 }
 if(!visible.length){
  out.append(el('div','card empty',eventWatch==='all'?'本轮暂无可整理的事件。':'本轮暂无与该主题匹配的事件。'));
  return out;
 }
 const list=el('ol','event-list');
 for(const event of visible.slice(0,limit))list.append(eventCard(event,relatedById.get(event.id)||[]));
 out.append(list);
 if(visible.length>limit){
  const more=el('button','load-more',`再显示 ${Math.min(24,visible.length-limit)} 条 · 还剩 ${visible.length-limit} 条`);
  more.id='load-more';out.append(more);
 }
 return out;
}
const socialPlatforms={all:'全部',telegram:'Telegram',x:'X'};
function socialRow(item){
 const row=el('li','article-row'),body=el('div','article-body'),meta=el('div','article-meta');
 const link=publicUrl(item.url),isX=item.platform==='x';
 const originalLink=link?.protocol==='https:'&&(isX
  ? ['x.com','twitter.com'].includes(link.hostname)&&link.pathname==='/search'
  : ['t.me','telegram.me'].includes(link.hostname))?link:null;
 append(meta,el('span','event-badge',isX?'X · 第三方公开榜单快照':'Telegram · 公开频道'),
  item.sourceLabel&&el('span','',item.sourceLabel),
  isX&&Number.isInteger(item.rank)&&item.rank>0&&el('span','','榜单第 '+item.rank+' 名'),
  !isX&&Number.isInteger(item.views)&&item.views>0&&el('span','','约 '+item.views.toLocaleString('zh-CN')+' 次浏览'),
  item.published&&el('time','',(isX?'快照时间 ':'发布时间 ')+eventStamp(item.published)),
  item.cached&&el('span','retained','上次成功'));
 const source=publicUrl(item.sourceUrl);
 if(source)meta.append(externalLink('来源页面',source,'event-source-link'));
 return append(row,append(body,externalLink(item.title,originalLink),meta));
}
function socialView(){
 const out=el('section','feed-view events-view social-view'),heading=el('div','feed-header'),left=el('div');
 const back=el('button','back-button','← 返回概览');back.dataset.category='overview';
 append(left,el('h2','','Telegram / X 公开动态'),el('p','',social.updated?'本轮更新 '+eventStamp(social.updated):'等待定时采集'));
 append(out,append(heading,left,back),el('p','lead-disclaimer','Telegram 展示选定公开频道的原帖。X 展示第三方公开榜单快照，标题链接打开 X 搜索。内容按线索展示，详情请核对原始出处。'));
 const filter=el('div','lead-filters event-filters');filter.setAttribute('role','group');filter.setAttribute('aria-label','社交平台');
 for(const [id,label] of Object.entries(socialPlatforms)){
  const button=el('button','lead-filter',label);button.type='button';button.dataset.socialPlatform=id;
  button.setAttribute('aria-pressed',String(socialPlatform===id));filter.append(button);
 }
 out.append(sourcePanel(socialSources,'社交信源状态',socialSources.length?socialSources.length+' 个信源':''),filter);
 const visible=socialItems.filter(item=>(item.platform==='telegram'||item.platform==='x')&&(socialPlatform==='all'||item.platform===socialPlatform));
 if(!visible.length)return append(out,el('div','card empty','本轮暂无'+(socialPlatform==='all'?'':socialPlatforms[socialPlatform]+' ')+'公开动态。'));
 const list=el('ol','article-list');
 for(const item of visible.slice(0,limit))list.append(socialRow(item));
 out.append(append(el('div','card list-card'),list));
 if(visible.length>limit){
  const more=el('button','load-more',`再显示 ${Math.min(24,visible.length-limit)} 条 · 还剩 ${visible.length-limit} 条`);
  more.id='load-more';out.append(more);
 }
 return out;
}
function leadRow(a){
 const li=el('li','article-row lead-row'),body=el('div','article-body'),details=el('div','article-meta lead-meta');
 append(details,el('span','lead-badge lead-status-'+a.status,leadStatuses[a.status]||'线索 · 待核'),
   el('span','lead-topic',leadTopics[a.topic]),el('span','',a.source_label+(a.indexed&&!a.source_label.endsWith('索引')?' · 搜索索引':'')),
   el('time','',(a.published?'报道 ':'榜单时间 ')+leadStamp(a.at)));
 if(a.cached)details.append(el('span','retained','上次成功'));
 if(a.related_links)details.append(el('span','','合并 '+a.related_links+' 条相似链接'));
 let url=null;
 try{const parsed=new URL(a.url);if(parsed.protocol==='https:'&&!parsed.username&&!parsed.password&&!parsed.port)url=parsed;}catch(e){}
 const title=el(url?'a':'span','article-title',a.title);
 if(url){title.href=url.href;title.target='_blank';title.rel='noopener noreferrer';}
 append(body,details,title);return append(li,body);
}
function leadsView(){
 const out=el('section','feed-view leads-view'),heading=el('div','feed-header'),left=el('div');
 const back=el('button','back-button','← 返回概览');back.dataset.category='overview';
 append(left,el('h2','','路边社 · 待核'),el('p','','公开讨论与早期线索 · 独立于新闻与 AI 分析'));
 append(heading,left,back);out.append(heading);
 out.append(el('p','lead-disclaimer','线索不是事实。相似转述合并，不代表多方证实；规划、招标和阵容传闻也不代表最终落地。'));
 const menu=el('div','lead-filters');menu.setAttribute('role','group');menu.setAttribute('aria-label','筛选线索主题');
 Object.entries(leadTopics).forEach(([key,name])=>{
  const count=key==='all'?counts.leads:leadData.items.filter(a=>a.topic===key).length;
  const button=el('button','lead-filter',name+' '+count);button.dataset.leadTopic=key;
  button.setAttribute('aria-pressed',String(key===leadTopic));menu.append(button);
 });out.append(menu);
 const sourceStatus=el('details','lead-source-status');
 const live=leadData.sources.filter(s=>s.state==='ok').length,retained=leadData.sources.filter(s=>s.state==='retained').length;
 sourceStatus.append(el('summary','',leadData.updated?'线索源 '+live+'/'+leadData.sources.length+' 正常'+(retained?' · '+retained+' 个复用':'')+' · '+leadStamp(leadData.updated):'等待首轮线索采集'));
 const info=el('ul','');leadData.sources.forEach(s=>info.append(el('li','',s.label+' · '+({ok:'本轮成功',retained:'复用上次结果',failed:'暂不可用'}[s.state]||'未更新')+(s.fetched?' · '+leadStamp(s.fetched):''))));sourceStatus.append(info);out.append(sourceStatus);
 const list=el('ol','article-list');
 const filtered=leadData.items.filter(a=>leadTopic==='all'||a.topic===leadTopic);
 filtered.forEach(a=>list.append(leadRow(a)));
 if(!filtered.length)list.append(el('li','empty',leadData.updated?'本轮暂无符合条件的线索。':'暂无线索，等待采集。'));
 out.append(append(el('section','card list-card'),list));return out;
}
function aiView(){
 const out=el('div','ai-view'),heading=el('div','feed-header'),left=el('div');
 append(left,el('h2','','AI 新闻分析'),el('p','',(data.aiModel||data.aiActiveModel||'AI')+' · '+(data.aiTime||meta['生成时间']||'')+' · '+(data.aiIsCached?'复用已有分析；'+(data.aiCacheReason||'本轮未重新调用API'):'本轮新分析；基于新闻标题，请结合原文阅读')));
 if(data.aiActiveModel&&data.aiActiveModel!==data.aiModel)left.append(el('p','','当前分析模型 '+data.aiActiveModel));
 const b=el('button','back-button','← 返回概览');b.dataset.category='overview';append(heading,left,b);out.append(heading);
 const grid=el('div','ai-grid');data.ai.forEach(block=>{const box=el('section','card ai-report');append(box,el('h3','',aiNames[block.title]||block.title),el('p','',block.body));grid.append(box);});
 if(!data.ai.length)grid.append(el('div','card empty',data.aiWarning||'本轮暂无 AI 分析。'));
 out.append(grid);return out;
}
const focusSnapshot=intelligence.focus;
let focusSettings=null,focusLoad='idle',focusSaved=false;
function focusTerms(value){return value.split(/[\n,，、]+/).map(term=>term.trim()).filter(Boolean);}
async function readFocusSettings(){
 focusLoad='loading';
 try{
  const response=await fetch('/api/focus',{cache:'no-store'});
  if(!response.ok)throw new Error();
  focusSettings=await response.json();
  focusLoad='ready';
 }catch{focusLoad='error';}
 if(selected==='focus')render();
}
function focusForm(){
 const form=el('form','focus-form'),inputs=el('div','focus-fields');
 function field(labelText,id,list,placeholder){
  const label=el('label','focus-field'),input=el('textarea');
  input.id=id;input.rows=3;input.maxLength=420;input.value=list.join('\n');input.placeholder=placeholder;
  label.append(el('span','',labelText),input);
  inputs.append(label);
  return input;
 }
 const keywords=field('重点关键词','focus-keywords',focusSettings.keywords,'例如 智能汽车、新品发布');
 const regions=field('重点区域','focus-regions',focusSettings.regions,'输入想关注的城市、区县或片区');
 const discover=el('input');discover.type='checkbox';discover.checked=focusSettings.discover;
 const toggle=el('label','focus-toggle');toggle.append(discover,el('span','','同时从 Google 新闻索引扩展采集'));
 const secret=el('input');secret.type='password';secret.required=true;secret.autocomplete='current-password';
 secret.name='focus-admin-key';secret.maxLength=120;
 const keyLabel=el('label','focus-field');keyLabel.append(el('span','','管理口令'),secret);
 const submit=el('button','focus-save','保存设置');submit.type='submit';
 const actions=el('div','focus-actions');actions.append(keyLabel,submit);
 const message=el('p','focus-message');message.setAttribute('role','alert');
 form.append(inputs,el('p','focus-help','一行一项，也可用逗号分隔。最多 10 个关键词、8 个区域。命中任一项即可收录，两组同时命中的内容优先。'),
  toggle,el('p','focus-help','开启后，每轮最多向 Google 新闻发送一次包含关注词的查询。'),actions,message);
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(submit.disabled)return;
  submit.disabled=true;message.textContent='正在保存…';
  try{
   const response=await fetch('/api/focus',{method:'PUT',cache:'no-store',headers:{
    'Content-Type':'application/json','Authorization':'Bearer '+secret.value.trim()
   },body:JSON.stringify({revision:focusSettings.revision,keywords:focusTerms(keywords.value),regions:focusTerms(regions.value),discover:discover.checked})});
   const result=await response.json();
   if(!response.ok)throw new Error(result.error||'保存失败，请稍后再试');
   secret.value='';focusSettings=result;focusSaved=true;
   if(selected==='focus')render();
  }catch(error){message.textContent=error.message||'保存失败，请稍后再试';submit.disabled=false;}
 });
 return form;
}
function focusView(){
 if(focusLoad==='idle')readFocusSettings();
 const out=el('section','feed-view focus-view'),header=el('div','feed-header'),copy=el('div','');
 const back=el('button','back-button','← 返回概览');back.dataset.category='overview';
 append(copy,el('h2','','重点关注'),el('p','','按你设置的关键词和区域筛选新闻与公示'));
 out.append(append(header,copy,back));
 if(focusSaved){
  const note=el('p','focus-note','设置已保存到 NAS。');note.setAttribute('role','status');out.append(note);
 }
 if(focusSettings){
  const details=el('details','card focus-settings'),summary=el('summary','','编辑关注设置');
  const terms=[...focusSettings.keywords,...focusSettings.regions];
  const chips=el('div','focus-chips');
  for(const term of terms)chips.append(el('span','',term));
  if(!terms.length)chips.append(el('span','','尚未设置'));
  details.append(summary,chips,focusForm());
  out.append(details);
 }else{
  const note=el('p','focus-note',focusLoad==='error'?'暂时无法读取 NAS 关注设置。':'正在读取 NAS 设置…');
  out.append(note);
 }
 if(!focusSnapshot||(focusSettings&&focusSnapshot.revision!==focusSettings.revision)){
  out.append(el('div','card empty','等待下一轮自动采集应用当前设置，通常在半小时内。'));
  return out;
 }
 const states={off:'当前使用已采集的新闻与公示',live:'已合并本轮索引结果',cached:'索引暂不可用，显示上次结果',unavailable:'索引暂不可用，显示本地结果',pending:'等待首次索引采集'};
 const status=states[focusSnapshot.status]||'';
 out.append(el('p','focus-result-summary',[
  '命中 '+focusSnapshot.count+' 条',
  focusSnapshot.count>focusSnapshot.items.length&&'展示前 '+focusSnapshot.items.length+' 条',
  status,focusSnapshot.updated&&'更新 '+eventStamp(focusSnapshot.updated),
 ].filter(Boolean).join(' · ')));
 if(!focusSnapshot.items.length){
  out.append(el('div','card empty','暂无匹配内容。可以调整关键词或区域。'));
  return out;
 }
 const list=el('ol','article-list');
 for(const item of focusSnapshot.items.slice(0,limit)){
  const body=el('div','article-body'),meta=el('div','article-meta');
  const badges=[...new Set([...item.keywords,...item.regions])];
  append(meta,item.kind==='notice'&&el('span','official','主管部门公示'),item.kind==='index'&&el('span','','新闻索引'),
   item.publisher&&el('span','',item.publisher),item.published&&el('time','',eventStamp(item.published)),
   item.cached&&el('span','retained','上次采集'));
  for(const badge of badges)meta.append(el('span','focus-hit',badge));
  append(body,externalLink(item.title,publicUrl(item.url)),meta);
  list.append(append(el('li','article-row'),body));
 }
 out.append(append(el('div','card list-card'),list));
 if(limit<focusSnapshot.items.length){
  const button=el('button','load-more','查看更多匹配报道');button.id='load-more';out.append(button);
 }
 return out;
}

function matches(){const value=query.toLocaleLowerCase();return data.articles.filter(a=>[a.title,...a.sources,...a.groups,names[a.category]].join(' ').toLocaleLowerCase().includes(value));}
function render(){
 document.querySelectorAll('.nav-link').forEach(a=>{const active=a.dataset.category===selected&&!query;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
 mobile.value=selected;
 const isReadingView=!nonExportRoutes.includes(selected);
 $('.search-box').hidden=!isReadingView;
 $('.reading-hint').hidden=!isReadingView;
 $('#export').disabled=!isReadingView;$('#print').disabled=!isReadingView;$('#original').disabled=!isReadingView;
 const specialViews={overview,focus:focusView,ai:aiView,leads:leadsView,events:eventsView,social:socialView};
 const content=query?feed(matches(),true):specialViews[selected]?.()||feed(categoryArticles(selected));
 $('#view').replaceChildren(content);
 document.title=(query?'搜索新闻':selected==='overview'?'本地新闻':names[selected])+' · 新闻雷达';
}
document.addEventListener('click',event=>{const target=event.target.closest('[data-category]');if(target){event.preventDefault();navigate(target.dataset.category);}if(event.target.id==='load-more'){limit+=24;render();}const topic=event.target.closest('[data-lead-topic]');if(topic&&selected==='leads'&&Object.hasOwn(leadTopics,topic.dataset.leadTopic)){leadTopic=topic.dataset.leadTopic;render();document.querySelector('[data-lead-topic="'+leadTopic+'"]').focus();}});
document.addEventListener('click',event=>{
 const filter=event.target.closest('[data-event-watch]');
 if(!filter||selected!=='events')return;
 eventWatch=filter.dataset.eventWatch;limit=24;render();
 [...document.querySelectorAll('[data-event-watch]')].find(item=>item.dataset.eventWatch===eventWatch)?.focus();
});
document.addEventListener('click',event=>{
 const filter=event.target.closest('[data-social-platform]');
 if(!filter||selected!=='social'||!Object.hasOwn(socialPlatforms,filter.dataset.socialPlatform))return;
 socialPlatform=filter.dataset.socialPlatform;limit=24;render();
 document.querySelector('[data-social-platform="'+socialPlatform+'"]').focus();
});
window.addEventListener('hashchange',()=>{selected=Object.hasOwn(names,location.hash.slice(1))?location.hash.slice(1):'overview';query='';$('#search').value='';limit=24;render();window.scrollTo({top:0,behavior:'instant'});});
$('#search').addEventListener('input',event=>{query=event.target.value.trim();limit=24;render();});
mobile.addEventListener('change',event=>navigate(event.target.value));
$('#refresh').addEventListener('click',()=>location.reload());
try{document.body.classList.toggle('dark',localStorage.getItem('radar-theme')==='dark');}catch(e){}
$('#theme').addEventListener('click',()=>{const dark=document.body.classList.toggle('dark');try{localStorage.setItem('radar-theme',dark?'dark':'light');}catch(e){}});
document.addEventListener('keydown',event=>{if(!$('#search').closest('.search-box').hidden&&event.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){event.preventDefault();$('#search').focus();}if(event.key==='Escape'&&query){query='';$('#search').value='';$('#search').blur();render();}});
$('#print').addEventListener('click',()=>{if(!$('#print').disabled)window.print();});
$('#original').addEventListener('click',()=>{if($('#original').disabled)return;const bytes=Uint8Array.from(atob($('#original-data').textContent.trim()),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:'text/html;charset=utf-8'}));const a=el('a');a.href=url;a.download='TrendRadar-原版报告.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);});
$('#export').addEventListener('click',()=>{
 if($('#export').disabled)return;
 const articles=query?matches():selected==='overview'?data.articles:categoryArticles(selected);
 let text='# '+(names[selected]||'新闻雷达')+'\n\n生成时间：'+(selected==='ai'?data.aiTime:meta['生成时间']||'')+'\n\n';
 if(selected==='ai'&&!query)text+=data.ai.map(b=>'## '+(aiNames[b.title]||b.title)+'\n\n'+b.body).join('\n\n');
 else text+=articles.map(a=>'- '+(a.url?'['+a.title.replace(/[\[\]]/g,'')+']('+a.url+')':a.title)+'\n  '+a.sources.join(' / ')+' · '+(a.published||a.observed)+(a.cachedAt?' · 上次采集 '+a.cachedAt:'')).join('\n\n');
 const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'})),a=el('a');a.href=url;a.download='新闻雷达-'+(selected||'overview')+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
});
render();
})();
