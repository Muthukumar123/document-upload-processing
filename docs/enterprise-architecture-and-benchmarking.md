# Document Upload Processing - Architecture and Enterprise Benchmarking Assessment

**Assessment date:** 2026-09-12  
**Validated code baseline:** `dda4c9d` (`Optimize document processing hot paths`)  
**Scope:** Architecture validation and performance benchmarking of the Document Upload solution on the current cost-sensitive Azure development/test configuration.

## 1. Executive summary

The Document Upload solution was redesigned to replace uncontrolled synchronous fan-out with a durable, bounded, retry-safe Azure processing pipeline.

The current architecture uses direct browser-to-Blob upload, Azure Service Bus as the processing boundary, bounded .NET 8 Azure Functions as workers, batched validation, bulk/set-based Azure SQL persistence, deterministic identifiers, and explicit retry/DLQ handling.

The latest validated workload processed **150 Excel documents containing 22,950 cases in 42.96 seconds**, with **0 failed documents** and **0 dead-lettered documents**. The original full baseline processed 153 documents / 23,409 cases in 168.53 seconds. On a normalized case-throughput basis, the latest implementation is approximately **3.85x faster** than the original baseline, while normalized time per document is approximately **74% lower**.

The architecture itself is **enterprise-aligned**. The current benchmark is a strong end-to-end engineering and architecture-validation benchmark, but it is **not yet a complete enterprise production performance certification**. Enterprise-standard benchmarking would additionally require repeated statistical runs, percentile latency, cold/warm separation, sustained and stress workloads, soak testing, fault injection, server-side stage timings, infrastructure telemetry, SLO-based acceptance criteria, and tests at the actual expected production scale.

## 2. Problem statement

The original processing pattern allowed an uploaded document burst to create too many overlapping workflows and synchronous processing calls. Downstream SQL capacity and the SQL client connection pool could therefore become the effective concurrency control mechanism.

That is unsafe for a production processing system because backpressure is implicit, failures occur late, and the database is exposed directly to burst concurrency.

The architecture objective was therefore to provide:

- explicit asynchronous backpressure;
- durable queueing;
- bounded document concurrency;
- short SQL transactions;
- idempotent retries;
- deterministic processing identity;
- exact document version processing;
- dead-letter handling;
- recovery from orphaned processing state;
- observable batch/document/case status;
- measurable end-to-end performance.

## 3. Final architecture

```text
React SPA
    |
    | prepare upload
    v
.NET 8 Upload API
    |
    | short-lived SAS URLs
    v
Azure Blob Storage
    |
    | direct browser upload
    | SHA-256 + Blob version recorded on completion
    v
Azure Service Bus
    |
    | one message per document
    | PeekLock / manual completion
    | duplicate detection
    v
.NET 8 Service Bus Function
    |
    | bounded document concurrency
    | exact Blob version download
    | SHA-256 verification
    | Excel parse
    | reference-data compare
    | batched/in-process validation
    | SqlBulkCopy + set-based persistence
    v
Azure SQL
    |
    +-- UploadBatch
    +-- DocumentUpload
    +-- Case
    +-- DocumentCaseLink
    +-- AuditEvent
    +-- ReferenceData
```

### Architectural principle

Upload concurrency and processing concurrency are deliberately separated.

The browser can upload documents quickly, while Service Bus absorbs the burst and the worker processes documents at a controlled rate. The queue therefore becomes the explicit backpressure mechanism rather than allowing the database connection pool to become an accidental backpressure mechanism.

## 4. Component responsibilities

| Component | Responsibility |
|---|---|
| React SPA | User-facing batch/file selection, direct Blob upload and status display |
| Upload API | Validate file metadata, create batch/document rows, issue SAS URLs |
| Blob Storage | Store original documents and preserve exact Blob versions |
| Completion API | Record SHA-256 and Blob version, publish one document message |
| Service Bus | Durable buffering, retry delivery, duplicate detection, DLQ |
| Processing Function | Claim document, verify file, parse, compare, validate, persist, finalize |
| Azure SQL | Durable business/process state and audit data |
| Recovery watchdog | Recover genuinely orphaned `PROCESSING` documents |
| Reconciliation | Repair/refresh aggregate stored batch state |
| Application Insights / Log Analytics | Runtime observability and diagnostics |
| Python load test | End-to-end architecture/performance validation |

