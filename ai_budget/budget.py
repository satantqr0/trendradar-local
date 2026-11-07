"""Fail-closed CNY reservations. All amounts are integer millionths of a yuan."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json, os, sqlite3, tempfile

POLICY = Path('/app/ai_budget/policy.json')
STATE = Path('/app/output/ai_budget')
class BudgetBlocked(RuntimeError): pass

def atomic_json(path, data):
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        json.dump(data,stream,ensure_ascii=False); temporary=stream.name
    os.replace(temporary,path)

def initialize(state, day, reason):
    state=Path(state); state.mkdir(parents=True,exist_ok=True)
    database=state/'usage.sqlite3'
    if database.exists(): raise RuntimeError('Budget ledger already exists; refusing to reset it')
    with sqlite3.connect(database) as db:
        db.executescript('''
        CREATE TABLE days(day TEXT PRIMARY KEY, prior_reserved_micro INTEGER NOT NULL DEFAULT 0,
          halted INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
        CREATE TABLE requests(id INTEGER PRIMARY KEY, day TEXT NOT NULL, slot TEXT NOT NULL,
          started_at TEXT NOT NULL, reserved_micro INTEGER NOT NULL,
          prompt_bound INTEGER NOT NULL, output_limit INTEGER NOT NULL, status TEXT NOT NULL,
          prompt_tokens INTEGER, completion_tokens INTEGER, estimated_peak_micro INTEGER,
          UNIQUE(day,slot));
        ''')
        db.execute('INSERT INTO days VALUES(?,?,?,?)',(day,200000,1,reason))
    os.chmod(database,0o600)

class BudgetGate:
    def __init__(self, policy=POLICY, state=STATE, now=None):
        self.policy=json.loads(Path(policy).read_text())
        self.state=Path(state); self.database=self.state/'usage.sqlite3'
        self.now=now or (lambda:datetime.now(ZoneInfo('Asia/Shanghai')))
        p=self.policy
        required=['daily_limit_micro_cny','max_requests_per_day','max_message_bytes','prompt_overhead_token_allowance',
                  'max_output_tokens','peak_input_micro_cny_per_token','peak_output_micro_cny_per_token']
        if any(type(p.get(k)) is not int or p[k]<=0 for k in required): raise BudgetBlocked('预算配置不完整，停止调用')
        if p['daily_limit_micro_cny']>200000 or p['max_requests_per_day']>4: raise BudgetBlocked('预算配置超过每日0.2元限制')
        if p['peak_input_micro_cny_per_token']<2 or p['peak_output_micro_cny_per_token']<8: raise BudgetBlocked('计价低于已核验的高峰价格，停止调用')
        if p['max_output_tokens']>1800 or p['max_message_bytes']>16000: raise BudgetBlocked('请求尺寸超过预算约束')
        if not self.database.is_file(): raise BudgetBlocked('预算账本缺失，停止调用；请先初始化并保留已用额度')

    def _time(self): return self.now().astimezone(ZoneInfo('Asia/Shanghai'))
    def _slot(self, now):
        minute=now.hour*60+now.minute
        for slot in self.policy['slots']:
            h,m=map(int,slot.split(':')); start=h*60+m
            if start<=minute<start+self.policy['slot_window_minutes']: return slot
        return None
    @contextmanager
    def _transaction(self):
        db=sqlite3.connect('file:'+str(self.database)+'?mode=rw',uri=True,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.execute('COMMIT')
        except BaseException:
            if db.in_transaction: db.execute('ROLLBACK')
            raise
        finally: db.close()

    def _inspect(self,db,now):
        day=now.date().isoformat(); slot=self._slot(now)
        control=db.execute('SELECT prior_reserved_micro,halted,note FROM days WHERE day=?',(day,)).fetchone() or (0,0,'')
        count,reserved,estimate=db.execute('SELECT COUNT(*),COALESCE(SUM(reserved_micro),0),COALESCE(SUM(estimated_peak_micro),0) FROM requests WHERE day=?',(day,)).fetchone()
        used=control[0]+reserved
        reason=''
        if control[1]: reason=control[2] or '当日预算已停止'
        elif not slot: reason='不在AI分析时段，复用最近分析'
        elif db.execute('SELECT 1 FROM requests WHERE day=? AND slot=?',(day,slot)).fetchone(): reason='本时段已尝试，复用最近分析'
        elif count>=self.policy['max_requests_per_day']: reason='今日调用次数已达上限'
        elif used>=self.policy['daily_limit_micro_cny']: reason='今日预算已达上限'
        return {'day':day,'slot':slot,'allowed':not reason,'reason':reason,'daily_limit_cny':self.policy['daily_limit_micro_cny']/1000000,
          'requests':count,'reserved_cny':used/1000000,'guarded_requests_reserved_cny':reserved/1000000,
          'estimated_guarded_cost_at_peak_cny':estimate/1000000,'prior_usage_unknown':bool(control[0]),
          'remaining_reservable_cny':max(0,self.policy['daily_limit_micro_cny']-used)/1000000}
    def inspect(self):
        with self._transaction() as db:return self._inspect(db,self._time())

    def reserve(self, message_bytes, output_limit):
        p=self.policy
        if type(message_bytes) is not int or message_bytes<1 or message_bytes>p['max_message_bytes']: raise BudgetBlocked('输入超过预算长度限制')
        if type(output_limit) is not int or not 1<=output_limit<=p['max_output_tokens']: raise BudgetBlocked('输出超过预算长度限制')
        # Text-only BPE: UTF-8 byte count is a conservative token upper bound;
        # add 1024 tokens for two message envelopes / JSON-output control tokens.
        bound=message_bytes+p['prompt_overhead_token_allowance']
        reserve=bound*p['peak_input_micro_cny_per_token']+output_limit*p['peak_output_micro_cny_per_token']
        with self._transaction() as db:
            now=self._time(); status=self._inspect(db,now)
            if not status['allowed']: raise BudgetBlocked(status['reason'])
            used=round(status['reserved_cny']*1000000)
            if used+reserve>p['daily_limit_micro_cny']: raise BudgetBlocked('剩余预算不足以覆盖本次最坏费用')
            cursor=db.execute('INSERT INTO requests(day,slot,started_at,reserved_micro,prompt_bound,output_limit,status) VALUES(?,?,?,?,?,?,?)',
                (status['day'],status['slot'],now.isoformat(),reserve,bound,output_limit,'reserved'))
            return cursor.lastrowid

    def finish(self, request_id, usage=None, status='success'):
        with self._transaction() as db:
            row=db.execute('SELECT day,reserved_micro,prompt_bound,output_limit FROM requests WHERE id=?',(request_id,)).fetchone()
            if row is None: raise BudgetBlocked('请求预留记录丢失')
            prompt=completion=estimate=None
            if isinstance(usage,dict) and type(usage.get('prompt_tokens')) is int and type(usage.get('completion_tokens')) is int:
                prompt=usage['prompt_tokens'];completion=usage['completion_tokens']
                if prompt<0 or completion<0: raise BudgetBlocked('用量返回值不合法')
                estimate=prompt*self.policy['peak_input_micro_cny_per_token']+completion*self.policy['peak_output_micro_cny_per_token']
                if prompt>row[2] or completion>row[3] or estimate>row[1]:
                    db.execute('INSERT INTO days(day,halted,note) VALUES(?,1,?) ON CONFLICT(day) DO UPDATE SET halted=1,note=excluded.note',(row[0],'供应商返回用量超出预留范围，停止当日后续调用'))
                    status='usage_bound_exceeded'
            elif status=='success': status='success_usage_unknown'
            db.execute('UPDATE requests SET status=?,prompt_tokens=?,completion_tokens=?,estimated_peak_micro=? WHERE id=?',(status,prompt,completion,estimate,request_id))
        # Reservations are never refunded: a timeout / process crash may still be billed.
