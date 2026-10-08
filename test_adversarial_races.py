import unittest
from adversarial_races import trial

class AdversarialRaceTests(unittest.TestCase):
    def test_retry_clean_transfer_and_budget(self):
        row = trial(120001, 'fast_competitors', 0, 'retry10')
        self.assertEqual(row['outcome'], 'verified')
        self.assertLessEqual(len(row['retry_trace']), 3)
        self.assertEqual(row['independent_findings'], [])
        self.assertEqual(row['runner_errors'], [])

    def test_contested_run_has_one_winner(self):
        row = trial(120002, 'uncertain_release', 3, 'retry10')
        self.assertIn(row['outcome'], ('verified', 'competitor_capture', 'unresolved'))
        self.assertLessEqual(sum(e['kind'] == 'acquired' for e in row['server_events']), 1)
        self.assertEqual(row['independent_findings'], [])
