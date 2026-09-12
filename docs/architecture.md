# Architecture

## Runtime flow

1. React requests a batch and per-document upload SAS from the Upload API.
2. The browser uploads each document directly to Blob Storage and calculates SHA-256.
3. The browser calls completion; the API verifies the blob exists, records Blob version/checksum, and sends one deterministic Service Bus message for the document.
4. The Service Bus trigger uses PeekLock/manual settlement with `maxConcurrentCalls=8` **per Function host instance**.
5. The worker atomically claims the document, increments its processing attempt, downloads the exact Blob version and verifies SHA-256.
6. The first Excel worksheet is parsed. The standard load-test document contains 153 data rows and 10 columns.
7. On the first processing attempt, the worker takes the normal fast path. On retries, it loads already-finished case IDs so successfully persisted rows are not repeated unnecessarily.
8. Reference values are loaded from Azure SQL for the required keys and the SQL comparison result is calculated.
9. Validation is performed in batches. The current development mock runs in-process to avoid a Function-to-self HTTP hop; a real external validator can still use the batch HTTP adapter.
10. Processed rows are staged with `SqlBulkCopy` and merged set-wise into the `Case` and `DocumentCaseLink` tables.
11. Case persistence, document final status and the final audit event are committed in the same SQL transaction. No slow Blob, parsing or validation work is performed while that transaction is open.
12. The Service Bus message is completed only after the durable SQL transaction succeeds.
13. The batch-status API derives the current aggregate status from `DocumentUpload` state rather than requiring every document worker to contend on the same batch row.
14. A reconciliation timer updates stored batch status and handles long-stuck processing state.
15. A separate recovery watchdog runs every minute and only considers genuinely stale `PROCESSING` documents after the Service Bus lock/auto-renewal window. It requeues with a deterministic recovery message ID and stops after the configured recovery attempt ceiling.
16. Application Insights and Log Analytics provide request, dependency, exception and Function telemetry.

## Reliability guarantees

- At-least-once Service Bus delivery with application-level idempotency.
- One queue message per document, not per row.
- Deterministic original Service Bus `MessageId=documentId` with one-hour duplicate detection.
- Deterministic recovery message identity per processing attempt.
- Deterministic case identity based on `(documentId,rowNumber)` plus SQL uniqueness.
- Queue settlement occurs only after the final durable SQL transaction commits.
- Blob versioning and SHA-256 protect against processing the wrong or changed content.
- Case persistence and document finalization are atomic with respect to each document.
- Retry processing can skip cases already in a finished state.
- Bounded document concurrency protects Azure SQL and the validation service.
- Recovery is a safety net for orphaned `PROCESSING` state, not the normal processing path.

## Concurrency model

The current `host.json` uses:

```json
{
  "serviceBus": {
    "prefetchCount": 0,
    "autoCompleteMessages": false,
    "maxConcurrentCalls": 8,
    "maxAutoLockRenewalDuration": "00:10:00"
  }
}
```

`maxConcurrentCalls=8` applies to a Function host instance. If the platform scales to multiple instances, total concurrent documents can exceed eight. A production scale-out configuration must therefore consider:

```text
aggregate concurrency ~= active Function instances x per-instance concurrency
```

The correct value is constrained by downstream SQL and validation capacity, not by the number of queued documents.

## Current infrastructure profile

The development/test Bicep profile uses:

- StorageV2 Standard LRS with Blob versioning;
- Service Bus Standard;
- duplicate detection window: 1 hour;
- queue lock duration: 5 minutes;
- max delivery count: 5;
- default message TTL: 7 days;
- Azure SQL Standard S0;
- Function Linux Consumption Y1;
- .NET isolated 8.0;
- SQL `Max Pool Size=15`;
- 55 MiB maximum file size;
- maximum 200 files in one upload-prepare request.

This profile is intentionally cost-sensitive and is the configuration used for the final 42.96-second 150-document validation run.

## Validated workload

The standard synthetic workbook contains:

```text
153 data rows x 10 columns
```

The current validated 150-document workload therefore contains:

```text
150 x 153 = 22,950 cases
```

Latest measured result:

```text
150 documents
22,950 cases
42.96 seconds end-to-end
0 failed
0 DLQ
```

See [`project-conclusion.md`](project-conclusion.md) for the complete benchmark history and configuration-scaling projections.

## Batch scaling

The processing side is queue-based and can absorb a backlog, but the current upload-prepare API accepts a maximum of 200 files per request.

For a future 500+ document user-visible job, the recommended model is one logical batch split into smaller preparation/upload chunks (for example 50-100 documents per chunk), while retaining one Service Bus message per document and bounded downstream processing.

A 500-document run with the current synthetic workbook shape would contain 76,500 cases.

## Transaction boundaries

The design intentionally separates slow work from the final persistence transaction:

```text
Claim transaction
    -> commit

Blob download / checksum / Excel parse / reference read / validation
    -> no long SQL transaction held

Final persistence transaction
    -> bulk/set-based Case persistence
    -> DocumentCaseLink persistence
    -> DocumentUpload final status
    -> final AuditEvent
    -> commit

Service Bus complete
```

This prevents SQL connections/transactions from being held while waiting for slow external work and eliminates the earlier durable state gap where cases could commit while the parent document remained `PROCESSING`.

## Batch status model

Document workers do not update the shared `UploadBatch` row for every completion. The status endpoint derives the live batch status from indexed document state and case counts. Reconciliation periodically updates the stored aggregate batch row outside the document critical path.

This avoids a hot-row write bottleneck when many documents from the same batch complete concurrently.

## Production security note

The current deployment profile is intended for an isolated development/test subscription and uses CORS plus short-lived write SAS URLs.

Before internet-facing production use:

- authenticate HTTP APIs using Microsoft Entra ID/App Service Authentication or an API gateway;
- replace SQL username/password authentication with managed identity / Entra database authentication;
- use least-privilege Service Bus/Storage RBAC rather than broad connection strings;
- place secrets and sensitive configuration behind managed identity/Key Vault patterns;
- use private endpoints/private networking where required by policy;
- introduce a transactional outbox for the strongest SQL-to-Service-Bus publication guarantee;
- add queue/DLQ/SQL/stuck-processing alerts and operational dashboards;
- add configurable external-validation fault injection for latency, 429, transient 5xx and permanent-failure testing;
- define a global concurrency policy for multi-instance production scale-out.
