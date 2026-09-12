import argparse
import asyncio
import hashlib
import io
import time
from dataclasses import dataclass

import httpx
from openpyxl import Workbook


def make_xlsx(index: int, rows: int = 153, cols: int = 10) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "FundAllocation"
    ws.append([f"Column{i}" for i in range(1, cols + 1)])
    for row in range(1, rows + 1):
        values = [f"FUND-{index:03d}-{row:03d}", f"VALUE-{row:03d}"]
        values.extend([f"DATA-{index}-{row}-{col}" for col in range(3, cols + 1)])
        ws.append(values)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


@dataclass
class TestDocument:
    name: str
    content: bytes
    sha256: str


def build_documents(count: int) -> list[TestDocument]:
    docs = []
    for i in range(1, count + 1):
        content = make_xlsx(i)
        docs.append(TestDocument(
            name=f"fund-allocation-{i:03d}.xlsx",
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        ))
    return docs


async def run(base_url: str, document_count: int, upload_concurrency: int, timeout_minutes: int) -> None:
    docs = build_documents(document_count)
    async with httpx.AsyncClient(timeout=60.0) as client:
        prepare = await client.post(
            f"{base_url}/uploads/prepare",
            json={"files": [{"name": d.name, "size": len(d.content)} for d in docs]},
        )
        prepare.raise_for_status()
        prepared = prepare.json()
        batch_id = prepared["batchId"]
        upload_targets = prepared["documents"]
        by_name = {d.name: d for d in docs}
        semaphore = asyncio.Semaphore(upload_concurrency)

        async def upload(target: dict) -> None:
            async with semaphore:
                doc = by_name[target["fileName"]]
                put = await client.put(
                    target["uploadUrl"],
                    content=doc.content,
                    headers={"x-ms-blob-type": "BlockBlob", "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                )
                put.raise_for_status()
                completed = await client.post(
                    f"{base_url}/uploads/complete",
                    json={"batchId": batch_id, "documentId": target["documentId"], "sha256": doc.sha256},
                )
                completed.raise_for_status()

        started = time.monotonic()
        await asyncio.gather(*(upload(target) for target in upload_targets))
        print(f"Uploaded and queued {document_count} documents in {time.monotonic() - started:.2f}s")

        deadline = time.monotonic() + timeout_minutes * 60
        previous = None
        while time.monotonic() < deadline:
            response = await client.get(f"{base_url}/batches/{batch_id}")
            response.raise_for_status()
            status = response.json()
            snapshot = (
                status.get("status"),
                status.get("completedDocuments"),
                status.get("failedDocuments"),
                status.get("deadLetteredDocuments"),
                status.get("totalCases"),
            )
            if snapshot != previous:
                print(status)
                previous = snapshot
            if status.get("completedDocuments") == document_count:
                elapsed = time.monotonic() - started
                expected_cases = document_count * 153
                if status.get("deadLetteredDocuments", 0) != 0:
                    raise AssertionError(f"DLQ documents found: {status}")
                if status.get("totalCases") != expected_cases:
                    raise AssertionError(f"Expected {expected_cases} cases, got {status.get('totalCases')}")
                print(f"PASS: {document_count} documents / {expected_cases} cases completed in {elapsed:.2f}s")
                return
            await asyncio.sleep(5)
        raise TimeoutError(f"Batch {batch_id} did not finish within {timeout_minutes} minutes")


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end Document Upload Azure load test")
    parser.add_argument("--base-url", required=True, help="Function API base URL, e.g. https://app.azurewebsites.net/api")
    parser.add_argument("--documents", type=int, default=153)
    parser.add_argument("--upload-concurrency", type=int, default=10)
    parser.add_argument("--timeout-minutes", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(run(args.base_url.rstrip("/"), args.documents, args.upload_concurrency, args.timeout_minutes))


if __name__ == "__main__":
    main()
