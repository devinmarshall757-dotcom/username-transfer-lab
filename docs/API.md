# Local product API

Base URL: `http://127.0.0.1:8766`. The service is loopback-only and uses a persistent local test fixture. No live accounts or payments are connected.

Authenticated requests use `Authorization: Bearer YOUR_LOCAL_KEY`. The key is created in the configured data directory's `api.key`; never commit or publish it. JSON mutations require `Content-Type: application/json`. Browser requests must originate from the exact local base URL; foreign origins and hosts are rejected. This is a single administrator credential, not buyer/seller authorization.

| Method | Route | Behavior |
|---|---|---|
| GET | `/health` | Public health and explicit local-adapter scope |
| POST | `/session` | Exchange authenticated request for local HttpOnly/SameSite cookie |
| POST | `/v1/transactions` | Create or idempotently return immutable transaction details |
| GET | `/v1/transactions` | Most recent 100 transactions, including recorded transitions |
| GET | `/v1/transactions/{id}` | Read one persisted transaction |
| POST | `/v1/transactions/{id}/execute` | Strictly verify and execute/reconcile local fixture ownership |

Create body:

```json
{"id":"transaction-001","username":"example-handle","seller":"alice","buyer":"bob"}
```

All four values must be 3–64 letters, digits, underscores, or hyphens. Seller and buyer must differ. The execution body is `{}`. Creation returns 201; an identical repeat returns 200. ID conflicts, resource reuse, or a busy worker return 409. Unknown IDs return 404; missing/invalid credentials return 401. The platform resource remains permanently bound to its first transaction in this prototype; resale lifecycle is not implemented.

Execution uses strict evidence through `Coordinator.run`. The local fixture's direct database read is the trusted ownership source. Persisted `eligible` is a simulation field recording a verified observation, not payment authorization. GET does not refresh ownership; POST execute rechecks previously verified records. A fresh observation can become outdated later.

Run with `python product_api.py --port 8766 --data artifacts/product-api`. The browser dashboard is served at `/`. Keys, runtime databases, reports, and screenshots are ignored by Git. Regenerating/changing the key invalidates sessions once the service restarts. There is no separate session revocation store or multi-user tenancy yet. Loopback HTTP cookies lack Secure; a production web service would need HTTPS and additional controls. All existing lab/benchmark APIs remain separate; this service does not retrofit their authentication.
