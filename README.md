# Username transfer feasibility prototype

## Local product API and dashboard

Run `python product_api.py`, then open http://127.0.0.1:8766/. Copy the generated key from `artifacts/product-api/api.key` into the unlock field. The UI clears the entered key and uses an HttpOnly, SameSite=Strict session cookie. Create a transaction with fixture seller/buyer labels, execute it, inspect durable state transitions, and recheck ownership. Existing records survive service restarts. Reusing a transaction ID with different details is rejected.

This is one local administrator workspace, not multi-user marketplace authentication. The adapter is the persistent local fixture; entering a seller label seeds test ownership and does not authenticate an external account. No real money, escrow, or FOMO actions occur. The HTTP service binds only to 127.0.0.1 and rejects foreign Host/Origin headers. It requires a bearer key or local session for transaction access. Do not expose it as a public production service; public deployment needs TLS, user/role authorization, operational limits, and deployment hardening.

API reference: [docs/API.md](docs/API.md). Public build progress: [docs/PROGRESS.md](docs/PROGRESS.md).

## Browser transfer lab

### Bounded scheduled retries

```sh
python tune_scheduled.py --retry-study --validation-runs 20 --output artifacts/retry-study --keep-open
```

This compares fixed 10 ms single claim, 10 ms bounded retry, and 40 ms single
claim policies on fresh seeds 90000–90019. Each policy is tested against 0, 1
and 3 competitors under baseline, higher latency, timing uncertainty, an 80 ms
request interval with a 75 ms cooldown, and claim-response loss. Default total
is 900 trials. Policies are recorded before the comparison; results do not feed
parameter selection.

Retry allows at most three claim attempts, with a 500 ms deadline for starting
attempts. It reads registry ownership before and after claims, stops after a
buyer win or competitor capture, respects the configured request interval and
server retry delay, and reconciles an unknown response through ownership. An
unknown response without verified ownership stops rather than blindly retrying.
Already-started operations can finish beyond the deadline; they are not canceled.
The local read-only ownership endpoint is exempt from the modeled claim budget.
This is an explicit simulator assumption, not a claim about a real platform.

Per-attempt traces and server events are retained, including rate-limit responses,
failed early claims and unknown responses. Retry and single-claim results remain
separate even at the same offset. The experiment also verifies the three-claim
cap on the server's observed buyer requests. No real accounts are accessed.

### Scheduled offset tuning and holdout validation

```sh
python tune_scheduled.py --tuning-runs 10 --validation-runs 20 --output artifacts/offset-study --keep-open
```

The tuning stage tests 0, 5, 10, 20, 40 and 60 ms buyer offsets against 0, 1 and
3 competitors on seeds starting at 10000. Selection requires at least 90% clean
control wins, then maximizes the worse of the two contested win rates, followed
by their mean and then the smaller offset. This is a prespecified exploratory
rule with small tuning samples, not a reliability guarantee.

The choice is saved in `selection.json` before validation. Holdout seeds start
at 50000 and never feed selection. The frozen choice and the incumbent 40 ms
offset are compared on paired seeds under baseline, higher modeled latency,
and higher jitter with seller timing error and faster competitor attempts.
Parameter combinations are shuffled within each seed. Groups remain separate
by phase, scenario, offset and competitor count. If no candidate qualifies,
only the incumbent is validated; duplicate offsets are not run twice.

Default maximum is 540 transfers (180 tuning + 360 validation). This is an
initial study: the validation sample is 20 attempts per condition, so results
are provisional. All attempts, including errors, stay in the denominator;
buyer exposure timing is conditioned on verified buyer wins. The two-context
Playwright runner includes browser/automation overhead and is not the same
execution path as the single-tab UI button. Prior reports remain untouched.
Reports and ledgers persist; interrupted tuning studies require a fresh output
folder rather than silently selecting from incomplete results.

### Automated batch benchmark

```sh
python batch_lab.py --runs 100 --port 0 --output artifacts/my-batch --keep-open
```

