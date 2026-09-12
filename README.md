# document-upload-processing

Enterprise-grade document upload and batch processing reference implementation with:

- Azure Functions-style entry points (`upload_document`, `process_batch`)
- Service Bus queue + dead-letter queue handling
- Blob storage abstraction for document persistence
- SQL-backed case tracking and audit trail (SQLite for local/dev)
- Validation service and idempotent processing keys
- Retry flow with max-attempt handling and DLQ fallback
- Observability counters/metrics emission hooks

## Run tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```