## 5. Current technology and infrastructure configuration

| Area | Current configuration |
|---|---|
| Frontend | React + Vite |
| Backend | .NET 8 isolated Azure Functions |
| Automated/load testing | Python 3.12 |
| Infrastructure-as-code | Bicep |
| Database | Azure SQL Standard S0 |
| Function plan | Linux Consumption `Y1` |
| Service Bus | Standard |
| Storage | Standard LRS StorageV2 |
| Blob versioning | Enabled |
| Queue | `document-processing` |
| Message model | One message per document |
| Duplicate detection | 1 hour |
| Queue lock duration | 5 minutes |
| Max delivery count | 5 |
| Service Bus prefetch | 0 |
| Auto lock renewal | 10 minutes |
| `maxConcurrentCalls` | 8 per Function host instance |
| SQL connection pool | `Max Pool Size=15` |
| Maximum file size | 55 MiB |
| Prepare request limit | 200 files |
| Default validation batch size | 200 |
| Maximum validation batch size | 500 |
| Recovery stale threshold | 12 minutes default, minimum 11 minutes |
| Recovery max attempts | 5 |

The configuration is intentionally cost-sensitive because it runs in a personal development subscription. It should not be confused with a sized production configuration.

## 6. Detailed processing flow

### 6.1 Upload preparation

1. The client sends file names and sizes to the upload preparation API.
2. The API validates the request and creates one `UploadBatch` row plus one `DocumentUpload` row per file.
3. Each document receives a deterministic server-side document identity.
4. The API returns a short-lived write SAS URL for each document.

### 6.2 Direct file upload

1. The browser uploads each document directly to Azure Blob Storage.
2. File bytes do not pass through the Function App upload endpoint.
3. The client computes SHA-256 for the uploaded content.

### 6.3 Upload completion and queue publication

1. The completion API validates the document/batch identity.
2. It reads Blob properties and records the exact Blob version ID.
3. It records the SHA-256 supplied by the client.
4. It marks the document `UPLOADED`.
5. It publishes one Service Bus message for the document.
6. The original Service Bus `MessageId` is the document ID.
7. The post-send `UPLOADED -> QUEUED` update is conditional so it cannot move a document backward if a fast worker has already advanced it to `PROCESSING`.
8. The processor is allowed to claim either `UPLOADED` or `QUEUED`, closing the publication/processing race window.

### 6.4 Queue processing

1. The worker atomically claims the document and increments `AttemptCount`.
2. The exact Blob version is downloaded.
3. SHA-256 is recalculated and compared with the stored expected value.
4. The first Excel worksheet is parsed.
5. Each non-empty data row is normalized into a deterministic case identity.
6. On first attempt, the worker takes the fast path and avoids retry-only SQL reads.
7. On retry, already-finished cases are loaded so durable rows do not need to be reprocessed unnecessarily.
8. Required reference data is loaded from SQL.
9. SQL/reference comparison is calculated.
10. Validation requests are batched.
11. The development mock is executed in-process to avoid an unnecessary Function-to-self HTTP call; an external batch HTTP validation adapter remains supported.
12. Processed rows are staged with `SqlBulkCopy` and persisted using set-based SQL.
13. Case data, document-case links, document final status and final audit state are committed in the same SQL transaction.
14. Only after this durable transaction succeeds is the Service Bus message completed.

## 7. Reliability and correctness model

### At-least-once delivery

Service Bus may redeliver messages. The processing model therefore treats duplicate delivery as normal rather than exceptional.

### Deterministic identity

- Original message ID: document ID.
- Case ID: deterministic from `(documentId, rowNumber)`.
- Recovery message ID: deterministic for the document/attempt combination.

### Idempotency

