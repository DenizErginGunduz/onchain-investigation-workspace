# Architecture for reviewers

The browser uses the loopback Python HTTP host. Providers map input into snapshot 1.0. The domain owns event IDs, exact amounts, coverage and source policy. SQLite owns cases and append-only notes. A provider switch changes subsequent acquisition rather than rewriting historical evidence.

## Main modules

| Component | Responsibility |
| --- | --- |
| server.py | Local host/origin/header guard, bounded JSON API and static assets |
| app/domain.py | Snapshot validation, canonical evidence IDs, exact unit display and reports |
| app/providers.py | Authored examples, snapshot adapter and bounded public activity adapter |
| app/reconciliation.py | Chain, transaction, receipt, canonical block position and log checks |
| app/bundles.py | Archived raw-response integrity and role checks; offline replay |
| app/settlement.py | Narrow supported settlement interpretation after receipt checks |
| app/tracing.py | Filtered log validation, selected receipt matches and bounded continuation state |
| app/storage.py | Atomic case insertion, reopening/replay and append-only notes |
| dist/app.js | Source/transfer layers, graph, ledger, receipt panel and analyst controls |
| app/collect_case.py, app/review_case.py, app/trace_case.py | Optional bounded manual acquisition; excluded from the offline demo workflow |

## Trust and portability

Imported RPC hashes detect changes to the supplied body but do not authenticate the upstream source. TLS and retrieval declarations are imported claims. Corroboration uses one source and is not a consensus proof. Derived receipt/trace results are recomputed on reopening. Ordinary snapshot imports strip reserved receipt and trace fields.

The portable contract does not promise equivalent proprietary coverage or data rights. Integrations need explicit source capabilities, license/export policy and migration checks. Raw data, secrets and SQLite belong outside source control. A future hosted product requires a production host, authentication and stronger isolation.
