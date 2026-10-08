"""Process-level checks exercise the operating system's actual lock behavior."""
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from durable_transfer import Coordinator, PersistentFakePlatform, WorkerBusy


def hold_after_release(path, platform_path, ready, resume, transaction_id='tx', username='demo', seller='seller'):
    c = Coordinator(path)
    p = PersistentFakePlatform(platform_path, username=username, initial_owner=seller)

    class PausedPlatform:
        resource_key = p.resource_key
        username = p.username

        def release(self, seller):
            p.release(seller)
            ready.set()
            if not resume.wait(15):
                raise RuntimeError('Test worker timed out')

        def claim(self, buyer):
            p.claim(buyer)

        def verify(self):
            return p.verify()

    try:
        c.run(transaction_id, PausedPlatform())
    finally:
        c.close()
        p.close()


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'c.sqlite')
        self.pp = str(Path(self.tmp.name) / 'p.sqlite')
        self.c = Coordinator(self.path)
        self.p = PersistentFakePlatform(self.pp)
        self.addCleanup(self.c.close)
        self.addCleanup(self.p.close)
        self.c.create('tx')

    def start_worker(self, transaction_id='tx', username='demo', seller='seller'):
        context = multiprocessing.get_context('spawn')
        ready, resume = context.Event(), context.Event()
        process = context.Process(target=hold_after_release,
                                  args=(self.path, self.pp, ready, resume, transaction_id, username, seller))
        process.start()

        def cleanup():
            if process.is_alive():
                resume.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            process.close()

        self.addCleanup(cleanup)
        self.assertTrue(ready.wait(10), 'Worker failed to reach release')
        return process, resume

    def test_contending_worker_cannot_mutate_transfer(self):
        process, resume = self.start_worker()
        before = self.c.get('tx')
        with self.assertRaises(WorkerBusy):
            self.c.run('tx', self.p)
        self.assertEqual(self.c.get('tx'), before)
        self.assertEqual(self.p.verify(), (True, None))
        resume.set()
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        result = self.c.run('tx', self.p)
        self.assertTrue(result['eligible'])
        self.assertEqual([x['state'] for x in result['log']],
                         ['prepared', 'release_intent', 'claim_intent', 'verified'])

    def test_process_death_unlocks_and_recovery_completes(self):
        process, _ = self.start_worker()
        process.terminate()
        process.join(10)
        self.assertFalse(process.is_alive())
        self.assertEqual(self.c.get('tx')['state'], 'release_intent')
        result = self.c.run('tx', self.p)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(self.p.verify(), (True, 'buyer'))

    def test_distinct_transaction_cannot_reuse_same_username(self):
        self.c.run('tx', self.p)
        self.c.create('other')
        with self.assertRaises(ValueError):
            self.c.run('other', self.p)
        self.assertFalse(self.c.get('other')['eligible'])

    def test_exception_does_not_leave_lock_held(self):
        with self.assertRaises(KeyError):
            self.c.run('missing', self.p)
        self.assertTrue(self.c.run('tx', self.p)['eligible'])

    def test_independent_usernames_overlap_across_processes(self):
        self.c.create('alpha-tx', 'alpha', 'alice', 'bob')
        self.c.create('beta-tx', 'beta', 'carol', 'dana')
        first, resume_first = self.start_worker('alpha-tx', 'alpha', 'alice')
        # This second worker must reach release while the first remains paused.
        second, resume_second = self.start_worker('beta-tx', 'beta', 'carol')
        self.assertTrue(first.is_alive())
        self.assertTrue(second.is_alive())
        self.assertEqual(self.c.get('alpha-tx')['state'], 'release_intent')
        self.assertEqual(self.c.get('beta-tx')['state'], 'release_intent')
        resume_second.set()
        second.join(10)
        self.assertEqual(second.exitcode, 0)
        self.assertTrue(self.c.get('beta-tx')['eligible'])
        self.assertEqual(self.c.get('alpha-tx')['state'], 'release_intent')
        resume_first.set()
        first.join(10)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(self.c.get('alpha-tx')['owner'], 'bob')
        self.assertEqual(self.c.get('beta-tx')['owner'], 'dana')

    def test_same_username_with_different_ids_still_blocked(self):
        process, resume = self.start_worker()
        before = self.c.create('other')
        with self.assertRaises(WorkerBusy):
            self.c.run('other', self.p)
        self.assertEqual(self.c.get('other'), before)
        self.assertEqual(self.p.verify(), (True, None))
        resume.set()
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        with self.assertRaises(ValueError):
            self.c.run('other', self.p)

    def test_same_transaction_cannot_run_against_different_platform(self):
        process, resume = self.start_worker()
        p = PersistentFakePlatform(str(Path(self.tmp.name) / 'other.sqlite'))
        self.addCleanup(p.close)
        with self.assertRaises(WorkerBusy):
            self.c.run('tx', p)
        self.assertEqual(p.verify(), (True, 'seller'))
        resume.set()
        process.join(10)
        self.assertEqual(process.exitcode, 0)
        with self.assertRaises(ValueError):
            self.c.run('tx', p)