SQL uniqueness, deterministic IDs and state-transition checks make a repeated message harmless.

### Exact source content

Blob versioning plus SHA-256 verification ensures the worker processes the exact expected document version and detects unexpected content changes.

### Manual settlement

The Service Bus message is completed only after durable processing succeeds.

### Atomic persistence/finalization

Case persistence and document finalization are in the same SQL transaction. This removes the previously observed failure window where all rows could be committed while the parent document remained stuck in `PROCESSING`.

### Retry and DLQ

Normal Service Bus retry continues until the configured maximum delivery count. Terminal failures are dead-lettered and recorded in document state.

### Recovery watchdog

The recovery function only considers stale `PROCESSING` records after the normal lock/renewal window. It is a safety net for orphaned state, not part of the expected fast path.

## 8. Important architecture fixes made during the project

### Queue boundary

Uncontrolled synchronous fan-out was replaced with durable Service Bus buffering.

### Bounded concurrency

The worker uses controlled document concurrency instead of allowing one active processor per uploaded document.

### Batched validation

Validation moved from repeated row-oriented calls to batch-oriented processing.

### Bulk/set-based SQL persistence

Rows are staged with `SqlBulkCopy` and persisted in sets rather than through repeated row-by-row database calls.

### Upload-to-processor race protection

The processor can claim `UPLOADED` as well as `QUEUED`, and the upload completion transition is conditional.

### Atomic document finalization

Cases and final document state are committed together before queue completion.

### Batch hot-row removal

Workers no longer update the same batch row on every document completion. Live batch status is derived from document states, removing a shared write hotspot from the critical processing transaction.

### Azure SDK client reuse

Blob and Service Bus clients/senders are reused for the Function-host lifetime rather than recreated for every document/request.

### First-attempt fast path

Retry-specific SQL reads are skipped on the common first attempt.

### In-process development validation

The mock validator no longer requires HTTPS from the Function App back into the same Function App.

## 9. Benchmark workload

The standard synthetic benchmark document contains:

- 153 data rows;
- 10 columns per row;
- one workbook per document.

Therefore:

- 150 documents = **22,950 cases**;
- 153 documents = **23,409 cases**;
- 500 documents would represent **76,500 cases** for this specific synthetic workload.

The benchmark is end-to-end. It exercises:

```text
Python client
  -> upload preparation API
  -> SAS Blob PUT
  -> upload completion API
  -> Service Bus
  -> Functions worker
  -> Blob download/checksum
  -> Excel parsing
  -> reference comparison
  -> validation
  -> SQL persistence
  -> batch-status API
```

It is therefore substantially stronger than a micro-benchmark of only one method or one database statement.

## 10. Measured benchmark history

All rows below are actual observed results unless explicitly stated otherwise.

| Test | Documents | Cases | Elapsed | Documents/sec | Cases/sec | Result |
|---|---:|---:|---:|---:|---:|---|
| Original full baseline | 153 | 23,409 | 168.53 s | 0.91 | 138.9 | PASS |
| Clean optimized architecture run | 150 | 22,950 | 55.14 s | 2.72 | 416.2 | PASS |
| Three concurrent batches x 50 | 150 total | 22,950 total | ~50.20 s | 2.99 | 457.2 | 3/3 PASS |
| Current optimized single batch | 150 | 22,950 | **42.96 s** | **3.49** | **534.2** | **PASS** |

### Latest validated run

```text
Uploaded and queued 150 documents in 15.31s
150 completed
22,950 cases
0 failed
0 dead-lettered
COMPLETED
PASS in 42.96s
```

### Normalized improvement from original baseline

Original baseline:

```text
23,409 cases / 168.53s ~= 138.9 cases/sec
```

Current baseline:

```text
22,950 cases / 42.96s ~= 534.2 cases/sec
```

Therefore:

- throughput multiplier: approximately **3.85x**;
- throughput increase: approximately **284.6%**;
- normalized time/document changed from approximately **1.10s** to **0.286s**;
- normalized time/document reduction: approximately **74%**.

