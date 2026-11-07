"""Title-only triage. Nothing this module accepts is established as a fact."""
import html
import re
import unicodedata

RUMOR = re.compile(
    r'网传|网络传言|传闻|传言|坊间|流传|风声|小道(?:消息)?|据传|据称|消息称|知情人(?:称|透露)|'
    r'爆料|路透(?!社)|疑似|未经证实|尚未证实|未获证实|或将|有望|传出|被曝|'
    r'(?:新机|新款|新车|规格|配置|照片|渲染图|相机|镜头|应用|日历|系统|版|价格|芯片|处理器)曝光|'
    r'(?:曝|曝光).{0,18}(?:参数|规格|渲染图|新品|新款|发布日期)|'
    r'\b(?:rumou?rs?|leaks?|leaked|reportedly|allegedly|unconfirmed|tipped)\b', re.I)
CORRECTION = re.compile(r'辟谣|不实(?:消息|传闻|传言|信息)?|系谣言|澄清|回应(?:称|：|:)|否认|^官方回应|\bdebunk(?:s|ed)?\b', re.I)
EARLY = re.compile(r'拟(?:开|建|增|引进|改|设|落地|投用)|意向|筹备|招标|规划(?:公示|调整)?|征求意见|环评|(?:真的要|即将)来[了啦]|预计.{0,15}(?:开业|通车|开工|开演)|(?:开业|通车|开工|开演)时间')
QUERY = re.compile(r'[?？]|讨论|猜测|或许|可能|预测|看法|你认为|你觉得|如何看待|会不会|到底.{0,12}(?:不|吗|么)|\bcould\b|\bmight\b', re.I)
CITY_TOPIC = re.compile(r'地铁|城际|公交|道路|天桥|隧道|商场|购物中心|商圈|综合体|开业|商业广场|万象(?:系商业|城|天地)|招商|地块|规划|拟建|施工|围挡|体育场|演唱会|交通|限行|通车|体育馆')
ENT = re.compile(r'演唱会|音乐节|巡演|综艺|剧集|短剧|电影|电视剧|影视|开机|杀青|选角|定档|撤档|剧组|演出|卡司')
DIGITAL = re.compile(r'手机|相机|耳机|平板|笔记本|显卡|屏幕|显示器|处理器|芯片|新机|机型|系统|日历|应用|数码|鸿蒙|像素|镜头|固件|内存|'
                     r'\b(?:apple|iphone|ipad|macbook|imac|mac|samsung|one ui|ios|windows|android|copilot|gpu|cpu|snapdragon|pixel|homepod|oled|nvidia)\b', re.I)
SPORT = re.compile(r'战队|主教练|球队|电竞|瓦罗兰特|(?<![A-Za-z0-9])(?:NBA|CBA|FIFA|LPL|LCK|BLG|VCT)(?![A-Za-z0-9])',re.I)
COMMUNITY = re.compile(r'比赛|赛事|赛程|战队|球员|篮球|足球|转会|交易|选秀|主教练|训练赛|上场|签约|电竞|瓦罗兰特|'
                       r'\b(?:NBA|CBA|FIFA|LPL|LCK|BLG|F1|VCT)\b|公积金|社保|出行|校园活动|学区规划|市民热线|公共服务', re.I)
SENSITIVE = re.compile(
    r'开盒|人肉|住址|家庭地址|身份证|手机号|私人电话|微信号|行踪|跟拍|偷拍|私密|裸照|未成年|'
    r'出轨|婚外|恋情|分手|离婚|怀孕|堕胎|生子|私生|陪睡|约炮|家暴|强奸|猥亵|性侵|'
    r'诈骗|涉诈|贪腐|贪污|受贿|刑拘|被捕|被抓|吸毒|涉毒|犯罪|造假|敲诈|洗钱|欠薪|举报|'
    r'爆炸|坍塌|失踪|自杀|死亡|死伤|遇难|伤亡|劫持|中毒|投毒|封城|疫情|瘟疫|'
    r'荐股|内幕交易|股价|个股|做空|涨停|稳赚|暴涨|暴跌|币圈|金融内幕|银行挤兑|'
    r'特效药|疫苗|确诊|传染|治愈|患癌|抗癌|重病|'
    r'\b(?:doxx?(?:ing)?|home address|phone number|pregnant|affair|rape|suicide|killed|fraud|stock tip)\b', re.I)
