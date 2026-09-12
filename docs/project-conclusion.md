# Document Upload Processing - Project Report and Conclusion

**Status:** Architecture validation complete for the current development/test target  
**Validated implementation:** .NET 8 Azure Functions + React + Python load tests + Bicep + Azure SQL  
**Latest validated code baseline:** `dda4c9d` (`Optimize document processing hot paths`)  
**Validation date:** 2026-09-12

## 1. Executive summary

This project redesigns document ingestion so a burst of uploaded files does not create uncontrolled downstream database and validation concurrency.

The final validated design uses Blob Storage as the file system of record, Azure Service Bus as the asynchronous processing boundary, bounded .NET Azure Functions for document processing, and Azure SQL for durable batch/document/case/audit state.

The current low-cost development configuration processed **150 Excel documents containing 22,950 cases in 42.96 seconds**, with **0 failed documents** and **0 dead-lettered documents**. This is the current measured project baseline.

Compared with the original 153-document baseline of 168.53 seconds, normalized case throughput increased from about **138.9 cases/second** to about **534.2 cases/second**, approximately **3.85x throughput**. Normalized processing time per document was reduced by about **74%**.

This result was achieved without increasing the Azure SQL tier. The current environment intentionally remains cost-sensitive and uses Azure SQL Standard S0 and a Consumption (`Y1`) Function plan.

## 2. Problem being solved

The original processing pattern allowed a file upload burst to fan out into many overlapping workflows and synchronous processing requests. The downstream Azure SQL connection pool was limited, so excessive concurrency could cause requests to wait for a connection and eventually fail with pool-exhaustion/503 behavior.

The architectural objective was therefore not simply "add more parallelism." It was to establish a controlled processing boundary with explicit backpressure, idempotency, retry behavior, and short database transactions.

## 3. Final architecture

```text
React SPA
   |
   | 1. prepare upload
   v
.NET Upload API
   |
   | short-lived write SAS
   v
Azure Blob Storage
   |
   | 2. browser uploads directly
   | 3. completion records SHA-256 + blob version
   v
Azure Service Bus
   |
   | one message per document
   v
.NET 8 Service Bus Function
   |
   | bounded document concurrency
   | exact blob version download + checksum verification
   | Excel parse
   | reference comparison
   | batched/in-process validation
   | bulk SQL persistence
   v
Azure SQL
   |
   +-- DocumentUpload
   +-- Case
   +-- DocumentCaseLink
   +-- AuditEvent
   +-- ReferenceData
```

The queue decouples upload speed from processing speed. A burst of documents becomes durable queued work instead of an uncontrolled number of synchronous database consumers.

## 4. Current development/test configuration

| Component | Current configuration | Purpose |
|---|---|---|
| Frontend | React + Vite | Browser upload UI |
| Backend | .NET 8 isolated Azure Functions | Upload API, queue processor, validation mock, reconciliation |
| Test stack | Python 3.12 | End-to-end and load testing |
| Storage | Standard LRS StorageV2, Blob versioning enabled | Original document storage and immutable version processing |
| Service Bus | Standard | Durable document queue |
| Queue | `document-processing` | One message per document |
| Duplicate detection | 1 hour | Suppresses duplicate document messages |
| Queue lock | 5 minutes | PeekLock processing |
| Max delivery count | 5 | Retry ceiling before DLQ |
| Function hosting | Linux Consumption `Y1` | Cost-sensitive dev/test hosting |
| Function runtime | .NET isolated 8.0 | Backend runtime |
| Service Bus `maxConcurrentCalls` | 8 per Function host instance | Bounded processing concurrency |
| Service Bus prefetch | 0 | Conservative queue behavior |
| Auto lock renewal | 10 minutes | Allows long-running processing within the retry model |
| Azure SQL | Standard S0 | Cost-sensitive durable database |
| SQL connection pool | Max Pool Size = 15 | Explicit connection pool ceiling |
| Upload file limit | 55 MiB per file | API validation limit |
| Prepare request limit | 200 files | Current API batch preparation ceiling |
| Validation batch size | 200 by default, max 500 | Reduces validation call overhead |
| Recovery stale interval | 12 minutes default, minimum 11 minutes | Recovers genuinely orphaned `PROCESSING` documents |
| Recovery max attempts | 5 | Prevents infinite recovery loops |