### Improvement from the previous clean 55.14-second run

- 55.14s -> 42.96s;
- approximately **22.1% lower elapsed time**;
- approximately **28.4% higher document/case throughput**.

### Multi-batch concurrency result

Three independent 50-document batches were started concurrently. All three completed successfully:

```text
Batch 1: 50 documents / 7,650 cases / 49.10s
Batch 2: 50 documents / 7,650 cases / 49.14s
Batch 3: 50 documents / 7,650 cases / 50.20s
```

Combined result:

```text
150 documents
22,950 cases
3/3 batches completed
0 failed
0 DLQ
~50.20s wall-clock completion
```

This validates that multiple independent upload batches can share the same queue, worker and database infrastructure without correctness loss at this workload.

## 11. Important benchmark interpretation

The current result proves that the architecture can process the validated workload correctly and much faster than the original design on the same low-cost class of environment.

It does **not** prove that every future workload will finish in 42.96 seconds.

Performance depends on:

- document size;
- rows per document;
- data distribution;
- Function cold/warm state;
- active Function instance count;
- Azure SQL utilization and waits;
- Service Bus scheduling delay;
- Blob/network latency;
- validation service latency;
- upload bandwidth;
- client location;
- concurrent workloads from other users;
- Azure platform variability.

The latest run also spent **15.31 seconds** in upload + completion + queue publication for all 150 documents. Consequently, an end-to-end 10-second target cannot be met while the upload phase by itself is above 10 seconds.

The Python harness polls batch status every five seconds, so the observed completion time can include up to roughly one polling interval of client-side observation delay. For precise enterprise latency measurement, server-side completion timestamps should be used.

## 12. Is the current architecture enterprise standard?

### Architecture assessment: enterprise-aligned

The architectural pattern is appropriate for enterprise evolution because it includes:

- durable asynchronous queueing;
- explicit backpressure;
- bounded consumers;
- idempotent processing;
- retry and DLQ handling;
- deterministic identities;
- immutable/versioned source processing;
- integrity verification;
- atomic durable finalization;
- recovery logic;
- observability hooks;
- infrastructure-as-code;
- CI/CD;
- automated end-to-end testing.

The architecture does not need additional orchestration products merely to be considered enterprise-capable. Durable Functions, Logic Apps, Event Grid fan-out, Event Hubs or AKS would only be justified by a specific measured requirement.

### Deployment assessment: not yet final enterprise production configuration

The current environment still requires production hardening in areas such as:

- Entra authentication for HTTP APIs;
- managed identity / Entra authentication for SQL;
- least-privilege Service Bus RBAC instead of broad management credentials;
- Key Vault / managed-identity secret patterns;
- private endpoints/private networking where policy requires them;
- transactional outbox for the strongest SQL-to-Service-Bus publication guarantee;
- multi-instance global concurrency control;
- production dashboards and alerts;
- validation fault injection and resilience testing;
- logical-batch/chunked upload support for 500+ documents.

## 13. Is the current benchmarking enterprise standard?

### Current classification

The current benchmark should be described as:

> **A strong end-to-end engineering benchmark and successful enterprise-architecture validation on a development/test environment.**

It should **not yet** be described as:

> **A complete enterprise production performance certification.**

### Benchmark maturity assessment

| Benchmark area | Current status |
|---|---|
| Real end-to-end Azure path | Strong |
| Functional correctness checks | Strong |
| Exact case-count validation | Strong |
| Failure/DLQ verification | Strong |
| Comparative before/after benchmark | Strong |
| Multi-batch concurrency validation | Strong |
| Repeatability statistics | Not yet complete |
| p50/p95/p99 latency | Not yet captured |
| Cold vs warm separation | Not yet formalized |
| Sustained throughput test | Not yet complete |
| Stress-to-failure test | Not yet complete |
| Long-duration soak test | Not yet complete |
| Fault-injection benchmark | Not yet complete |
| Infrastructure utilization capture | Not yet formalized |
| SLO-based acceptance criteria | Not yet formalized |
| Production-scale 500+/1000+ job validation | Not yet complete |
| Cost/performance benchmark | Not yet formalized |