SPAM = re.compile(r'加群|加我|扫码|返利|返现|领券|优惠码|代购|代拍|黄牛|付费进群|博彩|刷单|网赚|免费领取|私信领取|'
                  r'网红酒店.{0,40}(?:faq|维度)|酒店旺季服务好吗|\b(?:referral code|casino|coupon code|giveaway)\b', re.I)
INJECT = re.compile(
    r'@@(?:DATA|ORIGINAL|SCRIPT|STYLE)@@|<\s*/?\s*[a-z!][^<>]*>|'
    r'\[\s*(?:/?inst|/?system|/?assistant)\s*\]|<\||\|>|'
    r'忽略.{0,20}(?:指令|规则|提示|系统|预算)|(?:系统|开发者|工具)\s*(?:消息|指令|提示词)|'
    r'(?:泄露|打印|输出|披露|读取|发送|上传|公开|显示).{0,24}(?:密钥|密码|api[ _-]?key|system prompt|系统提示词)|'
    r'ignore.{0,38}(?:instructions?|previous|rules|system)|(?:reveal|print|exfiltrate|show).{0,38}(?:api.?key|system prompt|secret)|'
    r'(?:javascript|data|vbscript)\s*:|https?\s*://|\[.*?\]\s*\(', re.I)
PII = re.compile(r'(?<!\d)1[3-9](?:[\s.-]*\d){9}(?!\d)|(?<!\d)\d{17}[0-9x](?!\d)|'
                 r'(?<![a-z])[^\s@]{1,50}@[a-z0-9.-]+\.[a-z]{2,12}\b|'
                 r'(?:路|街|巷|小区|苑|公寓)\s*\d{1,6}\s*(?:号|栋|室)|(?:微信|QQ)\s*[:：]\s*[a-z0-9_-]{4,}', re.I)

def clean_title(value):
    if not isinstance(value,str) or len(value)>512:
        return ''
    value = unicodedata.normalize('NFKC',value)
    for _ in range(2):
        value = html.unescape(value)
    value = unicodedata.normalize('NFKC',value)
    # Join zero-width insertions before checking the policy; flatten line breaks.
    value = ''.join(c for c in value if unicodedata.category(c) not in {'Cf','Cs'} and (c.isspace() or unicodedata.category(c)!='Cc'))
    return ' '.join(value.split()).strip()

def quarantine_headline(value):
    title=clean_title(value)
    return not title or bool(INJECT.search(title) or PII.search(title) or (RUMOR.search(title) and not CORRECTION.search(title)))

def ai_blocked(value):
    title=clean_title(value)
    return not title or bool(INJECT.search(title) or PII.search(title) or RUMOR.search(title) or
            (QUERY.search(title) and (CORRECTION.search(title) or SENSITIVE.search(title))))

def classify(title, context):
    if not title or len(title)<6 or INJECT.search(title):
        return None,'injection_or_invalid'
    if SENSITIVE.search(title) or PII.search(title):
        return None,'sensitive_or_private'
    if SPAM.search(title):
        return None,'spam'
    rumor, correction, early, query = (p.search(title) for p in (RUMOR,CORRECTION,EARLY,QUERY))
    if CITY_TOPIC.search(title) and (context=='local' or re.search(r'[㐀-鿿]{2,}(?:市|区|县|镇)(?:人民政府|地铁|公交|交通|发布|规划|道路|新建)',title)):
        if not (rumor or correction or early or query):
            return None,'not_a_lead'
        return ('local', 'correction' if correction else 'planning' if early and not rumor else 'rumor' if rumor else 'discussion'),'accepted'
    if context=='local':
        return None,'wrong_topic'
    if ENT.search(title) and (rumor or correction or query or re.search(r'阵容(?:待定|引关注|疑变|有变)',title)):
        return ('ent', 'correction' if correction else 'rumor' if rumor else 'discussion'),'accepted'
    if context=='ent':
        return None,'wrong_topic'
    if SPORT.search(title) and context in ('community','local') and (rumor or correction or query):
        return ('community', 'correction' if correction else 'rumor' if rumor else 'discussion'),'accepted'
    if DIGITAL.search(title):
        if rumor or correction or (query and context in ('digital','community') and not re.search(r'召回|评测|体验|教程|故障',title)):
            return ('digital', 'correction' if correction else 'rumor' if rumor else 'discussion'),'accepted'
        return None,'not_a_lead'
    if context in ('digital','macrumors'):
        return None,'wrong_topic'
    if COMMUNITY.search(title) and (rumor or correction or query):
        return ('community', 'correction' if correction else 'rumor' if rumor else 'discussion'),'accepted'
    return None,'not_a_lead'
