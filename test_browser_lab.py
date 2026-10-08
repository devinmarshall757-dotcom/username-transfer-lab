import threading
import unittest
from urllib.error import HTTPError

from browser_lab import Config, LabServer, Run
from run_browser_lab import request


class LabTests(unittest.TestCase):
    def test_custom_target_and_invalid_names(self):
        run = Run(Config(target_handle='twin'))
        self.assertEqual(run.username, 'twin')
        self.assertEqual(run.handles['alice'], 'twin')
        for name in ('@twin', 'a b', 'x', 123):
            with self.assertRaises(ValueError):
                Config(target_handle=name)

    def run_fixture(self, **kwargs):
        return Run(Config(network_ms=0, processing_ms=0, jitter_ms=0, min_interval_ms=0, **kwargs))

    def test_profile_handles_change_and_ownership_is_verified(self):
        run = self.run_fixture()
        run.request('release', 'alice', run.replacement)
        self.assertEqual(run.handles['alice'], run.replacement)
        status, result = run.request('claim', 'bob', run.username)
        self.assertTrue(result['accepted'])
        self.assertEqual(run.snapshot()['owner'], 'bob')
        self.assertGreaterEqual(run.snapshot()['exposure_ms'], 0)

    def test_atomic_claim_with_concurrent_competitors(self):
        run = self.run_fixture()
        run.handles['competitor-0'] = 'other'
        run.request('release', 'alice', run.replacement)
        barrier = threading.Barrier(3)
        answers = []

        def claim(actor):
            barrier.wait()
            answers.append(run.request('claim', actor, run.username)[1]['accepted'])

        workers = [threading.Thread(target=claim, args=(actor,)) for actor in ('bob', 'competitor-0')]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join()
        self.assertEqual(sum(answers), 1)

    def test_cooldown_rejects_early_claim(self):
        run = self.run_fixture(cooldown_ms=1000)
        run.request('release', 'alice', run.replacement)
        self.assertFalse(run.request('claim', 'bob', run.username)[1]['accepted'])
        self.assertIsNone(run.owner)

    def test_rate_limit_applies_to_buyer_and_competitor(self):
        run = Run(Config(network_ms=0, processing_ms=0, jitter_ms=0, min_interval_ms=1000))
        run.handles['competitor-0'] = 'other'
        for actor in ('bob', 'competitor-0'):
            run.request('availability', actor)
            self.assertEqual(run.request('claim', actor, run.username)[0], 429)

    def test_lost_response_does_not_undo_ownership(self):
        run = self.run_fixture(lost_claim_response=True)
        run.request('release', 'alice', run.replacement)
        self.assertEqual(run.request('claim', 'bob', run.username)[0], 504)
        self.assertEqual(run.owner, 'bob')

    def test_invalid_actor_and_release_cannot_modify_owner(self):
        run = self.run_fixture()
        for operation, actor, handle in [('release', 'bob', run.replacement),
                                         ('claim', 'stranger', run.username)]:
            with self.assertRaises(ValueError):
                run.request(operation, actor, handle)
        self.assertEqual(run.owner, 'alice')

    def test_config_validation(self):
        for kwargs in ({'network_ms': -1}, {'competitor_count': 1000}, {'jitter_ms': float('nan')}):
            with self.assertRaises(ValueError):
                Config(**kwargs)

    def test_http_fixture_and_cross_origin_rejection(self):
        from urllib.request import Request, urlopen
        server = LabServer(('127.0.0.1', 0))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            run = request(base, '/runs', {})
            self.assertEqual(request(base, '/runs/' + run['id'])['owner'], 'alice')
            req = Request(base + '/runs', data=b'{}', headers={'Origin': 'https://example.com'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(req)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
