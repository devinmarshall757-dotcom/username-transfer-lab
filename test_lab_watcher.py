import unittest
from browser_lab import Run, Config
from lab_watcher import inspect_run, summarize


class WatcherTests(unittest.TestCase):
    def fixture(self):
        return Run(Config(network_ms=0,processing_ms=0,jitter_ms=0,min_interval_ms=0))

    def test_success_and_timing(self):
        r=self.fixture()
        r.request('release','alice',r.replacement)
        r.request('claim','bob',r.username)
        report=inspect_run(r.snapshot())
        self.assertEqual(report['outcome'],'verified')
        self.assertGreaterEqual(report['server_transfer_ms'],0)
        self.assertEqual(report['findings'],[])

    def test_duplicate_winner_and_owner_mismatch(self):
        r=self.fixture()
        r.request('release','alice',r.replacement)
        r.request('claim','bob',r.username)
        s=r.snapshot()
        s['events'].append({'kind':'acquired','time_ms':s['events'][-1]['time_ms'],'actor':'intruder'})
        codes={f['code'] for f in inspect_run(s)['findings']}
        self.assertIn('multiple_winners',codes)
        self.assertIn('owner_mismatch',codes)

    def test_missing_release_is_critical(self):
        r=self.fixture()
        s=r.snapshot()
        s['events']=[{'kind':'acquired','time_ms':1,'actor':'bob'}]
        s['owner']='bob'
        self.assertIn('claim_before_release',{f['code'] for f in inspect_run(s)['findings']})

    def test_unresolved_and_prepared_not_hidden(self):
        r=self.fixture()
        r.request('release','alice',r.replacement)
        r.finish()
        report=summarize([r.snapshot(),self.fixture().snapshot()])
        self.assertEqual(report['runs_created'],2)
        self.assertEqual(report['outcomes'],{'unresolved':1,'prepared':1})
        self.assertEqual(report['verified_fraction_of_released'],0)
        self.assertEqual(report['exposure']['samples'],0)
