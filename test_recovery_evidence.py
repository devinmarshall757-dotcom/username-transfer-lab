import unittest
import tempfile
from pathlib import Path
from durable_transfer import Coordinator, PersistentFakePlatform
from recovery_campaign import FaultReader
from recovery_campaign import run_case

class RecoveryEvidenceTests(unittest.TestCase):
    def test_real_death_after_claim_recovers(self):
        self.assertTrue(run_case('claim')['passed'])
    def test_stale_buyer_evidence_is_not_success(self):
        row=run_case('claim','expired_buyer',competitor=True)
        self.assertTrue(row['passed'])
        self.assertFalse(row['eligible'])
    def test_replayed_nonce_is_rejected(self):
        self.assertTrue(run_case('claim','replayed_buyer',competitor=True)['passed'])
    def test_saved_success_is_rechecked(self):
        row=run_case('claim','expired_buyer',competitor=True,saved_success=True)
        self.assertTrue(row['passed'])
        self.assertFalse(row['eligible'])

    def test_unknown_preflight_does_not_release_and_can_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Coordinator(str(Path(directory)/'c.sqlite'), require_evidence=True)
            p = PersistentFakePlatform(str(Path(directory)/'p.sqlite'))
            try:
                c.create('tx')
                first = c.run('tx', FaultReader(p, 'expired_buyer'))
                self.assertEqual(first['state'], 'prepared')
                self.assertFalse(first['eligible'])
                self.assertEqual(p.verify(), (True, 'seller'))
                self.assertTrue(c.run('tx', p)['eligible'])
            finally:
                c.close()
                p.close()

    def test_missing_evidence_adapter_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Coordinator(str(Path(directory)/'c.sqlite'), require_evidence=True)
            p = PersistentFakePlatform(str(Path(directory)/'p.sqlite'))
            class LegacyOnly:
                resource_key = p.resource_key
                username = p.username
                def verify(self):
                    return True, 'seller'
                def release(self, seller):
                    raise AssertionError('Must not release without evidence')
            try:
                c.create('tx')
                self.assertEqual(c.run('tx', LegacyOnly())['state'], 'prepared')
            finally:
                c.close()
                p.close()
