import unittest
from batch_lab import grouped_report, percentile


class BatchTests(unittest.TestCase):
    def test_grouping_keeps_failures_and_conditions_separate(self):
        rows = grouped_report([
            {'strategy':'confirmed','competitors':1,'outcome':'verified','watcher':{'exposure_ms':10}},
            {'strategy':'confirmed','competitors':1,'outcome':'competitor_capture','watcher':{'exposure_ms':2}},
            {'strategy':'confirmed','competitors':1,'outcome':'unresolved','error':'failure'},
            {'strategy':'confirmed','competitors':0,'outcome':'verified','watcher':{'exposure_ms':1}},
        ])
        contested=next(r for r in rows if r['competitors']==1)
        self.assertEqual(contested['attempts'],3)
        self.assertEqual(contested['verified_rate'],1/3)
        self.assertEqual(contested['buyer_exposure_samples'],1)
        self.assertEqual(contested['buyer_exposure_median_ms'],10)

    def test_empty_and_p95(self):
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile(list(range(1,101)),.95),95)
