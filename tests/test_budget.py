from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from ai_budget.budget import BudgetBlocked, BudgetGate, initialize

POLICY = Path(__file__).resolve().parents[1]/'ai_budget/policy.json'
ZONE = ZoneInfo('Asia/Shanghai')


class BudgetTests(TestCase):
    def setUp(self):
        self.scratch = TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.state = Path(self.scratch.name)
        self.time = datetime(2026,11,7,8,0,tzinfo=ZONE)

    def gate(self):
        return BudgetGate(POLICY,self.state,lambda:self.time)

    def test_new_ledger_is_blocked_for_the_first_day(self):
        with self.assertRaises(BudgetBlocked):
            self.gate()
        initialize(self.state,'2026-11-07','Initialization')
        self.assertFalse(self.gate().inspect()['allowed'])
        with self.assertRaises(RuntimeError):
            initialize(self.state,'2026-11-07','Duplicate')

    def test_four_full_reservations_fit_the_limit_without_duplicate_slots(self):
        initialize(self.state,'2026-11-06','Initialization')
        gate = self.gate()
        for hour, minute in ((8,0),(12,30),(18,30),(22,0)):
            self.time = self.time.replace(hour=hour,minute=minute)
            gate.reserve(16000,1800)
            with self.assertRaises(BudgetBlocked):
                gate.reserve(16000,1800)
        self.assertEqual(gate.inspect()['requests'],4)
        self.assertLessEqual(gate.inspect()['reserved_cny'],0.20)
