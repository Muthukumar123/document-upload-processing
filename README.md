# Document Upload Processing

Production-oriented Azure implementation for controlled batch document ingestion and Excel-driven case processing.

## Project status

The development/test architecture has been validated successfully on the current cost-sensitive Azure configuration.

**Latest validated result:**

- **150 documents**
- **153 data rows x 10 columns per document**
- **22,950 cases**
- **42.96 seconds end-to-end**
- **0 failed documents**
- **0 dead-lettered documents**
- latest validated code baseline: `dda4c9d` (`Optimize document processing hot paths`)

### Benchmark workload terminology: documents are not cases

The benchmark uploads **150 Excel documents**. It does not upload 150 cases.

Each synthetic Excel document contains **153 non-header data rows**. In this benchmark, **each non-empty data row becomes one case** in Azure SQL. The 10 columns are fields belonging to that case and do not multiply the case count.

Therefore:

```text
150 documents
x 153 data rows per document
= 22,950 cases
```

So `150 documents / 22,950 cases` means that **150 uploaded files produced 22,950 row-level case records in total**.

Examples:

| Documents | Rows per document | Calculation | Cases |
|---:|---:|---:|---:|
| 1 | 153 | 1 x 153 | 153 |
| 50 | 153 | 50 x 153 | 7,650 |
| 3 batches x 50 | 153 | 3 x 50 x 153 | 22,950 |
| 150 | 153 | 150 x 153 | 22,950 |
| 153 | 153 | 153 x 153 | 23,409 |

**153 rows per document is a synthetic load-test setting, not a production rule.** Real documents may contain fewer or more business rows, so real case volume depends on the actual non-empty rows processed from each document.

See [`docs/benchmark-workload-model.md`](docs/benchmark-workload-model.md) for the authoritative explanation of document, row, column and case counts used by the benchmark.

Compared with the original 153-document baseline of 168.53 seconds, normalized case throughput improved from approximately **138.9 cases/sec** to **534.2 cases/sec**: about **3.85x throughput**, with approximately **74% lower normalized time per document**.

See [`docs/project-conclusion.md`](docs/project-conclusion.md) for the complete project write-up, benchmark history, current Azure configuration, projected results for larger server configurations, production-hardening items, and final conclusion.

See [`docs/enterprise-architecture-and-benchmarking.md`](docs/enterprise-architecture-and-benchmarking.md) for the detailed architecture assessment, benchmarking methodology, benchmark-maturity evaluation, enterprise-standard gap analysis, SLO guidance, telemetry requirements, stress/soak/failure testing plan, and server-capacity scenarios.

See [`docs/application-and-infrastructure-architecture.md`](docs/application-and-infrastructure-architecture.md) for the **comprehensive application and Azure infrastructure architecture**, including Mermaid component diagrams, upload/processing/retry/recovery/deployment sequence diagrams, SQL ER model, state transitions, Service Bus concurrency model, security topology, observability, CI/CD architecture, 500+ document scaling model, transactional-outbox target and production evolution.

## Enterprise benchmark classification

The architecture is **enterprise-aligned**, and the current measurements are a **strong end-to-end engineering/architecture benchmark**. They should not yet be described as a complete enterprise production performance certification.

The current test already validates the real Azure path, exact persisted case count, zero failures/DLQ, comparative before/after performance, and concurrent independent batches. Full enterprise performance certification would additionally require repeated statistical runs, p50/p95/p99 latency, cold-vs-warm separation, stage-level server timings, infrastructure utilization telemetry, sustained/stress/spike/soak workloads, controlled fault injection, formal SLO acceptance criteria, production-scale workload validation, and cost/performance analysis.

## Technology standards

- **React + Vite**: browser user interface only
- **.NET 8 / C# Azure Functions isolated worker**: upload APIs, Service Bus processing, validation mock and reconciliation
- **Python 3.12**: automated integration/load tests
- **Bicep**: Azure infrastructure-as-code
- **Azure SQL**: durable batch/document/case/audit state

No Node.js/TypeScript backend is used.

## Current architecture

```text
React SPA
   -> Upload API
   -> direct-to-Blob upload using SAS
   -> completion API records SHA-256 + Blob version
   -> Azure Service Bus
   -> bounded .NET 8 document processor
   -> reference comparison + batched/in-process validation
   -> bulk/set-based Azure SQL persistence
   -> document/case links + audit
```

The Service Bus queue is the processing boundary and backpressure mechanism. The application sends **one message per document**, never one message per row.

### Detailed architecture diagrams

The comprehensive architecture document contains GitHub-rendered Mermaid diagrams for:

- system context and logical application components;
- upload preparation/direct Blob upload/completion flow;
- Service Bus document-processing sequence;
- retry and DLQ behavior;
- stale-processing recovery watchdog;
- batch-status derivation;
- document state transitions;
- SQL entity relationships;
- Azure infrastructure topology;
- current network/security topology;
- per-instance Service Bus concurrency and scale-out behavior;
- validation modes;
- GitHub Actions deployment sequence;
- observability topology;
- future 500+ document logical batching;
- recommended transactional outbox;
- production target architecture.