## 14. What enterprise-standard benchmarking would add

### 14.1 Repeatability

Each benchmark scenario should be executed repeatedly, for example 10-20 runs, and report:

- minimum;
- maximum;
- mean;
- median;
- standard deviation;
- p50;
- p95;
- p99.

A single successful result demonstrates capability, but repeated distributions demonstrate predictability.

### 14.2 Cold and warm execution

Function cold start and steady-state warm execution should be measured separately.

Example categories:

```text
Cold-start batch
Warm steady-state batch
Warm sustained queue-drain workload
```

### 14.3 Stage-level latency

The following should be captured separately:

```text
upload prepare
Blob upload
completion API
queue wait
worker execution
Blob download
Excel parse
reference-data SQL
validation
SQL persistence
queue settlement
batch finalization visibility
```

This allows optimization decisions to be based on measured bottlenecks rather than assumptions.

### 14.4 Infrastructure telemetry

A production benchmark should correlate client timings with:

**Azure SQL**

- CPU/DTU percentage;
- data IO percentage;
- log IO percentage;
- session/connection count;
- waits/blocking;
- query duration;
- deadlocks/timeouts.

**Azure Functions**

- active instance count;
- execution duration;
- failures;
- CPU/memory where available;
- cold starts;
- concurrent executions.

**Service Bus**

- active queue depth;
- oldest-message age;
- delivery count;
- incoming/outgoing messages;
- dead-letter count;
- throttling/errors.

**Storage**

- Blob request latency;
- failures/throttling;
- egress/ingress where relevant.

### 14.5 Load profiles

Enterprise performance testing should include several workload shapes:

| Test type | Purpose |
|---|---|
| Baseline | Validate expected normal workload |
| Burst | Validate sudden large upload arrival |
| Concurrent batches | Simulate several users/jobs |
| Sustained load | Measure stable throughput over time |
| Stress | Find maximum sustainable load and failure behavior |
| Spike | Validate rapid changes in arrival rate |
| Soak/endurance | Detect leaks, gradual degradation or lock accumulation |
| Recovery | Validate restart/redelivery behavior |
| Capacity | Establish safe production sizing |

### 14.6 Soak testing

A multi-hour workload should verify that throughput remains stable and that there is no:

- connection leak;
- memory growth;
- queue-lock instability;
- accumulating SQL blocking;
- unbounded retry growth;
- growing DLQ;
- gradually increasing latency.

### 14.7 Failure-mode injection

The validation adapter should support controlled injection of:

- latency;
- HTTP 429;
- transient 5xx;
- permanent validation failure.

Additional resilience tests should cover:

- worker termination during processing;
- SQL transient failures;
- Blob read failures;
- duplicate Service Bus delivery;
- lock expiration/redelivery;
- recovery watchdog execution;
- message reaching max delivery count.

### 14.8 Realistic data distribution

The current workload deliberately uses a repeatable synthetic 153-row x 10-column workbook. Enterprise performance validation should additionally vary:

- document size;
- row count;
- empty/sparse rows;
- value distribution;
- reference-data hit/miss rate;
- validation outcomes;
- invalid cases;
- duplicate/retry scenarios.

### 14.9 Defined SLOs

The benchmark should test explicit service-level objectives rather than only recording the fastest elapsed time.

Example only:

```text
For a 150-document standard batch:
- p95 completion <= 60 seconds
- p99 completion <= 90 seconds
- zero lost documents
- exact expected case count
- DLQ rate < 0.01%
- recovery of orphaned PROCESSING state <= 15 minutes
```

Actual production SLOs must come from business requirements rather than these example values.

### 14.10 Cost/performance measurement

For production capacity planning, compare configurations using both throughput and cost.

Useful metrics include:

```text
cases/sec
batches/hour
documents/hour
cost per 1,000 documents
cost per 100,000 cases
cost of warm capacity vs latency benefit
```

## 15. Server/configuration scenarios

