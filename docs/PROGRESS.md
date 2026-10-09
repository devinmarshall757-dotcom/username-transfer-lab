# Public development progress

This repository studies username transfer coordination in an offline local simulator. It has no live FOMO integration. These experiments do not establish live-platform success rates or a comprehensive security assessment.

## 2026-10-08

- Built an independent experiment director that checks raw events and audits the watcher with eight planted faults. All eight are detected after adding exposure consistency checks.
- Completed 540 adversarial server-model races across fixed timing policies, competitor counts, and latency conditions. No fixed policy dominated all conditions.
- Completed a 240-trial frozen adaptive timing pilot on unseen seeds. Kept it experimental because it did not consistently improve contested outcomes.
- Added opt-in strict ownership evidence to the durable coordinator: request nonce, resource and handle binding, authoritative-source flag, and a maximum evidence age. Cached success is rechecked in strict mode.
- Ran 11 recovery/evidence cases using spawned processes that exit after release or claim. All cases passed; zero false verified results against the persistent fixture.
- Published the public repository; initial GitHub CI passed on Windows and Linux.
- Integrated opt-in strict evidence into the browser runner. Completed 24 Edge browser checks: 6 fresh-evidence cases verified, 18 stale/replayed/delayed cases remained unresolved, zero false verified outcomes. See browser-evidence-backtest.json.
- Strict browser integration CI passed on Windows and Linux.
- Completed 48 browser race/recovery cells with 1/3 competitors and 24 buyer-session closures. All 36 faulty evidence reads blocked eligibility until fresh reconciliation. No release replay or false verification. Final outcomes: 2 verified, 46 competitor captures. See browser-race-recovery-backtest.json.
- Combined browser race/recovery campaign CI passed on Windows and Linux.
- Completed 540 strict-evidence acquisition trials with fresh paired seeds. The fixed 20 ms retry candidate matched or beat the 40 ms single baseline in all six contested cells and passed clean controls; it qualifies only for a larger holdout. The 10 ms retry candidate regressed in two cells. Zero runner errors or independent audit disagreements. See strict-acquisition-backtest.json.
- Local suite now passes 68 tests; hosted CI for the acquisition review is pending publication.

## Next experiments

1. Test browser/server process failure and delayed operations that survive worker death, beyond buyer-session closure.
2. Test recovery after evidence becomes available again.
3. Validate frozen 20 ms retries against 40 ms single claims on a larger unseen-seed holdout before promotion.

## Reproduce

Install `requirements-lab.txt`, then run `python -m unittest -q` and `python recovery_campaign.py`. See README for browser and race studies. Generated reports and databases stay in ignored `artifacts/`; publish selected aggregate evidence with its scope and reproduction command here. This log records completed work rather than scheduled promises of daily updates.

- Built and browser-tested an authenticated loopback product API/dashboard over the durable local fixture. It supports immutable/idempotent creation, authenticated status reads, strict-evidence execution/reconciliation, and recorded transitions. Foreign Host/Origin requests are rejected; runtime keys and databases stay out of Git. This is a single local administrator workspace, not a live FOMO marketplace or payment service.
- Local suite now passes 75 tests, including seven API integration checks. Headless Edge smoke test passed unlock → create → execute → verified, with the entered access key cleared. Hosted CI for the API commit is pending publication.