This runs 900 transfers: 100 per combination of three strategies and 0, 1, or 3
competitors. A headless browser runner executes them sequentially, reusing two
isolated browser contexts. The read-only watcher checks every result. Strategies
and competition levels are interleaved; seeds 1000–1099 are paired across groups.
All other model settings stay fixed. The printed `/batch` URL shows live progress.
`--port 0` selects a free local port. `--keep-open` retains the result page after
completion; Ctrl+C closes the progress server.

`report.json` is checkpointed after every attempt. `results.jsonl` includes each
outcome, seed/configuration, coordinator record, watcher findings and server
events. Errors count as unresolved rather than disappearing from success rates.
Existing result folders are rejected to protect prior evidence. A stopped batch
is marked interrupted and is not described as complete. It does not resume
automatically. Resume the saved ledger explicitly with the same run count and
output folder plus `--resume`; completed seed/strategy/competition keys are
skipped, including failed attempts. Browser restarts can change real scheduling,
and resumed reports identify that fact. Success rates are grouped by strategy and competitor count.
Buyer exposure median/p95 use verified-buyer wins only (nearest-rank percentile),
with sample counts; competitor acquisition times are kept separate. Watcher
findings and rate-limit counts are included. Results are model-dependent and
do not establish live platform reliability.

### One-click transfers and live watcher

Create a fresh test, then click **Run automated transfer**. Choose a scheduled,
release-confirmed, or availability-polling strategy. The scheduled strategy uses
the configured buyer offset; polling has a three-second deadline. Both account
actions execute in the same browser tab. This manual-lab automation does not
write coordinator records; use the Python runner for persisted transactions.

The read-only watcher refreshes every 750 ms and observes server snapshots. It
checks duplicate winners/releases, acquisition before release or cooldown,
ownership/event disagreement, timestamp regression, rate limits, and lost
responses. It reports release-to-acquisition exposure and server receipt of the
release request to acquisition. Timing spread uses population standard deviation.
Aggregate timing combines conditions and includes competitor wins, so it is not
a strategy performance comparison or a guaranteed-delivery rate. Prepared,
in-progress and unresolved tests remain visible in outcome counts. The watcher
does not execute transfers, call an LLM, or prove the absence of vulnerabilities.

Watcher history exists for the current server process only. Download its JSON
report before restarting. The server registry is the ground truth in this lab,
not an independent external audit. The UI cannot cancel requests already in
flight if a tab closes; full crash recovery remains in the separate durable
coordinator prototype.

```sh
python -m pip install -r requirements-lab.txt
python browser_lab.py
```

Open http://127.0.0.1:8765 and create fixture accounts. Seller and buyer controls
appear side by side in the same tab, with prepared handles and no typing needed.
Click step 1 to release the seller's handle, then step 2 to claim for the buyer.
The buyer button is disabled until release; completed or captured transfers show
a clear outcome. Create a fresh test to repeat. The dashboard shows server-observed owner,
events, and the measured release-to-acquisition exposure window. Manual runs do
not write coordinator records; the automated runner does.

```sh
python run_browser_lab.py --runs 3
python run_browser_lab.py --headed --runs 1
python run_browser_lab.py --competitors 3 --runs 3 --output artifacts/contested
python run_browser_lab.py --cooldown-ms 200 --runs 1 --output artifacts/cooldown
```

The runner uses installed Microsoft Edge Chromium by default. For bundled
Chromium, run `python -m playwright install chromium` and pass `--channel ""`.
It starts a loopback server, creates isolated browser contexts, prepares forms,
and executes scheduled, release-confirmed, and availability-polling strategies.
It uses the existing coordinator for transaction identity, locks, persisted
intent, and ownership reconciliation. Scheduled/polling browser operations may
start during the release phase; verification waits for those operations to drain.

