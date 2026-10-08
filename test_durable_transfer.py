import tempfile
import unittest
from pathlib import Path
from durable_transfer import Coordinator, PersistentFakePlatform, InjectedCrash


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'coordinator.sqlite')
        self.platform_path = str(Path(self.tmp.name) / 'platform.sqlite')

    def open_pair(self):
        coordinator = Coordinator(self.path)
        platform = PersistentFakePlatform(self.platform_path)
        self.addCleanup(coordinator.close)
        self.addCleanup(platform.close)
        coordinator.create('tx')
        return coordinator, platform

    def test_restart_after_each_side_effect(self):
        for point in ('release', 'claim'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as folder:
                path = str(Path(folder) / 'c.sqlite')
                pp = str(Path(folder) / 'p.sqlite')
                c, p = Coordinator(path), PersistentFakePlatform(pp)
                c.create('tx')
                with self.assertRaises(InjectedCrash):
                    c.run('tx', p, point)
                c.close()
                p.close()
                c, p = Coordinator(path), PersistentFakePlatform(pp)
                try:
                    result = c.run('tx', p)
                    self.assertTrue(result['eligible'])
                    self.assertEqual(p.verify(), (True, 'buyer'))
                    self.assertEqual(c.run('tx', p), result)
                    self.assertEqual(c.create('tx'), result)
                finally:
                    c.close()
                    p.close()

    def test_capture_during_restart(self):
        c, p = self.open_pair()
        with self.assertRaises(InjectedCrash):
            c.run('tx', p, 'release')
        p.db.execute("UPDATE usernames SET owner='competitor' WHERE name='demo'")
        result = c.run('tx', p)
        self.assertEqual(result['state'], 'competitor_capture')
        self.assertFalse(result['eligible'])

    def test_unavailable_verification_can_reconcile_later(self):
        c, p = self.open_pair()
        p.verification_available = False
        self.assertFalse(c.run('tx', p)['eligible'])
        self.assertEqual(p.db.execute('SELECT owner FROM usernames').fetchone()[0], 'seller')
        p.verification_available = True
        self.assertTrue(c.run('tx', p)['eligible'])

    def test_release_intent_with_no_side_effect_is_conservative(self):
        c, p = self.open_pair()
        c.transition('tx', 'release_intent')
        result = c.run('tx', p)
        self.assertEqual(result['state'], 'seller_retained')
        self.assertFalse(result['eligible'])

    def test_unknown_transaction(self):
        c, p = self.open_pair()
        with self.assertRaises(KeyError):
            c.run('missing', p)
