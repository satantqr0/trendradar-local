"""Use the pinned upstream renderer, then apply the local news reading layout."""
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import logging
from html import escape
_FOLDER = Path('/app/custom_layout')
_spec = spec_from_file_location('trendradar_upstream_html', _FOLDER/'upstream_html.py')
_upstream = module_from_spec(_spec)
_spec.loader.exec_module(_upstream)
_layout_spec = spec_from_file_location('trendradar_local_layout', _FOLDER/'layout.py')
_layout = module_from_spec(_layout_spec)
_layout_spec.loader.exec_module(_layout)

def render_html_content(*args, **kwargs):
    original = _upstream.render_html_content(*args, **kwargs)
    result = kwargs.get('ai_analysis')
    stamp = getattr(result, 'budget_analyzed_at', '') if result else ''
    if stamp:
        cached = getattr(result, 'budget_cached', False)
        reason = getattr(result, 'budget_cache_reason', '')
        tags = ''.join('<meta name="'+name+'" content="'+escape(str(value),quote=True)+'">' for name,value in [
          ('trendradar-ai-time',stamp),('trendradar-ai-cached',str(cached).lower()),('trendradar-ai-cache-reason',reason),('trendradar-ai-model',getattr(result,'budget_model','')),
          ('trendradar-ai-active-model',getattr(result,'budget_active_model',''))])
        original = original.replace('</head>',tags+'</head>',1)
        note = '分析生成：'+stamp+' · '+('复用已有结果，未新增 API 调用' if cached else '本轮新分析')+' · 每日预算 0.20 元'
        original = original.replace('<div class="ai-section-title">✨ AI 热点分析</div>',
          '<div class="ai-section-title">✨ AI 热点分析</div><div>'+escape(note)+'</div>',1)
    try:
        return _layout.relayout(original, '/app/output/layout_last_complete.json')
    except Exception:
        logging.exception('Local layout failed; preserving the upstream HTML report')
        return original
