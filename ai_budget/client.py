"""One budgeted MiniMax M Plan request, with no metered key or fallback."""
import json
import re
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from ai_budget.budget import BudgetGate, BudgetBlocked, POLICY

MODEL = 'MiniMax-M3.1-Flash'
API_BASE = 'https://api.minimaxi.com/v1'
QUOTA_URL = 'https://www.minimaxi.com/v1/token_plan/remains'
KEY_FILE = Path('/run/trendradar-secrets/minimax.key')
MIN_REMAINING_PERCENT = 10


class AIClient:
    def __init__(self, config):
        self.model = self.api_base = self.api_key = ''
        try:
            policy = json.loads(POLICY.read_text())
            self.model = policy['model']
            self.api_base = policy['api_base']
            if (self.model, self.api_base) == (MODEL, API_BASE):
                self.api_key = KEY_FILE.read_text().strip()
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.max_tokens = min(int(config.get('MAX_TOKENS') or 1800), 1800)
        self.timeout = min(int(config.get('TIMEOUT') or 90), 90)

    def validate_config(self):
        if (self.model, self.api_base) != (MODEL, API_BASE):
            return False, '预算策略没有启用指定的 MiniMax 套餐模型和官方端点'
        if not re.fullmatch(r'sk-cp-[A-Za-z0-9_-]{30,300}', self.api_key):
            return False, '缺少有效格式的 MiniMax 套餐密钥'
        return True, ''

    @staticmethod
    def _check_plan(session, headers):
        try:
            response = session.get(QUOTA_URL, headers=headers, timeout=(4, 8),
                                   allow_redirects=False)
            if response.status_code != 200:
                raise ValueError
            quota = response.json()
            if quota['base_resp']['status_code'] != 0:
                raise ValueError
            windows = next(row for row in quota['model_remains'] if row['model_name'] == 'general')
            for name in ('interval', 'weekly'):
                percent = windows[f'current_{name}_remaining_percent']
                if (windows[f'current_{name}_status'] != 1 or type(percent) not in (int, float)
                        or not MIN_REMAINING_PERCENT <= percent <= 100):
                    raise ValueError
        except (requests.RequestException, StopIteration, KeyError, TypeError, ValueError):
            raise BudgetBlocked('MiniMax 套餐额度不足或无法核验，复用最近分析') from None

    def chat(self, messages, **kwargs):
        valid, error = self.validate_config()
        if not valid:
            raise BudgetBlocked(error)
        if not 1 <= len(messages) <= 2 or any(
            not isinstance(message, dict) or set(message) != {'role', 'content'}
            or message['role'] not in ('system', 'user') or not isinstance(message['content'], str)
            for message in messages
        ):
            raise BudgetBlocked('预算仅支持两条纯文本消息')
        if set(kwargs) - {'temperature', 'max_tokens', 'timeout', 'num_retries'}:
            raise BudgetBlocked('请求包含未核算费用的参数')
        maximum = min(int(kwargs.get('max_tokens') or self.max_tokens), self.max_tokens)
        message_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode())
        gate = BudgetGate()
        if message_bytes > gate.policy['max_message_bytes'] or not 1 <= maximum <= gate.policy['max_output_tokens']:
            raise BudgetBlocked('消息或输出超过预算长度限制')
        state = gate.inspect()
        if not state['allowed']:
            raise BudgetBlocked(state['reason'])

        headers = {'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'}
        payload = {'model': MODEL, 'messages': messages, 'stream': False,
                   'max_completion_tokens': maximum, 'reasoning_effort': 'low',
                   'reasoning_split': True}
        with requests.Session() as session:
            session.mount('https://', HTTPAdapter(max_retries=0))
            self._check_plan(session, headers)
            request_id = gate.reserve(message_bytes, maximum)
            try:
                response = session.post(API_BASE + '/text/chatcompletion_v2', json=payload,
                                        headers=headers, timeout=(10, self.timeout),
                                        allow_redirects=False)
                if response.status_code != 200:
                    gate.finish(request_id, status=f'http_{response.status_code}')
                    raise RuntimeError(f'MiniMax HTTP {response.status_code}，保留本次预留')
                body = response.json()
            except requests.RequestException as exc:
                gate.finish(request_id, status='transport_' + type(exc).__name__)
                raise RuntimeError('MiniMax 连接或响应异常，保留本次预留') from None

        usage = body.get('usage') if isinstance(body, dict) else None
        try:
            code = body['base_resp']['status_code']
            if type(code) is not int:
                raise ValueError
            if code != 0:
                gate.finish(request_id, usage, status=f'provider_{code}')
                raise RuntimeError(f'MiniMax 套餐接口返回状态 {code}，保留本次预留')
            choice = body['choices'][0]
            if body['model'] != MODEL or choice['finish_reason'] != 'stop' or body.get('output_sensitive'):
                raise ValueError
            content = choice['message']['content']
            if (not isinstance(content, str) or not content.strip()
                    or re.search(r'<\s*/?\s*(think|reasoning|analysis)\b', content, re.I)):
                raise ValueError
            if not isinstance(usage, dict) or any(
                type(usage.get(key)) is not int or usage[key] < 0 for key in ('prompt_tokens', 'completion_tokens')
            ):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            gate.finish(request_id, usage, status='invalid_completion')
            raise RuntimeError('MiniMax 返回不完整，保留最近分析') from None

        gate.finish(request_id, usage)
        if (usage['prompt_tokens'] > message_bytes + gate.policy['prompt_overhead_token_allowance']
                or usage['completion_tokens'] > maximum):
            raise BudgetBlocked('MiniMax 返回用量超过预留，当日停止调用')
        print('[AI-Budget] MiniMax 套餐请求已记账')
        return content
