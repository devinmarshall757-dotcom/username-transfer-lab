import unittest
from browser_recovery_campaign import assess

class BrowserRecoveryAuditTests(unittest.TestCase):
    def snapshot(self):
        return {'id':'x','username':'target','owner':'intruder','buyer':'bob','seller':'alice','config':{'cooldown_ms':0},'exposure_ms':1,'events':[{'kind':'released','time_ms':0},{'kind':'acquired','time_ms':1,'actor':'intruder'},{'kind':'server_received','time_ms':1,'operation':'release','actor':'alice'}],'request_counts':{'alice':1}}
    def test_false_verified_is_caught_independently(self):
        record={'eligible':True}
        codes=assess(record,record,self.snapshot(),'stale')
        self.assertIn('first_false_verified',codes)
        self.assertIn('fault_granted_eligibility',codes)
    def test_release_request_replay_is_caught(self):
        s=self.snapshot()
        s['events'].append({'kind':'server_received','time_ms':2,'operation':'release','actor':'alice'})
        self.assertIn('release_replayed_or_missing',assess({'eligible':False},{'eligible':False},s,'none'))
