# Evidence contracts and bounded tracing

Snapshot 1.0 has `schema_version`, `synthetic`, `subject`, `source`, `coverage` and at most 200 `events`. IDs use chain, transaction, exact locator and event kind. Provider row identities are not chain log positions. Amounts are integer strings; decimals are applied only to display. A bridge destination requires explicit correspondence evidence and remains a supplied assertion.

Bundle 1.1 contains `bundle_version`, `title`, `snapshot` and one to three `evidence` sets. Each set supplies raw receipt, transaction, block, head and chain JSON-RPC records. Optional finality records require both a finalized tag and its numbered canonical block. Optional metadata supports known collateral getters at the included receipt/finalized block.

A raw record carries source URL, request, retrieval time, HTTP status, declared TLS verification, exact UTF-8 body and SHA-256. Request method/params and response ID must match; hashes do not establish authenticity. No imported URL is fetched. All bundle imports force exports off. Reuse rights must be decided separately.

Bundle 1.2 also requires `trace` version 1.0: scope, chain/head, boundary blocks and subject incoming/outgoing plus selected-recipient outgoing log queries. Window cap: 1000 blocks. Result cap: 500 logs per query. Raw response cap: 1 MB. Packaged bundle cap: 900 KB; HTTP body cap: 1 MB. Selected transfers must match receipt and canonical block evidence exactly. Discovery logs alone are not promoted into the graph.

The trace ledger shows raw integers with an explicit raw-display convention. Source activity stays separate. Same address, temporal order or equal value does not establish common ownership or same-fund continuity. Later outgoing discovery is an unverified candidate; completed second-hop receipt support is not implemented. Other assets, native/internal transfers, outcome tokens and other chains are outside this trace method.

See `fixtures/trace-demo.json` and `tests/test_tracing.py` for an authored, replayable example. The demo's large integer is deliberate precision-test data, not an observed transfer or economic claim.
