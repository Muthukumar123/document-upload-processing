from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, TypedDict


class ValidationError(Exception):
    """Raised when a document payload fails validation."""


class TransientProcessingError(Exception):
    """Raised for retryable processing failures."""


@dataclass(frozen=True)
class DocumentMessage:
    case_id: str
    document_id: str
    content: bytes
    metadata: dict[str, str]
    attempt: int = 0

    @property
    def idempotency_key(self) -> str:
        return f"{self.case_id}:{self.document_id}"


class UploadRequest(TypedDict):
    case_id: str
    document_id: str
    content: bytes
    metadata: dict[str, str]


class ValidationService:
    def validate(self, message: DocumentMessage) -> None:
        if not message.case_id.strip():
            raise ValidationError("case_id is required")
        if not message.document_id.strip():
            raise ValidationError("document_id is required")
        if not message.content:
            raise ValidationError("content cannot be empty")
        if "content_type" not in message.metadata:
            raise ValidationError("content_type metadata is required")


class BlobStorageClient:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def upload(self, document_id: str, content: bytes) -> str:
        if not content:
            raise TransientProcessingError("unable to upload empty content")
        self._blobs[document_id] = content
        return f"blob://documents/{document_id}"

    def get(self, document_id: str) -> bytes | None:
        return self._blobs.get(document_id)


class CaseRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_document_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processed_documents (
                idempotency_key TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processing_locks (
                idempotency_key TEXT PRIMARY KEY,
                locked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def is_processed(self, idempotency_key: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM processed_documents WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def mark_processed(self, idempotency_key: str) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO processed_documents (idempotency_key, processed_at)
            VALUES (?, ?)
            """,
            (idempotency_key, utc_now()),
        )
        self._connection.commit()

    def acquire_processing_lock(self, idempotency_key: str) -> bool:
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO processing_locks (idempotency_key, locked_at)
            VALUES (?, ?)
            """,
            (idempotency_key, utc_now()),
        )
        self._connection.commit()
        return cursor.rowcount == 1

    def release_processing_lock(self, idempotency_key: str) -> None:
        self._connection.execute(
            "DELETE FROM processing_locks WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        self._connection.commit()

    def upsert_case(self, case_id: str, status: str, last_document_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO cases (case_id, status, last_document_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                status = excluded.status,
                last_document_id = excluded.last_document_id,
                updated_at = excluded.updated_at
            """,
            (case_id, status, last_document_id, utc_now()),
        )
        self._connection.commit()

    def get_case_status(self, case_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT status FROM cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return row["status"] if row else None

    def add_audit_event(
        self,
        case_id: str,
        document_id: str,
        event_type: str,
        details: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events (case_id, document_id, event_type, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, document_id, event_type, details, utc_now()),
        )
        self._connection.commit()

    def get_audit_events(self, case_id: str) -> list[dict[str, str]]:
        rows = self._connection.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]


class ObservabilityTracker:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("document_upload_processing")
        self.counters: dict[str, int] = {}

    def emit(self, metric_name: str, **dimensions: str) -> None:
        self.counters[metric_name] = self.counters.get(metric_name, 0) + 1
        self.logger.info("metric=%s dimensions=%s", metric_name, dimensions)


class ServiceBusQueue:
    def __init__(self) -> None:
        self._messages: list[DocumentMessage] = []
        self._dlq: list[tuple[DocumentMessage, str]] = []

    def enqueue(self, message: DocumentMessage) -> None:
        self._messages.append(message)

    def dequeue_batch(self, batch_size: int) -> list[DocumentMessage]:
        batch = self._messages[:batch_size]
        self._messages = self._messages[batch_size:]
        return batch

    def schedule_retry(self, message: DocumentMessage) -> None:
        self.enqueue(replace(message, attempt=message.attempt + 1))

    def dead_letter(self, message: DocumentMessage, reason: str) -> None:
        self._dlq.append((message, reason))

    @property
    def dlq(self) -> list[tuple[DocumentMessage, str]]:
        return list(self._dlq)


class BatchProcessor:
    """Processes queued documents; max_retries is retries after the initial attempt."""

    def __init__(
        self,
        repository: CaseRepository,
        validator: ValidationService,
        blob_storage: BlobStorageClient,
        observability: ObservabilityTracker,
        max_retries: int = 3,
    ) -> None:
        self.repository = repository
        self.validator = validator
        self.blob_storage = blob_storage
        self.observability = observability
        self.max_retries = max_retries

    def process_message(
        self,
        message: DocumentMessage,
        on_retry: Callable[[DocumentMessage], None],
        on_dlq: Callable[[DocumentMessage, str], None],
        on_reschedule: Callable[[DocumentMessage], None],
    ) -> str:
        if self.repository.is_processed(message.idempotency_key):
            self.repository.add_audit_event(
                message.case_id,
                message.document_id,
                "duplicate_skipped",
                "idempotent skip",
            )
            self.observability.emit("duplicate_skipped", case_id=message.case_id)
            return "duplicate"

        if not self.repository.acquire_processing_lock(message.idempotency_key):
            if message.attempt >= self.max_retries:
                self.repository.upsert_case(message.case_id, "failed", message.document_id)
                self.repository.mark_processed(message.idempotency_key)
                self.repository.add_audit_event(
                    message.case_id,
                    message.document_id,
                    "dead_lettered",
                    "lock contention retries exhausted",
                )
                self.observability.emit("dead_lettered", case_id=message.case_id)
                on_dlq(message, "lock_contention_retries_exhausted")
                return "dead_lettered"
            self.repository.add_audit_event(
                message.case_id,
                message.document_id,
                "lock_contention",
                "already in progress; rescheduled",
            )
            self.observability.emit("lock_contention", case_id=message.case_id)
            on_reschedule(replace(message, attempt=message.attempt + 1))
            return "lock_contention"

        try:
            self.validator.validate(message)
            blob_url = self.blob_storage.upload(message.document_id, message.content)
        except ValidationError as exc:
            self.repository.upsert_case(message.case_id, "validation_failed", message.document_id)
            self.repository.mark_processed(message.idempotency_key)
            self.repository.add_audit_event(
                message.case_id,
                message.document_id,
                "validation_failed",
                str(exc),
            )
            self.observability.emit("validation_failed", case_id=message.case_id)
            on_dlq(message, f"validation_error:{exc}")
            self.repository.release_processing_lock(message.idempotency_key)
            return "validation_failed"
        except TransientProcessingError as exc:
            if message.attempt >= self.max_retries:
                self.repository.upsert_case(message.case_id, "failed", message.document_id)
                self.repository.mark_processed(message.idempotency_key)
                self.repository.add_audit_event(
                    message.case_id,
                    message.document_id,
                    "dead_lettered",
                    str(exc),
                )
                self.observability.emit("dead_lettered", case_id=message.case_id)
                on_dlq(message, f"retries_exhausted:{exc}")
                self.repository.release_processing_lock(message.idempotency_key)
                return "dead_lettered"

            self.repository.add_audit_event(
                message.case_id,
                message.document_id,
                "retry_scheduled",
                str(exc),
            )
            self.observability.emit("retry_scheduled", case_id=message.case_id)
            on_retry(message)
            self.repository.release_processing_lock(message.idempotency_key)
            return "retry"
        except Exception:
            self.repository.release_processing_lock(message.idempotency_key)
            raise

        self.repository.upsert_case(message.case_id, "processed", message.document_id)
        self.repository.mark_processed(message.idempotency_key)
        self.repository.add_audit_event(
            message.case_id,
            message.document_id,
            "processed",
            blob_url,
        )
        self.observability.emit("processed", case_id=message.case_id)
        self.repository.release_processing_lock(message.idempotency_key)
        return "processed"


def upload_document(request: UploadRequest, queue: ServiceBusQueue) -> DocumentMessage:
    """Validate upload input and enqueue a document for batch processing."""
    required_fields = ("case_id", "document_id", "content", "metadata")
    missing_fields = [field for field in required_fields if field not in request]
    if missing_fields:
        raise ValidationError(f"missing required fields: {', '.join(missing_fields)}")

    metadata = request["metadata"]
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a dictionary")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()):
        raise ValidationError("metadata keys and values must be strings")

    content = request["content"]
    if isinstance(content, bytearray):
        content = bytes(content)
    if not isinstance(content, bytes):
        raise ValidationError("content must be bytes")
    if not isinstance(request["case_id"], str) or not isinstance(request["document_id"], str):
        raise ValidationError("case_id and document_id must be strings")

    message = DocumentMessage(
        case_id=request["case_id"],
        document_id=request["document_id"],
        content=content,
        metadata=dict(metadata),
    )
    queue.enqueue(message)
    return message


def process_batch(
    queue: ServiceBusQueue,
    processor: BatchProcessor,
    batch_size: int = 10,
) -> list[str]:
    results: list[str] = []
    for message in queue.dequeue_batch(batch_size):
        result = processor.process_message(
            message,
            on_retry=queue.schedule_retry,
            on_dlq=queue.dead_letter,
            on_reschedule=queue.enqueue,
        )
        results.append(result)
    return results


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