### Important concurrency note

`maxConcurrentCalls=8` is a **per Function host instance** setting. If the hosting platform scales to multiple instances, aggregate processing concurrency can exceed eight. Production scale-out therefore requires an intentional global concurrency strategy or a carefully bounded instance count.

## 5. Processing and reliability model

### Upload path

1. The client requests upload preparation for one or more files.
2. The API creates the batch/document records and returns short-lived Blob SAS URLs.
3. The browser uploads directly to Blob Storage instead of proxying the file through the Function App.
4. The client sends completion with SHA-256.
5. The API records checksum/blob version and publishes one deterministic Service Bus message per document.

### Processing path

1. The worker atomically claims a document and increments its processing attempt.
2. The exact Blob version is downloaded.
3. SHA-256 is recalculated and compared with the client-provided checksum.
4. The first Excel worksheet is parsed; the workload test uses 153 rows x 10 columns per document.
5. First-attempt processing avoids retry-only SQL reads.
6. Required reference values are loaded from Azure SQL and comparison results are calculated.
7. The current local mock validator runs in-process to avoid Function-to-self HTTP overhead. An external validation endpoint remains supported through batched HTTP calls.
8. Processed rows are staged with `SqlBulkCopy` and merged set-wise.
9. Case persistence, document-case links, final document status, and final audit state are committed in one SQL transaction.
10. The Service Bus message is completed only after the durable SQL transaction succeeds.

### Idempotency and retry behavior

- Service Bus delivery is at-least-once.
- Original message identity is deterministic using the document ID.
- Case IDs are deterministic from `(documentId, rowNumber)`.
- SQL uniqueness and state transitions make redelivery safe.
- Retry attempts load already-finished case IDs and avoid reprocessing completed rows.
- Maximum normal Service Bus delivery is five attempts.
- A separate watchdog only recovers stale `PROCESSING` records after the normal lock/renewal window.
- Recovery messages use deterministic IDs per recovery attempt.
- Messages are dead-lettered on terminal Service Bus delivery failure.

## 6. Major logic improvements made during the project

### A. Queue boundary instead of uncontrolled fan-out

The queue became the explicit backpressure point. Documents wait durably in Service Bus instead of creating unbounded overlapping processing requests.

### B. Bounded document concurrency

The processor was configured with a controlled concurrency of eight per Function host instance instead of allowing every uploaded document to compete for SQL connections at once.

### C. Batched validation and bulk SQL persistence

Row-oriented validation/database operations were reduced by batching validation and using set-based/bulk database persistence.

### D. Upload-to-processor race protection

The worker can safely claim both `UPLOADED` and `QUEUED` documents, and the upload completion path only performs the `UPLOADED -> QUEUED` transition if the processor has not already advanced the document state.

### E. Atomic persistence and finalization

Earlier testing exposed a failure window in which all cases could be committed but the parent document could remain `PROCESSING`. The final implementation persists case work and finalizes the document in the same SQL transaction, then completes the Service Bus message.

### F. Removal of the batch hot-row write

Workers no longer update the same `UploadBatch` row every time a document finishes. The batch-status endpoint derives the live state from document state, while reconciliation updates the stored batch status separately. This removes a shared write hotspot from the document critical path.

### G. Azure SDK client reuse

Blob and Service Bus clients/senders are reused for the lifetime of the Function host instead of being reconstructed per request/document.

### H. First-attempt fast path

Retry-specific SQL reads are skipped during normal first-attempt processing, reducing SQL round-trips on the common path.

### I. In-process mock validation

The development mock no longer requires an HTTPS call from the Function App back to itself. Real external validation can still use the batch HTTP adapter.

