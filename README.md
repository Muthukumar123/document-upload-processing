# Document Upload Processing

Production-oriented Azure implementation for batch document ingestion and Excel-driven fund-allocation processing.

## Technology standards

- **React + Vite**: browser user interface only
- **.NET 8 / C# Azure Functions isolated worker**: upload APIs, Service Bus processing, validation mock and reconciliation
- **Python 3.12**: automated integration/load tests, including the 153-document acceptance test
- **Bicep**: all Azure infrastructure-as-code
- **Azure SQL**: durable batch/document/case/audit state

No Node.js/TypeScript backend is used.

## Capabilities

- React batch upload UI
- Direct-to-Blob uploads using short-lived SAS URLs and SHA-256 integrity verification
- Azure Service Bus queue with duplicate detection, PeekLock/manual settlement, bounded concurrency of 8 and DLQ handling
- .NET 8 Azure Functions for orchestration and document processing
- Azure SQL comparison, case persistence, audit and document-case associations
- Deterministic/idempotent case identifiers and safe message replay
- Blob versioning so workers process the exact uploaded version
- Application Insights + Log Analytics
- Python end-to-end test that creates and uploads **153 Excel documents × 153 rows × 10 columns** and verifies **23,409 cases**
- Bicep infrastructure and GitHub Actions CI/CD

## Repository layout

- `frontend/` React + Vite application
- `backend/DocumentUpload.Functions/` .NET 8 isolated Azure Functions
- `tests/` Python tests and Azure load-test harness
- `sql/` durable Azure SQL schema
- `infra/` Azure Bicep
- `docs/` architecture and operations guidance
- `.github/workflows/` CI and Azure deployment workflow

## Processing model

`React -> C# Upload API -> Blob Storage -> C# Complete API -> Service Bus -> bounded C# Function worker -> Azure SQL compare -> Validation API -> Case + document link + audit`

The Service Bus queue absorbs batch bursts. A maximum of eight documents are processed concurrently, while rows within a document are processed in a controlled sequence so 153 documents × 153 rows do not become uncontrolled downstream concurrency.

## 153-document acceptance test

After deployment:

```bash
python -m pip install -r tests/requirements.txt
python tests/load_test.py --base-url https://<function-app>.azurewebsites.net/api --documents 153
```

The test generates the Excel files in Python, uploads all 153 documents through the real API/Blob path, waits for Service Bus processing, and fails unless the batch completes without DLQ documents and produces 23,409 cases.

## Deployment

Use `.github/workflows/deploy.yml`. Configure the GitHub environment `azure-dev` with `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and secure `SQL_ADMIN_PASSWORD`.
