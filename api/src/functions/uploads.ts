import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import sql from "mssql";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { config } from "../config.js";
import { db, tx } from "../lib/sql.js";
import { blobService, createUploadSas } from "../lib/storage.js";
import { enqueueDocument } from "../lib/serviceBus.js";

const prepareSchema = z.object({
  files: z.array(z.object({ name: z.string().min(1).max(240), size: z.number().int().positive().max(config.MAX_FILE_BYTES) })).min(1).max(200)
});
const completeSchema = z.object({ batchId: z.string().uuid(), documentId: z.string().uuid(), sha256: z.string().regex(/^[a-f0-9]{64}$/i) });
const safe = (name: string) => name.replace(/[^a-zA-Z0-9._-]/g, "_");

export async function prepareUpload(request: HttpRequest, context: InvocationContext): Promise<HttpResponseInit> {
  const body = prepareSchema.parse(await request.json());
  const batchId = randomUUID();
  const docs = body.files.map(f => ({ ...f, documentId: randomUUID() }));
  await tx(async t => {
    await new sql.Request(t).input("batchId", sql.UniqueIdentifier, batchId).input("count", sql.Int, docs.length)
      .query("INSERT INTO dbo.UploadBatch(BatchId, TotalDocuments, Status) VALUES(@batchId,@count,'UPLOADING')");
    for (const d of docs) {
      const blobName = `${batchId}/${d.documentId}/${safe(d.name)}`;
      await new sql.Request(t).input("documentId", sql.UniqueIdentifier, d.documentId).input("batchId", sql.UniqueIdentifier, batchId)
        .input("fileName", sql.NVarChar(260), d.name).input("blobName", sql.NVarChar(1024), blobName).input("size", sql.BigInt, d.size)
        .query("INSERT INTO dbo.DocumentUpload(DocumentId,BatchId,FileName,BlobName,FileSize,Status) VALUES(@documentId,@batchId,@fileName,@blobName,@size,'PREPARED')");
    }
  });
  context.log(`Prepared batch ${batchId} with ${docs.length} documents`);
  return { status: 201, jsonBody: { batchId, documents: docs.map(d => { const blobName = `${batchId}/${d.documentId}/${safe(d.name)}`; return { documentId: d.documentId, fileName: d.name, uploadUrl: createUploadSas(blobName) }; }) } };
}

export async function completeUpload(request: HttpRequest, context: InvocationContext): Promise<HttpResponseInit> {
  const input = completeSchema.parse(await request.json());
  const pool = await db();
  const row = await pool.request().input("id", sql.UniqueIdentifier, input.documentId).input("batch", sql.UniqueIdentifier, input.batchId)
    .query("SELECT BlobName,Status FROM dbo.DocumentUpload WHERE DocumentId=@id AND BatchId=@batch");
  if (!row.recordset[0]) return { status: 404, jsonBody: { error: "Document not found" } };
  if (["QUEUED","PROCESSING","COMPLETED","PARTIAL_SUCCESS"].includes(row.recordset[0].Status)) return { status: 200, jsonBody: { status: row.recordset[0].Status, idempotent: true } };
  const blobName = row.recordset[0].BlobName as string;
  const blob = blobService.getContainerClient(config.UPLOAD_CONTAINER).getBlockBlobClient(blobName);
  const p = await blob.getProperties();
  await pool.request().input("id", sql.UniqueIdentifier, input.documentId).input("hash", sql.Char(64), input.sha256).input("version", sql.NVarChar(128), p.versionId ?? null)
    .query("UPDATE dbo.DocumentUpload SET Sha256=@hash,BlobVersionId=@version,Status='UPLOADED',UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@id");
  await enqueueDocument({ batchId: input.batchId, documentId: input.documentId, blobName, versionId: p.versionId, sha256: input.sha256 });
  await pool.request().input("id", sql.UniqueIdentifier, input.documentId).query("UPDATE dbo.DocumentUpload SET Status='QUEUED',UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@id");
  context.log(`Queued ${input.documentId}`);
  return { status: 202, jsonBody: { status: "QUEUED" } };
}

export async function batchStatus(request: HttpRequest): Promise<HttpResponseInit> {
  const batchId = request.params.batchId;
  const pool = await db();
  const result = await pool.request().input("batchId", sql.UniqueIdentifier, batchId).query(`
    SELECT b.BatchId,b.TotalDocuments,b.Status,b.CreatedAt,
      SUM(CASE WHEN d.Status IN ('COMPLETED','PARTIAL_SUCCESS') THEN 1 ELSE 0 END) CompletedDocuments,
      SUM(CASE WHEN d.Status='FAILED' THEN 1 ELSE 0 END) FailedDocuments,
      SUM(CASE WHEN d.Status='DEAD_LETTERED' THEN 1 ELSE 0 END) DeadLetteredDocuments,
      COUNT(c.CaseId) TotalCases
    FROM dbo.UploadBatch b LEFT JOIN dbo.DocumentUpload d ON b.BatchId=d.BatchId LEFT JOIN dbo.[Case] c ON d.DocumentId=c.DocumentId
    WHERE b.BatchId=@batchId GROUP BY b.BatchId,b.TotalDocuments,b.Status,b.CreatedAt`);
  return result.recordset[0] ? { status: 200, jsonBody: result.recordset[0] } : { status: 404, jsonBody: { error: "Batch not found" } };
}

app.http("prepareUpload", { methods: ["POST"], authLevel: "anonymous", route: "uploads/prepare", handler: prepareUpload });
app.http("completeUpload", { methods: ["POST"], authLevel: "anonymous", route: "uploads/complete", handler: completeUpload });
app.http("batchStatus", { methods: ["GET"], authLevel: "anonymous", route: "batches/{batchId}", handler: batchStatus });
