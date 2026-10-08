# Security and verification scope

This is an offline local simulation and research prototype. There is no live platform integration, production escrow, payment settlement, or platform-issued transfer reservation.

Strict evidence mode is enabled with `Coordinator(path, require_evidence=True)` or the durable CLI `--strict-evidence`. It rejects missing, expired, future-dated, replayed, non-authoritative, and resource/handle-mismatched evidence, and rechecks saved success when execution resumes. Rejected evidence yields a retryable prepared or unresolved state rather than verified ownership.

The evidence adapter is a trusted boundary. Its authority flag and observation timestamp are not cryptographic platform attestations. A dishonest adapter could fabricate fresh metadata around cached ownership. The persistent local fixture reads its database directly; a real adapter would require a documented authoritative read contract. Ownership can also change after a valid observation. These tests establish behavior for injected cases, not a universal guarantee.

Legacy tuple-based adapters remain supported in default mode and are not hardened by this evidence gate. The browser runner supports opt-in `--strict-evidence`; existing batch/tuning runs still use the legacy contract. Strict mode fails closed for adapters without `verify_evidence`.

Reports may contain local fixture identifiers and filesystem paths. Runtime databases, raw artifacts, credentials, and account sessions are excluded from the public source tree. Public progress summaries should distinguish server-model and browser measurements and include failures as well as successes.