Each initiated run has a unique transaction and target username. Report JSON,
transaction database, and latest screenshot are written to `artifacts/browser-lab`
by default. Failed runner attempts are recorded as unresolved and stop the batch;
they are not silently excluded. Reports include all recorded outcomes, server
receipt/processing events, per-session browser observations, request counts,
and exposure duration. Browser performance clocks are session-relative and must
not be subtracted across sessions; use server clock events to compare actors.

Config models network delay, processing, jitter, cooldown, rate limiting, and
lost buyer responses. Runner options also configure seller schedule error,
buyer offset, and polling interval. Defaults are invented model assumptions.
Competitors submit periodic claims through the same registry/rate-limit path
without learning the private seller schedule or receiving a release signal.
Polling and claiming share a request budget, so an availability check can make
the subsequent claim rate-limited. Scheduled and confirmed strategies attempt
one claim; polling times out after three seconds. No guaranteed acquisition or
millisecond polling accuracy is assumed.

This is a real-time local experiment: seeds fix delay distributions, but actual
OS, browser, HTTP, and thread scheduling remain variable. Results are not FOMO
measurements. The fake platform's ownership registry and event stream are
in-memory; coordinator records and exported reports persist. Full browser-lab
crash recovery, real account authentication, and live integrations are outside
this milestone. The HTTP server is for local development only.

Offline Python prototype of releasing a username and attempting to acquire it
for a buyer. No external accounts, networking, payments, or AI inference.
Uses only the Python standard library (Python 3.10+).

```sh
python transfer_sim.py demo
python transfer_sim.py demo --competitors 100
python transfer_sim.py benchmark --runs 100 --seed 42
python -m unittest -v
```

The seeded discrete-event model processes claims in simulated arrival order.
Individual claims are atomic; the release-and-claim sequence is not. Competitors
make one attempt each, uniformly distributed over 25 simulated milliseconds
after release. Buyer jitter is uniform over 0–5 milliseconds. These are explicit
model assumptions, not measurements of FOMO or any other platform.

Outcomes: verified buyer ownership, competitor capture, seller retained after
rejected release, or unresolved ownership. Settlement eligibility requires
verified buyer ownership, even if a claim response is lost. Eligibility is a
simulation flag, not a payment operation. Logs use simulated time, not measured
network latency. No automatic rollback is claimed or attempted: a competitor's
acquisition cannot be undone by the coordinator.

## Durable recovery demo

```sh
python durable_transfer.py --crash-after release
python durable_transfer.py
```

The first command persists release intent and interrupts after the fake platform
releases the username. The second opens the same two SQLite databases, checks
ownership, and attempts acquisition only if the username is available. Use
`--crash-after claim` with fresh database paths to exercise acquisition recovery.
Transaction IDs make creation and completed runs repeatable without replaying
side effects. State and event-log writes commit together. Unknown verification
blocks completion and can be reconciled on a later run.

This recovery demo supports multiple local coordinator processes, multiple fake
usernames, and transaction-specific seller and buyer identities. It is separate
from the race benchmark. It does not implement real payments or delayed
in-flight platform requests.
An intent without an observed side effect is resolved conservatively; it does
not blindly repeat release. Stored eligibility is historical verification, not
permission to pay without a new ownership check.

## Concurrent workers

All workers must use the same coordinator database on the same machine and
access execution through `Coordinator.run`. A nonblocking operating-system file
lock protects each transaction ID and each platform username separately. Independent
usernames can execute in parallel, including while another worker waits on a
platform operation. A competing worker receives
`WorkerBusy` and can retry later; it makes no transfer changes. The lock remains
held while a worker is paused and is released by the operating system on process
death. Lock filenames use hashed identifiers. Never delete or replace lock files
while workers are running. Stop all old workers before upgrading from the global
lock version: workers using different locking versions must not run together.

Each fake username is permanently bound to its first transaction ID in this
prototype, preventing another transaction from treating existing buyer ownership
as its own successful transfer. New demos need fresh database paths. This is
local coordination, not a distributed lease system. Different coordinator
databases do not coordinate with each other. Remote adapters with requests that
can finish after worker death require additional platform-side guarantees.

