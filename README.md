# Document Upload Processing

Production-oriented Azure implementation for batch document ingestion and Excel-driven case processing.

## Capabilities

- React batch upload UI and one-click **153-document / 153-row / 10-column** stress scenario
- Direct-to-Blob uploads with short-lived SAS and SHA-256 integrity verification
- Azure Service Bus queue with duplicate detection, PeekLock processing, bounded concurrency and DLQ
- Node.js 20 Azure Functions for upload orchestration, processing, reconciliation and a mock validation API
- Azure SQL for batch/document/case state, reference comparison, audit and document-case associations
- Idempotent case creation and safe message replay
- Blob versioning so workers process the exact uploaded version
- Application Insights + Log Analytics
- Bicep infrastructure-as-code and GitHub Actions CI/CD

## Repository layout

- `frontend/` React + Vite application
- `api/` Azure Functions v4 TypeScript application
- `sql/` durable schema
- `infra/` Azure Bicep
- `docs/` architecture and operations guidance
- `.github/workflows/` CI and Azure deployment workflow

## Processing model

`React -> Upload API -> Blob Storage -> Complete API -> Service Bus -> bounded Function worker -> SQL compare -> Validation API -> Case + document link + audit`

The queue absorbs a batch burst. Only eight documents are processed concurrently, and rows inside each document are processed sequentially, preventing 153 documents × 153 rows from becoming uncontrolled downstream concurrency.

## Deployment

Use `.github/workflows/deploy.yml`. The Azure federated identity must have rights to the target resource group. Configure GitHub environment `azure-dev` with `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and secure `SQL_ADMIN_PASSWORD`.