## 7. Measured performance results

All figures below are actual observed end-to-end load-test results unless explicitly marked as an estimate.

| Stage | Documents | Cases | Elapsed time | Documents/sec | Cases/sec | Result |
|---|---:|---:|---:|---:|---:|---|
| Original full baseline | 153 | 23,409 | 168.53 s | 0.91 | 138.9 | PASS |
| First optimized clean full run | 150 | 22,950 | 55.14 s | 2.72 | 416.2 | PASS |
| Three concurrent batches of 50 | 150 total | 22,950 total | ~50.20 s wall clock | 2.99 | 457.2 | 3/3 PASS |
| Current optimized single batch | 150 | 22,950 | **42.96 s** | **3.49** | **534.2** | **PASS** |

### Current validated run

```text
Uploaded and queued 150 documents in 15.31s
150 completed
22,950 cases
0 failed
0 dead-lettered
COMPLETED
PASS in 42.96s
```

### Improvement from the original baseline

- Case throughput: **138.9 -> 534.2 cases/sec**
- Throughput multiplier: **~3.85x**
- Throughput increase: **~284.6%**
- Normalized time/document: **1.10s -> 0.286s**
- Normalized time/document reduction: **~74.0%**

### Improvement from the previous 55.14-second clean run

- Elapsed time: **55.14s -> 42.96s**
- Elapsed-time reduction: **~22.1%**
- Document/case throughput improvement: **~28.4%**

The 42.96-second result also beats the earlier three-concurrent-batch result of approximately 50.20 seconds by about **14.4%** while using one logical 150-document batch.

## 8. What the performance figures mean

The current result demonstrates that the architectural bottleneck was not simply the number of input documents. Significant time was being lost to avoidable SQL round-trips, repeated client construction, validation self-calls, and contention on shared state.

The current code removes those costs while keeping the same low-cost SQL S0 tier.

The remaining path to materially lower latency is increasingly a **capacity problem** rather than a correctness/architecture problem.

A particularly important fact from the latest run is that upload + completion + queue publication took **15.31 seconds** for 150 documents. Therefore:

- an end-to-end **10-second** target is not achievable while the upload stage alone remains above 10 seconds;
- a **15-second** end-to-end target leaves essentially no margin for remaining backend work unless upload and processing overlap almost perfectly;
- the current low-cost environment should be judged primarily on reliability and throughput rather than a guaranteed 10-15 second SLA.

## 9. Server/configuration scenarios and expected results

The current S0/Y1 measurements are real. The other scenarios below are **engineering projections, not measured benchmarks or guarantees**. Actual performance depends on document size, Azure load, Function instance count, SQL wait profile, network latency, validation-service latency, and data distribution.

| Scenario | SQL | Function hosting | Processing concurrency | Expected 150-document end-to-end range | Confidence / interpretation |
|---|---|---|---:|---:|---|
| **Current validated** | Standard S0 | Consumption Y1 | 8/instance | **42.96s measured** | Actual benchmark |
| Cost-neutral tuning | Standard S0 | Consumption Y1 | 8-10/instance | **~35-45s projected** | Small gains possible; higher concurrency can also make S0 slower if saturated |
| Moderate production capacity | General Purpose ~2 vCore | Flex/Premium-style warm compute | 8-12/instance | **~25-35s projected** | More SQL CPU/IO and predictable compute reduce queue drain time |
| Higher production capacity | General Purpose ~4 vCore | Flex with warm capacity / Premium | 12-16 controlled | **~18-28s projected** | Requires measuring SQL waits and bounding total concurrency |
| Performance-focused configuration | General Purpose ~4-8 vCore | Warm Flex/Premium, bounded scale-out | 12-16 controlled + faster upload path | **~12-20s projected** | 10-15s becomes plausible but still requires benchmark proof |

### Why a larger server does not produce a linear speedup

Doubling compute does not halve end-to-end time because the total path includes several independent stages:

