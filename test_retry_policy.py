import unittest
from unittest.mock import Mock
from retry_policy import RetryBrowserPlatform
from tune_scheduled import groups_for


class RetryTests(unittest.TestCase):
    def test_bounds_are_passed_to_browser(self):
        seller,buyer=Mock(),Mock()
        adapter=RetryBrowserPlatform('http://local',{'id':'x','username':'target'},seller,buyer,'scheduled',10,25,0)
        adapter.release('alice')
        args=buyer.evaluate.call_args.args[1]
        self.assertEqual(args['attempts'],3)
        self.assertEqual(args['deadline'],500)
        self.assertTrue(adapter.started)

    def test_single_and_retry_results_never_merge(self):
        rows=[{'phase':'retry_validation','scenario':'baseline','offset_ms':10,
               'strategy':s,'competitors':1,'outcome':'verified'} for s in ('scheduled','scheduled_retry')]
        self.assertEqual(len(groups_for(rows)),2)
