import unittest
from unittest.mock import Mock,patch
from run_browser_lab import BrowserPlatform
from tune_scheduled import select_offset,groups_for


class TuningTests(unittest.TestCase):
    def test_terminal_verification_drains_competitors_after_buyer(self):
        run={'id':'test','username':'target'}
        for strategy in ('scheduled','confirmed'):
            adapter=BrowserPlatform('http://local',run,Mock(),Mock(),strategy,0,25,0)
            adapter.started=True
            with patch('run_browser_lab.request',return_value={'owner':'competitor'}) as api:
                if strategy=='confirmed':
                    adapter.verify()
                    self.assertEqual(api.call_args.args[1],'/runs/test')
                    adapter.claim_finished=True
                adapter.verify()
                self.assertEqual(api.call_args.args[1],'/runs/test/finish')

    def test_rejects_fast_but_unreliable_clean_control(self):
        results=[]
        for offset in (0,20):
            for competitor in (0,1,3):
                for i in range(10):
                    win=(i<5 if offset==0 and competitor==0 else i<8 if competitor else True)
                    results.append({'phase':'tuning','scenario':'baseline','offset_ms':offset,
                                    'strategy':'scheduled','competitors':competitor,
                                    'outcome':'verified' if win else 'unresolved'})
        self.assertEqual(select_offset(results),20)
        # Holdout wins cannot influence the frozen tuning decision.
        results.append({'phase':'validation','scenario':'baseline','offset_ms':0,
                        'strategy':'scheduled','competitors':1,'outcome':'verified'})
        self.assertEqual(select_offset(results),20)

    def test_group_separation(self):
        results=[{'phase':'validation','scenario':s,'offset_ms':o,'strategy':'scheduled',
                  'competitors':1,'outcome':'verified'} for s in ('baseline','high_latency') for o in (20,40)]
        self.assertEqual(len(groups_for(results)),4)
        self.assertIsNone(select_offset([]))