The test suite starts real child processes, pauses two independent transfers
after release at the same time, checks that same-username and same-transaction
contenders cannot execute, and terminates a worker to verify recovery. SQLite
write commits still serialize briefly; database transactions are never held
across platform operations. Use separate Coordinator and platform connections
per worker; do not share a SQLite connection between threads.

## Scope and next steps

This is a feasibility model, not a production transfer service. The platform
interface is intentionally narrow. Future work includes account authentication,
fresh verification before settlement, and lifecycle support for resales.
Additional simulator scenarios should cover release-response loss,
stale verification, cooldowns, retries, and time-varying rate limits before a
marketplace or settlement system is connected. Real integration requires
verified platform capabilities and permission; this prototype makes no claim
about FOMO's terms or transfer support.

User-facing promise: transfers can fail and usernames can be captured by others
after release. A simulation success does not establish real platform reliability.

## Multiple usernames and account identities

```sh
python durable_transfer.py --id alpha-1 --username alpha --seller alice --buyer bob
python durable_transfer.py --id beta-1 --username beta --seller carol --buyer dana
```

Both commands share the default databases. Each transaction permanently records
its username, seller, and buyer; reusing an ID with changed details is rejected.
Recovery uses these persisted identities. The platform resource is bound on
first execution and cannot be swapped during recovery. Seller ownership must be
verified before release: a mismatched owner (even the intended buyer) is an
`ownership_mismatch`, never a successful transfer. Unavailable initial verification
leaves the transaction prepared for retry without changing platform ownership.

The fake platform seeds a previously unseen username with the supplied seller.
That is test fixture creation, not authentication or proof of real ownership.
Reopening an existing username preserves its owner. Names and account IDs are
exact and case-sensitive. Existing single-username databases migrate to `demo`
with the original `seller` and `buyer` identities, preserving ownership and
resource bindings. A username remains bound to one transaction; resales require
a future explicit lifecycle. Local workers use separate execution locks for
independent platform usernames and transaction IDs.

### Independent experiment director

Run `python experiment_director.py --source artifacts/retry-holdout/results.jsonl --output artifacts/director` after a benchmark. Open `artifacts/director/index.html` for its report. It reads raw ledger events independently of watcher conclusions, audits terminal ownership and retry budgets, injects eight known faults into copied local snapshots, and emits prioritized hypotheses with pass conditions. It does not modify benchmark evidence or automatically promote policies.

This first director is a deterministic audit and proposal worker, not an ongoing LLM creative agent. Rerun it against a new ledger after each experiment. Historical timing comparisons allow 1 ms because transition and event clocks were sampled separately; new transition events now use the exact state timestamp. Detection of eight planted faults is evidence about those cases only, not a comprehensive security assessment. Smarter competitor and crash/stale-read campaigns are proposed, not yet executed by this worker.

### Adversarial race campaign

`python adversarial_races.py` runs 540 direct threaded server-model races: 20 fresh seeds per combination of three stress conditions, three policies, and 0/1/3 competitors. Policies and scenarios are frozen in `plan.json` before execution, results are flushed to `results.jsonl`, and each record receives both watcher checks and an independent director audit. Output must be a fresh directory. This campaign removes browser overhead and must not be pooled with browser benchmark success rates. Competitor intervals describe waits between completed claim requests; all actors retain the same modeled request latency and budget. Lower intervals stress frequency, not privileged backend access or faster competitor networks. No policy is automatically promoted.

### Frozen adaptive timing pilot

`python adversarial_races.py --adaptive-study --runs 10 --seed-start 150000 --output artifacts/adaptive-holdout` compares retry10, single40, and adaptive_retry in four conditions with 0/3 competitors (240 trials). Every policy gets the same two preflight requests, one availability RTT per actor. Adaptive timing uses only those measured round trips: clamp(round((seller RTT - buyer RTT)/2 + 0.15 * median RTT), 10, 60) ms. The formula is frozen before holdout and is a candidate heuristic, not an established optimal policy. Retry admission is capped at three claims and 500 ms; already submitted requests may finish later. The latency_shift case changes modeled network and jitter after calibration to test stale estimates. Ownership snapshots remain immediate reads in this simulator. Results are separate from browser runs, one-sample calibration is noisy, and ten attempts per condition are provisional. No automatic promotion occurs.

