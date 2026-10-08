"""SQLite-backed offline transfer coordinator and independent fake platform."""
import argparse
import json
import sqlite3
import os
import hashlib
import time
import secrets
import math
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path


@dataclass(frozen=True)
class OwnershipEvidence:
    owner: str | None
    resource: str
    username: str
    nonce: str
    observed_at: float
    authoritative: bool


class WorkerBusy(RuntimeError):
    """Another worker currently owns this transaction or username lock."""


@contextmanager
def execution_lock(path):
    """Kernel-held lock: no timeout can let a paused worker overlap a new one."""
    with open(path, 'a+b') as handle:
        # Windows byte-range locks require a byte; concurrent identical writes
        # during initialization do not change the lock range.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise WorkerBusy('Another worker owns this transaction or username') from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkerBusy('Another worker owns this transaction or username') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class InjectedCrash(RuntimeError):
    pass


class Coordinator:
    def __init__(self, path, require_evidence=False, evidence_max_age_seconds=1):
        self.require_evidence = require_evidence
        self.evidence_max_age_seconds = evidence_max_age_seconds
        if not math.isfinite(evidence_max_age_seconds) or evidence_max_age_seconds <= 0:
            raise ValueError("Evidence max age must be positive")
        if str(path) == ':memory:':
            raise ValueError('Durable coordination requires a database file')
        self.path = os.path.normcase(str(Path(path).resolve()))
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS transfers (
                id TEXT PRIMARY KEY, state TEXT NOT NULL,
                owner TEXT, eligible INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resource_binding (
                resource TEXT PRIMARY KEY, transaction_id TEXT NOT NULL);
        """)
        # Serialize schema upgrades so older demo databases remain usable.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(transfers)')}
            for name, definition in (
                ('username', "TEXT NOT NULL DEFAULT 'demo'"),
                ('seller', "TEXT NOT NULL DEFAULT 'seller'"),
                ('buyer', "TEXT NOT NULL DEFAULT 'buyer'"),
                ('resource', 'TEXT'),
            ):
                if name not in columns:
                    self.db.execute(f'ALTER TABLE transfers ADD COLUMN {name} {definition}')
            self.db.execute('''UPDATE transfers SET resource=(
                SELECT resource FROM resource_binding WHERE transaction_id=transfers.id LIMIT 1)
                WHERE resource IS NULL''')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()

    def create(self, transaction_id, username='demo', seller='seller', buyer='buyer'):
        for value in (transaction_id, username, seller, buyer):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError('Identifiers must be nonempty strings without surrounding whitespace')
        if seller == buyer:
            raise ValueError('Seller and buyer must be different accounts')
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute("""INSERT OR IGNORE INTO transfers
                (id,state,username,seller,buyer) VALUES (?, 'prepared',?,?,?)""",
                                     (transaction_id, username, seller, buyer))
            existing = self.db.execute('SELECT username,seller,buyer FROM transfers WHERE id=?',
                                       (transaction_id,)).fetchone()
            if tuple(existing) != (username, seller, buyer):
                raise ValueError('Transaction ID is already bound to different transfer details')
            if cursor.rowcount:
                self.db.execute("INSERT INTO events(id,state) VALUES (?, 'prepared')", (transaction_id,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.get(transaction_id)

    def get(self, transaction_id):
        row = self.db.execute("SELECT * FROM transfers WHERE id=?", (transaction_id,)).fetchone()
        if row is None:
            raise KeyError(transaction_id)
        result = dict(row)
        result['eligible'] = bool(result['eligible'])
        result['log'] = [dict(x) for x in self.db.execute(
            "SELECT seq,state FROM events WHERE id=? ORDER BY seq", (transaction_id,))]
        return result

    def transition(self, transaction_id, state, owner=None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE transfers SET state=?,owner=?,eligible=? WHERE id=?",
                            (state, owner, state == 'verified', transaction_id))
            self.db.execute("INSERT INTO events(id,state) VALUES (?,?)", (transaction_id, state))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def lock_path(self, kind, identifier):
        # Hash identifiers so user-supplied names never become filesystem paths.
        digest = hashlib.sha256(identifier.encode('utf-8')).hexdigest()
        return f'{self.path}.{kind}.{digest}.worker.lock'

    def run(self, transaction_id, platform, crash_after=None):
        # Always take the transaction lock first. It also prevents concurrent
        # first runs from binding one ID to two different platform resources.
        with execution_lock(self.lock_path('transaction', transaction_id)):
            record = self.get(transaction_id)
            if platform.username != record['username']:
                raise ValueError('Platform username does not match transaction')
            resource = platform.resource_key
            if record['resource'] is not None and record['resource'] != resource:
                raise ValueError('Transaction cannot switch platform resources')
            with execution_lock(self.lock_path('resource', resource)):
                return self._bind_and_run(transaction_id, platform, resource, crash_after)

    def _bind_and_run(self, transaction_id, platform, resource, crash_after):
        # Short SQLite commits serialize writes, but no database write lock
        # is held while waiting for platform operations.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT OR IGNORE INTO resource_binding VALUES (?,?)',
                            (resource, transaction_id))
            bound = self.db.execute('SELECT transaction_id FROM resource_binding WHERE resource=?',
                                    (resource,)).fetchone()[0]
            if bound != transaction_id:
                raise ValueError('This username is already bound to another transaction')
            self.db.execute('UPDATE transfers SET resource=? WHERE id=?', (resource, transaction_id))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return self._run(transaction_id, platform, crash_after)

    def verify_owner(self, platform):
        if not self.require_evidence:
            return platform.verify()
        reader = getattr(platform, 'verify_evidence', None)
        if reader is None:
            return False, None
        nonce = secrets.token_hex(16)
        try:
            evidence = reader(nonce)
        except Exception:
            return False, None
        if not isinstance(evidence, OwnershipEvidence):
            return False, None
        if type(evidence.observed_at) not in (int, float) or not math.isfinite(evidence.observed_at):
            return False, None
        if evidence.owner is not None and (not isinstance(evidence.owner, str) or not evidence.owner.strip()):
            return False, None
        age = time.monotonic() - evidence.observed_at
        if (evidence.authoritative is not True or evidence.nonce != nonce or
                evidence.resource != platform.resource_key or evidence.username != platform.username or
                not 0 <= age <= self.evidence_max_age_seconds):
            return False, None
        return True, evidence.owner

    def _run(self, transaction_id, platform, crash_after=None):
        record = self.get(transaction_id)
        if self.require_evidence and record['state'] == 'verified':
            known, owner = self.verify_owner(platform)
            state = ('unresolved' if not known or owner is None else
                     'verified' if owner == record['buyer'] else
                     'seller_retained' if owner == record['seller'] else 'competitor_capture')
            self.transition(transaction_id, state, owner if known else None)
            return self.get(transaction_id)
        if record['state'] in ('verified', 'competitor_capture', 'seller_retained', 'ownership_mismatch'):
            return record
        if record['state'] == 'prepared':
            known, owner = self.verify_owner(platform)
            if not known:
                # Stay retryable without issuing a release or claim.
                self.transition(transaction_id, 'prepared')
                return self.get(transaction_id)
            if owner != record['seller']:
                self.transition(transaction_id, 'ownership_mismatch', owner)
                return self.get(transaction_id)
            # Persist intent before an operation that may succeed without a response.
            self.transition(transaction_id, 'release_intent')
            platform.release(record['seller'])
            if crash_after == 'release':
                raise InjectedCrash('crash after platform release')
        known, owner = self.verify_owner(platform)
        if not known:
            self.transition(transaction_id, 'unresolved')
            return self.get(transaction_id)
        if owner is None:
            self.transition(transaction_id, 'claim_intent')
            platform.claim(record['buyer'])
            if crash_after == 'claim':
                raise InjectedCrash('crash after platform claim')
            known, owner = self.verify_owner(platform)
        state = ('unresolved' if not known or owner is None else
                 'verified' if owner == record['buyer'] else
                 'seller_retained' if owner == record['seller'] else 'competitor_capture')
        self.transition(transaction_id, state, owner if known else None)
        return self.get(transaction_id)


class PersistentFakePlatform:
    """Separate database survives coordinator process death; exact, case-sensitive names."""
    def __init__(self, path, verification_available=True, username='demo', initial_owner='seller'):
        if not username.strip():
            raise ValueError('Username is required')
        self.username = username
        # Preserve the old default resource key for existing demo bindings.
        self.resource_key = str(Path(path).resolve()) + (':username:1' if username == 'demo'
                                                       else ':username:' + json.dumps(username))
        self.db = sqlite3.connect(path, isolation_level=None)
        self.verification_available = verification_available
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS usernames (name TEXT PRIMARY KEY, owner TEXT);
        """)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            legacy = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='username'").fetchone()
            if legacy:
                self.db.execute("INSERT OR IGNORE INTO usernames SELECT 'demo',owner FROM username WHERE id=1")
                self.db.execute('DROP TABLE username')
            self.db.execute('INSERT OR IGNORE INTO usernames VALUES (?,?)', (username, initial_owner))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()

    def release(self, seller):
        self.db.execute('UPDATE usernames SET owner=NULL WHERE name=? AND owner=?', (self.username, seller))

    def claim(self, buyer):
        self.db.execute('UPDATE usernames SET owner=? WHERE name=? AND owner IS NULL', (buyer, self.username))

    def verify_evidence(self, nonce):
        if not self.verification_available:
            return None
        owner = self.db.execute('SELECT owner FROM usernames WHERE name=?', (self.username,)).fetchone()[0]
        return OwnershipEvidence(owner, self.resource_key, self.username, nonce, time.monotonic(), True)

    def verify(self):
        return (True, self.db.execute('SELECT owner FROM usernames WHERE name=?', (self.username,)).fetchone()[0]) if self.verification_available else (False, None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict-evidence', action='store_true', help='Require fresh ownership evidence and recheck saved success')
    parser.add_argument('--db', default='coordinator.sqlite')
    parser.add_argument('--platform-db', default='platform.sqlite')
    parser.add_argument('--id', default='demo-1')
    parser.add_argument('--username', default='demo')
    parser.add_argument('--seller', default='seller')
    parser.add_argument('--buyer', default='buyer')
    parser.add_argument('--crash-after', choices=('release', 'claim'))
    args = parser.parse_args()
    coordinator = Coordinator(args.db, require_evidence=args.strict_evidence)
    platform = PersistentFakePlatform(args.platform_db, username=args.username, initial_owner=args.seller)
    try:
        coordinator.create(args.id, args.username, args.seller, args.buyer)
        try:
            result = coordinator.run(args.id, platform, args.crash_after)
        except InjectedCrash as exc:
            result = {'interrupted': str(exc), 'transaction': coordinator.get(args.id)}
        except WorkerBusy as exc:
            result = {'busy': str(exc), 'retryable': True}
        print(json.dumps(result, indent=2))
    finally:
        coordinator.close()
        platform.close()