See [`docs/application-and-infrastructure-architecture.md`](docs/application-and-infrastructure-architecture.md).

## Current Azure development/test configuration

- Azure Storage Standard LRS with Blob versioning
- Azure Service Bus Standard
- Queue duplicate detection: 1 hour
- Queue lock: 5 minutes
- Maximum delivery count: 5
- Azure Functions Linux Consumption `Y1`
- .NET isolated 8.0
- Service Bus `maxConcurrentCalls=8` per Function host instance
- Service Bus prefetch disabled
- Azure SQL Standard `S0`
- SQL connection string `Max Pool Size=15`
- 55 MiB maximum file size
- maximum 200 files in one prepare request
- Application Insights + Log Analytics

The current configuration is intentionally cost-sensitive. More expensive SQL/Function configurations are discussed as **projections, not measured guarantees**, in the project conclusion and enterprise benchmarking documents.

## Reliability and processing characteristics

- Direct-to-Blob browser uploads with short-lived SAS
- SHA-256 integrity verification
- exact Blob version processing
- deterministic Service Bus document message IDs
- PeekLock/manual queue settlement
- at-least-once delivery with application-level idempotency
- deterministic case IDs derived from document + row
- bounded document concurrency
- first-attempt processing fast path
- batched validation
- in-process mock validation for performance testing; external batch HTTP validation remains supported
- `SqlBulkCopy` + set-based persistence
- case persistence and document finalization in one SQL transaction
- Service Bus message completed only after durable SQL commit
- retry-safe finished-case detection
- recovery watchdog for orphaned `PROCESSING` records
- DLQ after terminal Service Bus delivery failure

## Benchmark summary

| Test | Documents | Cases | Elapsed | Cases/sec | Result |
|---|---:|---:|---:|---:|---|
| Original full baseline | 153 | 23,409 | 168.53s | 138.9 | PASS |
| First optimized clean full run | 150 | 22,950 | 55.14s | 416.2 | PASS |
| 3 concurrent batches x 50 | 150 total | 22,950 total | ~50.20s | 457.2 | 3/3 PASS |
| **Current optimized single batch** | **150** | **22,950** | **42.96s** | **534.2** | **PASS** |

For every 150-document benchmark shown above, the case count is based on the synthetic workload formula `150 documents x 153 rows/document = 22,950 cases`.

The latest run uploaded and queued all 150 documents in **15.31 seconds**. This is important when considering a future 10-15 second end-to-end target: server scaling alone cannot remove upload/client/network time.

## Repository layout

- `frontend/` React + Vite application
- `backend/DocumentUpload.Functions/` .NET 8 isolated Azure Functions
- `tests/` Python tests and Azure load-test harness
- `sql/` durable Azure SQL schema
- `infra/` Azure Bicep
- `docs/architecture.md` concise runtime architecture and reliability model
- `docs/application-and-infrastructure-architecture.md` comprehensive application/infrastructure architecture and sequence diagrams
- `docs/benchmark-workload-model.md` authoritative benchmark document/row/case definitions and calculations
- `docs/project-conclusion.md` full project report, facts/figures, scaling projections and conclusion
- `docs/enterprise-architecture-and-benchmarking.md` enterprise architecture and benchmark maturity assessment
- `.github/workflows/` CI and Azure deployment workflow

## End-to-end load test

The Python load harness generates Excel files, uploads them through the real API/Blob path, waits for Service Bus processing, and validates the exact number of persisted cases.

```bash
python -m pip install -r tests/requirements.txt
python tests/load_test.py \
  --base-url https://<function-app>.azurewebsites.net/api \
  --documents 150 \
  --upload-concurrency 10 \
  --timeout-minutes 30
```

Each generated workbook currently contains 153 data rows and 10 columns. Each non-empty data row represents one case, so a 150-document run expects exactly `150 x 153 = 22,950` cases. The 10 columns are attributes within each case and are not additional cases.

The prepare endpoint currently accepts at most **200 files in one request**. A future 500+ document user-visible job should use one logical batch with smaller preparation/upload chunks rather than uncontrolled fan-out.

## Deployment

Use `.github/workflows/deploy.yml`. Configure the GitHub environment `azure-dev` with `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and secure `SQL_ADMIN_PASSWORD`.

## Production note

The current deployment profile is a validated development/test architecture, not a final internet-facing production security profile. Production hardening should include Entra authentication, managed identity for SQL and Service Bus, least-privilege RBAC, private networking where required, stronger secret handling, operational alerts, failure-mode testing, a global multi-instance concurrency policy, and a transactional outbox for the strongest SQL-to-Service-Bus publication guarantee.
