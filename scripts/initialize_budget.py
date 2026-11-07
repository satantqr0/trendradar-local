"""Explicitly initialize a new ledger with the current day blocked."""
from datetime import datetime
import fcntl
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0,'/app')
from ai_budget.budget import STATE, initialize

state = Path(STATE)
state.mkdir(mode=0o700,parents=True,exist_ok=True)
with (state/'.initialize.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    initialize(state,today,'新建账本，下一自然日开始允许 AI 调用')
print('已创建账本，下一自然日开始允许 AI 调用。')
