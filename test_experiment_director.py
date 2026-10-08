import copy
import unittest
from experiment_director import audit_record, fault_campaign

class DirectorTests(unittest.TestCase):
    def test_faults_and_clean_control(self):
        report = fault_campaign()
        self.assertEqual(report['detected'], 8)
        self.assertEqual(report['clean_control_critical_findings'], 0)

    def test_independent_audit_rejects_false_success(self):
        row = {'outcome': 'verified', 'server_events': [], 'watcher': {'outcome': 'verified'}}
        self.assertIn('success_without_acquisition', audit_record(row))

    def test_independent_audit_checks_budget(self):
        row = {'strategy': 'scheduled_retry', 'server_events': [{'kind': 'server_received', 'actor': 'bob', 'operation': 'claim'}] * 4}
        self.assertIn('retry_budget_exceeded', audit_record(row))

    def test_independent_timing_check(self):
        row = {'server_events': [{'kind': 'released', 'time_ms': 10}, {'kind': 'acquired', 'time_ms': 20, 'actor': 'bob'}], 'watcher': {'owner': 'bob', 'exposure_ms': 100}}
        self.assertIn('watcher_timing_disagreement', audit_record(row))
