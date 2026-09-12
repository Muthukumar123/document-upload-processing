import sqlite3
import unittest

from document_upload_processing import (
    BatchProcessor,
    BlobStorageClient,
    CaseRepository,
    ObservabilityTracker,
    ServiceBusQueue,
    TransientProcessingError,
    ValidationService,
    process_batch,
    upload_document,
)


class AlwaysFailBlobStorage(BlobStorageClient):
    def upload(self, document_id: str, content: bytes) -> str:
        raise TransientProcessingError("temporary blob outage")


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = CaseRepository(sqlite3.connect(":memory:"))
        self.validator = ValidationService()
        self.observability = ObservabilityTracker()

    def test_happy_path_upload_and_process(self) -> None:
        queue = ServiceBusQueue()
        storage = BlobStorageClient()
        processor = BatchProcessor(self.repo, self.validator, storage, self.observability)

        upload_document(
            {
                "case_id": "CASE-001",
                "document_id": "DOC-001",
                "content": b"hello",
                "metadata": {"content_type": "application/pdf"},
            },
            queue,
        )

        self.assertEqual(process_batch(queue, processor), ["processed"])
        self.assertEqual(storage.get("DOC-001"), b"hello")
        self.assertEqual(self.repo.get_case_status("CASE-001"), "processed")
        self.assertEqual(self.observability.counters["processed"], 1)

    def test_idempotent_duplicate_is_skipped(self) -> None:
        queue = ServiceBusQueue()
        storage = BlobStorageClient()
        processor = BatchProcessor(self.repo, self.validator, storage, self.observability)
        request = {
            "case_id": "CASE-002",
            "document_id": "DOC-002",
            "content": b"payload",
            "metadata": {"content_type": "application/pdf"},
        }

        upload_document(request, queue)
        process_batch(queue, processor)
        upload_document(request, queue)

        self.assertEqual(process_batch(queue, processor), ["duplicate"])
        events = self.repo.get_audit_events("CASE-002")
        self.assertEqual(events[-1]["event_type"], "duplicate_skipped")

    def test_retry_and_dead_letter_after_exhaustion(self) -> None:
        queue = ServiceBusQueue()
        storage = AlwaysFailBlobStorage()
        processor = BatchProcessor(
            self.repo,
            self.validator,
            storage,
            self.observability,
            max_retries=2,
        )

        upload_document(
            {
                "case_id": "CASE-003",
                "document_id": "DOC-003",
                "content": b"retry",
                "metadata": {"content_type": "application/pdf"},
            },
            queue,
        )

        self.assertEqual(process_batch(queue, processor), ["retry"])
        self.assertEqual(process_batch(queue, processor), ["dead_lettered"])
        self.assertEqual(len(queue.dlq), 1)
        self.assertEqual(self.repo.get_case_status("CASE-003"), "failed")

    def test_validation_failure_moves_to_dlq(self) -> None:
        queue = ServiceBusQueue()
        storage = BlobStorageClient()
        processor = BatchProcessor(self.repo, self.validator, storage, self.observability)

        upload_document(
            {
                "case_id": "CASE-004",
                "document_id": "DOC-004",
                "content": b"invalid",
                "metadata": {},
            },
            queue,
        )

        self.assertEqual(process_batch(queue, processor), ["validation_failed"])
        self.assertEqual(len(queue.dlq), 1)
        self.assertEqual(self.repo.get_case_status("CASE-004"), "validation_failed")


if __name__ == "__main__":
    unittest.main()
