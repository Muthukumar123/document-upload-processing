# Architecture

## Runtime flow

1. React requests a batch and per-document upload SAS from the Upload API.
2. The browser uploads each document directly to Blob Storage and calculates SHA-256.
3. The browser calls completion; the API verifies the blob exists, records blob version/checksum, and sends a deterministic Service Bus message.
4. The Service Bus trigger runs with global `maxConcurrentCalls=8`.
5. Each worker atomically claims the document, downloads the immutable blob version, verifies SHA-256, parses the first Excel worksheet, and processes rows sequentially.
6. Each row creates a deterministic Case ID, compares reference data in Azure SQL, calls the validation API, persists the result, and links the source document.
7. SQL transactions are deliberately short; no SQL transaction is held while calling the validation service.
8. A technical failure throws so Service Bus retries. After `maxDeliveryCount=5`, the message is dead-lettered. The worker records terminal failure state when the last delivery is reached.
9. A reconciliation timer re-enqueues uploaded/failed documents that have been stuck beyond the configured interval.
10. Application Insights provides request, dependency, exception and Function telemetry.

## Reliability guarantees

- At-least-once delivery with application-level idempotency.
- Deterministic Service Bus `MessageId=documentId` with one-hour duplicate detection.
- Deterministic case identity based on `(documentId,rowNumber)` plus a SQL unique constraint.
- Queue settlement occurs only after the Function completes successfully.
- Blob versioning and SHA-256 protect against processing the wrong content.
- Bounded document concurrency protects SQL and the validation service.

## Batch test

The React UI can generate 153 `.xlsx` documents. Each generated workbook contains 153 rows and 10 columns. That creates a maximum workload of 23,409 row/case operations while the queue limits active document processing to eight workers.

## Production security note

The current deployment profile is intended for an isolated development/test subscription and uses CORS plus short-lived write-only SAS URLs. Before internet-facing production use, place the HTTP APIs behind Microsoft Entra ID/App Service Authentication or an API gateway, disable anonymous access, use private endpoints where required by policy, and replace SQL username/password authentication with managed identity/Entra database authentication.