```text
file generation/client work
+ Blob upload
+ completion API / Service Bus publication
+ queue scheduling
+ Excel parsing
+ validation
+ SQL persistence
+ status polling interval
```

Some stages overlap, and only some are improved by a faster SQL server. The test harness also polls batch status every five seconds, so the reported completion time can contain up to roughly one polling interval of observation delay.

### Current cost-conscious decision

For the personal development subscription, the project intentionally keeps:

- Azure SQL Standard S0;
- Consumption Y1 Function hosting;
- Service Bus Standard;
- bounded concurrency;
- no additional caching/service tier solely for benchmark speed.

This is the recommended stopping point for a low-cost architecture-validation environment.

## 10. Scaling beyond 150 documents

The processing architecture can accept larger queue backlogs, but the current upload preparation API accepts a maximum of **200 files per request**.

A direct 500-file prepare call is therefore intentionally not supported today.

The recommended production-scale upload model is:

```text
Logical upload job: 500 documents
   |
   +-- chunk 1: 50-100 documents
   +-- chunk 2: 50-100 documents
   +-- ...
   +-- chunk N
            |
            v
       same Service Bus queue
            |
            v
       bounded processors
```

That future change should preserve one logical batch identity while using smaller preparation/upload chunks. It should not create uncontrolled document or row fan-out.

For the current synthetic workload, 500 documents x 153 rows would represent **76,500 cases**.

## 11. Production hardening still required

The architecture is validated, but the current deployment profile is a development/test environment rather than a final internet-facing production configuration.

Before production use, address the following:

- authenticate HTTP APIs with Microsoft Entra ID/App Service Authentication or an API gateway;
- replace SQL username/password authentication with managed identity / Entra database authentication;
- move secrets and sensitive connection material to managed identity/Key Vault patterns;
- replace broad Service Bus management credentials with least-privilege identity/RBAC;
- use private endpoints/private networking where required;
- introduce a transactional outbox for the strongest SQL-to-Service-Bus publication guarantee;
- add production alerts/dashboards for queue age/depth, DLQ > 0, Function failures, SQL saturation, and stuck documents;
- add configurable validation-service fault injection (latency, 429, transient 5xx and permanent failures) for resilience testing;
- define and test a global concurrency policy for multi-instance scale-out;
- add a logical-batch/chunked-prepare API before treating 500+ documents as one user-visible upload job.

## 12. What is intentionally not added

The workload does not currently require another orchestration platform. The project deliberately avoids adding components that do not solve an observed bottleneck, including:

- Durable Functions for normal document flow;
- Logic Apps for row/document orchestration;
- Event Grid as the processing fan-out mechanism;
- Event Hubs;
- AKS;
- Redis solely for the current 150-document benchmark.

These can be reconsidered only when a measured requirement justifies them.

## 13. Project conclusion

The project objective was to replace uncontrolled parallel document processing with a durable, bounded, retry-safe Azure processing pipeline.

That objective has been achieved for the development/test scope.

The final validated implementation demonstrates:

- **150 documents processed successfully**;
- **22,950 cases persisted exactly**;
- **0 failed documents**;
- **0 dead-lettered documents**;
- **42.96-second end-to-end completion** on the current cost-sensitive configuration;
- approximately **3.85x normalized case throughput** compared with the original full baseline;
- approximately **74% reduction in normalized time per document**;
- successful simultaneous execution of **3 independent batches x 50 documents**;
- idempotent/retry-safe document processing;
- atomic case persistence and document finalization;
- recovery protection for orphaned processing state;
- infrastructure, CI/CD and load testing captured in code.

For the current personal Azure subscription and cost constraint, **the project is concluded at the architecture-validation stage with Azure SQL S0 and Consumption hosting**. Further reductions toward 10-15 seconds should be treated as a separate performance/capacity phase, because they would require additional upload-path optimization, careful concurrency tuning, and likely more database/compute capacity.

The correct production evolution is therefore **scale the existing architecture**, not replace it.
