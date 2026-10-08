import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from durable_transfer import Coordinator, PersistentFakePlatform, InjectedCrash


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cp = str(Path(self.tmp.name) / 'c.sqlite')
        self.pp = str(Path(self.tmp.name) / 'p.sqlite')
        self.c = Coordinator(self.cp)
        self.addCleanup(self.c.close)

    def platform(self, username, owner, path=None):
        p = PersistentFakePlatform(path or self.pp, username=username, initial_owner=owner)
        self.addCleanup(p.close)
        return p

    def test_two_usernames_with_distinct_accounts(self):
        first = self.platform('alpha', 'alice')
        second = self.platform('beta', 'carol')
        self.c.create('a', 'alpha', 'alice', 'bob')
        self.c.create('b', 'beta', 'carol', 'dana')
        self.assertTrue(self.c.run('a', first)['eligible'])
        self.assertEqual(second.verify(), (True, 'carol'))
        self.assertTrue(self.c.run('b', second)['eligible'])
        self.assertEqual(first.verify(), (True, 'bob'))
        self.assertEqual(second.verify(), (True, 'dana'))

    def test_id_reuse_must_match_all_details(self):
        original = self.c.create('a', 'alpha', 'alice', 'bob')
        for details in [('beta', 'alice', 'bob'), ('alpha', 'carol', 'bob'),
                        ('alpha', 'alice', 'dana')]:
            with self.assertRaises(ValueError):
                self.c.create('a', *details)
        self.assertEqual(self.c.get('a'), original)

    def test_wrong_owner_including_buyer_cannot_fake_success(self):
        for owner in ('bob', 'stranger'):
            name = 'name-' + owner
            p = self.platform(name, owner)
            self.c.create(name, name, 'alice', 'bob')
            result = self.c.run(name, p)
            self.assertEqual(result['state'], 'ownership_mismatch')
            self.assertFalse(result['eligible'])
            self.assertEqual(p.verify(), (True, owner))

    def test_wrong_username_is_rejected_without_mutation(self):
        p = self.platform('beta', 'alice')
        before = self.c.create('a', 'alpha', 'alice', 'bob')
        with self.assertRaises(ValueError):
            self.c.run('a', p)
        self.assertEqual(self.c.get('a'), before)
        self.assertEqual(p.verify(), (True, 'alice'))

    def test_recovery_uses_persisted_buyer_and_cannot_switch_platform(self):
        p = self.platform('alpha', 'alice')
        self.c.create('a', 'alpha', 'alice', 'bob')
        with self.assertRaises(InjectedCrash):
            self.c.run('a', p, 'release')
        other = self.platform('alpha', 'alice', str(Path(self.tmp.name) / 'other.sqlite'))
        with self.assertRaises(ValueError):
            self.c.run('a', other)
        reopened = Coordinator(self.cp)
        try:
            self.assertEqual(reopened.run('a', p)['owner'], 'bob')
        finally:
            reopened.close()

    def test_same_account_rejected(self):
        with self.assertRaises(ValueError):
            self.c.create('a', 'alpha', 'alice', 'alice')

    def test_legacy_database_migration_preserves_owner_and_binding(self):
        cp = str(Path(self.tmp.name) / 'old-c.sqlite')
        pp = str(Path(self.tmp.name) / 'old-p.sqlite')
        with closing(sqlite3.connect(cp, isolation_level=None)) as db:
            db.executescript("""
                CREATE TABLE transfers(id TEXT PRIMARY KEY,state TEXT NOT NULL,owner TEXT,eligible INTEGER NOT NULL DEFAULT 0);
                INSERT INTO transfers VALUES ('old','release_intent',NULL,0);
                CREATE TABLE resource_binding(resource TEXT PRIMARY KEY,transaction_id TEXT NOT NULL);
            """)
            db.execute('INSERT INTO resource_binding VALUES (?,?)',
                       (str(Path(pp).resolve()) + ':username:1', 'old'))
        with closing(sqlite3.connect(pp, isolation_level=None)) as db:
            db.executescript('CREATE TABLE username(id INTEGER PRIMARY KEY,owner TEXT); INSERT INTO username VALUES(1,NULL);')
        c = Coordinator(cp)
        p = PersistentFakePlatform(pp)
        try:
            self.assertEqual(p.verify(), (True, None))
            self.assertEqual(c.get('old')['seller'], 'seller')
            self.assertEqual(c.get('old')['resource'], p.resource_key)
            self.assertTrue(c.run('old', p)['eligible'])
        finally:
            c.close()
            p.close()
