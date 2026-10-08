import tempfile
import unittest
from pathlib import Path
from durable_transfer import Coordinator
from run_browser_lab import BrowserPlatform

class BrowserEvidenceTests(unittest.TestCase):
    def test_fresh_browser_evidence_is_bound_to_request(self):
        adapter = BrowserPlatform('http://local', {'id':'fixture','username':'target'},None,None,'scheduled',40,25,0)
        adapter.verify = lambda: (True,'bob')
        evidence = adapter.verify_evidence('nonce')
        self.assertEqual(evidence.nonce,'nonce')
        self.assertEqual(evidence.resource,adapter.resource_key)
        self.assertEqual(evidence.owner,'bob')

    def test_stale_and_replayed_browser_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Coordinator(Path(directory)/'c.sqlite', require_evidence=True)
            try:
                adapter = BrowserPlatform('http://local',{'id':'fixture','username':'target'},None,None,'scheduled',40,25,0)
                adapter.started = True
                adapter.verify = lambda: (True,'bob')
                for fault in ('stale','replayed'):
                    adapter.evidence_fault = fault
                    self.assertEqual(c.verify_owner(adapter),(False,None))
                adapter.evidence_fault = 'none'
                self.assertEqual(c.verify_owner(adapter),(True,'bob'))
            finally:
                c.close()