Only the current S0/Y1 result below is measured. All other rows are engineering projections and must be benchmarked before being treated as commitments.

| Scenario | SQL | Function compute | Controlled concurrency | Expected 150-doc result | Status |
|---|---|---|---:|---:|---|
| Current cost-sensitive environment | Standard S0 | Consumption Y1 | 8/instance | **42.96s** | **Measured** |
| Cost-neutral tuning | Standard S0 | Consumption Y1 | 8-10/instance | ~35-45s | Projection |
| Moderate production capacity | GP ~2 vCore | warm Flex/Premium-style compute | 8-12/instance | ~25-35s | Projection |
| Higher production capacity | GP ~4 vCore | warm Flex/Premium | 12-16 controlled | ~18-28s | Projection |
| Performance-focused | GP ~4-8 vCore | warm bounded scale-out | 12-16 + faster upload | ~12-20s | Projection |

These ranges are not linear because only part of the end-to-end path is improved by SQL/compute scaling.

Total latency includes:

```text
client file generation
+ network upload
+ Blob write
+ completion API
+ queue publication
+ queue wait
+ Function compute
+ Excel parsing
+ validation
+ SQL persistence
+ status observation
```

Increasing SQL capacity cannot directly remove client/network upload time.

## 16. Current cost-conscious conclusion

Because this project runs in a personal Azure subscription, the current stopping point intentionally keeps:

- Azure SQL Standard S0;
- Linux Consumption Y1;
- Service Bus Standard;
- bounded processing concurrency;
- no additional paid service solely to improve benchmark numbers.

Within that constraint, the current implementation achieved a large improvement through architecture and code changes rather than through expensive infrastructure scaling.

## 17. Production scaling beyond 150 documents

The queue-processing architecture itself can accept larger backlogs. The current upload preparation API, however, accepts no more than 200 files per request.

For a user-visible 500+ document job, the recommended future model is:

```text
Logical batch: 500 documents
    |
    +-- upload chunk 1: 50-100
    +-- upload chunk 2: 50-100
    +-- upload chunk 3: 50-100
    +-- ...
    |
    v
same Service Bus queue
    |
    v
bounded document processors
```

The chunks should share one logical job/batch identity so the UI still presents one 500-document operation.

A production scale-out design must also remember that `maxConcurrentCalls=8` is per Function host instance. Aggregate concurrency is approximately:

```text
active Function instances x per-instance concurrency
```

Therefore production scale-out requires an intentional global concurrency strategy or carefully bounded worker instances so that SQL and validation capacity are not exceeded.

## 18. Final assessment

### Architecture

**Enterprise-aligned and successfully validated for the current development/test workload.**

### Reliability model

**Strong architecture-validation level**, with at-least-once delivery, idempotency, deterministic identity, atomic finalization, DLQ and recovery protection.

### Performance result

**150 documents / 22,950 cases / 42.96 seconds / 0 failures / 0 DLQ** on the current cost-sensitive S0/Y1 configuration.

### Benchmark classification

**Strong engineering/end-to-end architecture benchmark, but not yet a complete enterprise production benchmark certification.**

### Production readiness

The core processing architecture should be **scaled and hardened rather than replaced**. Production adoption still requires security hardening, transactional-outbox consideration, multi-instance global concurrency control, formal SLOs, telemetry-driven capacity testing, repeated percentile benchmarks, stress/soak/failure testing, and validation at the actual expected production workload.

## 19. Recommended wording for project documentation/reviews

Use:

> The Document Upload architecture is enterprise-aligned and has been successfully validated end-to-end on Azure using a cost-sensitive development configuration. The latest measured workload processed 150 documents / 22,950 cases in 42.96 seconds with zero failed or dead-lettered documents, approximately 3.85x normalized case throughput compared with the original baseline. The current measurements constitute a strong architecture and engineering benchmark; full enterprise production performance certification requires repeatable percentile, stress, soak, resilience, telemetry and production-scale testing.

Avoid claiming:

> The system is fully enterprise production benchmark-certified.

That statement would exceed the evidence currently collected.