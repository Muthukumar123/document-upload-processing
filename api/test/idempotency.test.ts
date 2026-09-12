import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

function caseId(documentId: string, row: number): string {
  const h = createHash("sha256").update(`${documentId}:${row}`).digest("hex").slice(0, 32);
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

test("case id is deterministic", () => {
  const doc = "0d2158c4-4636-4c1e-9623-748bbf912caa";
  assert.equal(caseId(doc, 10), caseId(doc, 10));
  assert.notEqual(caseId(doc, 10), caseId(doc, 11));
});