### Strict evidence and real process recovery

`python recovery_campaign.py` runs 11 local cases with actual spawned worker exits after release or claim. `python durable_transfer.py --strict-evidence` enables the opt-in hardened contract for the persistent fixture. Evidence is bound to a fresh request nonce, exact platform resource and username, an authoritative-source flag, and a monotonic observation age of at most one second. Strict mode rechecks a saved verified record on restart. Missing or rejected evidence cannot grant eligibility. See SECURITY.md for the trusted-adapter boundary and legacy-mode limitations. This is not a guarantee against fabricated evidence from an untrusted source.

Public build progress: [docs/PROGRESS.md](docs/PROGRESS.md). GitHub CI is configured for Windows and Linux; hosted checks become meaningful once the repository is published and those runs complete.

### Browser strict evidence backtests

`python run_browser_lab.py --strict-evidence --runs 2 --output artifacts/browser-evidence-none` uses the evidence gate through the browser adapter. Add `--evidence-fault stale`, `replayed`, or `delayed` to inject a post-release read fault; delayed evidence waits 1.1 seconds after observing ownership, exceeding the one-second freshness budget. These faults leave coordinator outcomes unresolved even if the independent final fixture snapshot shows buyer ownership. Reports retain both the coordinator outcome and `observed_outcome` so apparent platform success is not confused with sufficient evidence for eligibility. The adapter's live local HTTP read is its trusted authority; its nonce is generated locally, not attested by FOMO. This does not test real FOMO caching or cryptographic freshness.

### Browser race and session interruption campaign

`python browser_recovery_campaign.py --runs 1 --output artifacts/browser-race-recovery` executes 48 cells: three strategies, 1/3 competitors, fresh/stale/replayed/delayed evidence, with/without buyer-session closure after release. Each cell has one attempt by default; this is a behavior check, not a reliability estimate. The fixture server stays alive. The closed buyer browser context is replaced, strict verification resumes from persisted intent, and a later fresh read reconciles rejected evidence. The independent checks require exactly one release event and one release request, no eligibility from injected faulty evidence, and no eligible result inconsistent with terminal fixture ownership. Browser closure can abort HTTP responses while server operations finish; these transport disconnects are distinct from runner failures and are not counted by this campaign. Coordinator OS-process death remains covered by the separate recovery campaign. Competitor workers are stopped and in-flight competitor requests drained before final reconciliation; this controlled endpoint behavior is a simulator assumption.

### Strict acquisition comparison

`python tune_scheduled.py --acquisition-study --validation-runs 20 --output artifacts/strict-acquisition-holdout` freezes 10/20 ms bounded retry policies and a 40 ms single-claim baseline before running 540 local browser trials. Seeds 200000–200019 are new and paired across policies. All policies use strict ownership evidence; each receives the same readiness and terminal evidence checks. Three latency conditions use a 20 ms competitor wait interval with 0/1/3 competitors; clean controls remain visible. Rate budgets, browser contexts, and deadlines are shared; retry policies intentionally permit up to three claims while the baseline permits one, and request costs are reported. Combinations are shuffled per seed. Errors remain in denominators and raw logs are flushed per trial.

Review with `python acquisition_review.py artifacts/strict-acquisition-holdout/results.jsonl --output artifacts/strict-acquisition-holdout/review.json`. A candidate can only qualify for a larger holdout if all clean controls verify, no contested condition regresses against the baseline, and at least one improves. Even a qualifying candidate is not promoted automatically. Twenty attempts per condition remain provisional. This comparison has no browser interruptions or stale-read injections; those belong to the separate behavior campaigns.
