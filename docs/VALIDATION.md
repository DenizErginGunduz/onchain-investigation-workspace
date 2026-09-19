# Validation record

Validation date: 19 September 2026. Application version: 0.1.0.

All 58 tests passed on the operational source after the demo change. The existing HTTP test now checks that the fixed demo endpoint supplies an offline-replayable synthetic bundle without creating a case on GET. It also checks rejection of arbitrary fixture and database paths.

JavaScript syntax checking passed. The demo uses the guarded bundle-import route and adds no provider requests.

The release source also passed all 58 tests and JavaScript syntax checking. Browser checks on a separate synthetic-only database verified one-click import, switching between source and receipt-transfer layers, saving a More information needed note, and reopening the case with its note intact. Screenshots were captured from the running application.

Test success is not security certification or financial-crime detection accuracy.
