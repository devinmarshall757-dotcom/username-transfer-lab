import unittest
from adversarial_races import choose_offset, trial

class AdaptivePolicyTests(unittest.TestCase):
    def test_measurement_bounds(self):
        self.assertEqual(choose_offset(0, 0), 10)
        self.assertEqual(choose_offset(1000, 1), 60)
        self.assertGreater(choose_offset(200, 200), choose_offset(40, 40))

    def test_adaptive_uses_preflight_measurement(self):
        row = trial(150000, 'fast_competitors', 0, 'adaptive_retry', calibrate=True)
        self.assertEqual(set(row['calibration_rtt_ms']), {'alice', 'bob'})
        self.assertEqual(row['selected_offset_ms'], choose_offset(row['calibration_rtt_ms']['alice'], row['calibration_rtt_ms']['bob']))
        self.assertEqual(row['outcome'], 'verified')
        self.assertEqual(row['independent_findings'], [])
        self.assertLessEqual(len(row['retry_trace']), 3)
