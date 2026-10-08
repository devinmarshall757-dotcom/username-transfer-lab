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
- Local suite now passes 63 tests; hosted CI for this integration is pending publication.

## Next experiments

1. Extend strict browser evidence tests to competitor races and interrupted browser sessions.
2. Test recovery after evidence becomes available again.
3. Expand the holdout sample before considering any policy promotion.

## Reproduce

Install `requirements-lab.txt`, then run `python -m unittest -q` and `python recovery_campaign.py`. See README for browser and race studies. Generated reports and databases stay in ignored `artifacts/`; publish selected aggregate evidence with its scope and reproduction command here. This log records completed work rather than scheduled promises of daily updates.
