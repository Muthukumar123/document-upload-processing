import sqlite3
import unittest

from document_upload_processing import (
    BatchProcessor,
    BlobStorageClient,
    CaseRepository,
    ObservabilityTracker,
    ServiceBusQueue,
    TransientProcessingError,
    ValidationError,
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

    def test_lock_contention_requeues_message(self) -> None:
        queue = ServiceBusQueue()
        storage = BlobStorageClient()
        processor = BatchProcessor(self.repo, self.validator, storage, self.observability)
        original_lock = self.repo.acquire_processing_lock
        self.repo.acquire_processing_lock = lambda _key: False  # type: ignore[method-assign]

        upload_document(
            {
                "case_id": "CASE-LOCK",
                "document_id": "DOC-LOCK",
                "content": b"locked",
                "metadata": {"content_type": "application/pdf"},
            },
            queue,
        )

        self.assertEqual(process_batch(queue, processor), ["lock_contention"])
        self.repo.acquire_processing_lock = original_lock
        self.assertEqual(process_batch(queue, processor), ["processed"])

    def test_lock_contention_has_dead_letter_cap(self) -> None:
        queue = ServiceBusQueue()
        storage = BlobStorageClient()
        processor = BatchProcessor(
            self.repo,
            self.validator,
            storage,
            self.observability,
            max_retries=1,
        )
        self.repo.acquire_processing_lock = lambda _key: False  # type: ignore[method-assign]
        upload_document(
            {
                "case_id": "CASE-LOCK-DLQ",
                "document_id": "DOC-LOCK-DLQ",
                "content": b"locked",
                "metadata": {"content_type": "application/pdf"},
            },
            queue,
        )
        self.assertEqual(process_batch(queue, processor), ["lock_contention"])
        self.assertEqual(process_batch(queue, processor), ["dead_lettered"])
        self.assertEqual(len(queue.dlq), 1)

    def test_upload_document_validates_request_shape(self) -> None:
        queue = ServiceBusQueue()
        with self.assertRaises(ValidationError):
            upload_document({"case_id": "CASE-005"}, queue)

        with self.assertRaises(ValidationError):
            upload_document(
                {
                    "case_id": "CASE-006",
                    "document_id": "DOC-006",
                    "content": b"data",
                    "metadata": "not-a-dict",
                },
                queue,
            )

        with self.assertRaises(ValidationError):
            upload_document(
                {
                    "case_id": "CASE-007",
                    "document_id": "DOC-007",
                    "content": b"data",
                    "metadata": {"content_type": 1},
                },
                queue,
            )

        with self.assertRaises(ValidationError):
            upload_document(
                {
                    "case_id": 7,
                    "document_id": "DOC-008",
                    "content": b"data",
                    "metadata": {"content_type": "application/pdf"},
                },
                queue,
            )

    def test_upload_document_copies_metadata_payload(self) -> None:
        queue = ServiceBusQueue()
        metadata = {"content_type": "application/pdf"}
        upload_document(
            {
                "case_id": "CASE-009",
                "document_id": "DOC-009",
                "content": b"data",
                "metadata": metadata,
            },
            queue,
        )
        metadata["content_type"] = "text/plain"
        queued = queue.dequeue_batch(1)[0]
        self.assertEqual(queued.metadata["content_type"], "application/pdf")

    def test_upload_document_accepts_bytearray_content(self) -> None:
        queue = ServiceBusQueue()
        upload_document(
            {
                "case_id": "CASE-010",
                "document_id": "DOC-010",
                "content": bytearray(b"bytearray-payload"),
                "metadata": {"content_type": "application/pdf"},
            },
            queue,
        )
        queued = queue.dequeue_batch(1)[0]
        self.assertEqual(queued.content, b"bytearray-payload")


if __name__ == "__main__":
    unittest.main()
