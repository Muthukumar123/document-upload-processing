import { app, InvocationContext, ServiceBusQueueHandler } from "@azure/functions";
import sql from "mssql";
import ExcelJS from "exceljs";
import { createHash } from "node:crypto";
import { config } from "../config.js";
import { blobService } from "../lib/storage.js";
import { db, tx } from "../lib/sql.js";
import type { DocumentMessage } from "../lib/serviceBus.js";

function caseId(documentId: string, row: number): string {
  const h = createHash("sha256").update(`${documentId}:${row}`).digest("hex").slice(0, 32);
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
function hash(buf: Buffer) { return createHash("sha256").update(buf).digest("hex"); }
async function validate(payload: unknown) {
  const r = await fetch(config.VALIDATION_API_URL, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload), signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error(`Validation service returned ${r.status}`);
  return await r.json() as { valid: boolean; reason?: string };
}

export const processDocument: ServiceBusQueueHandler = async (message: unknown, context: InvocationContext) => {
  const m = message as DocumentMessage;
  const pool = await db();
  const claimed = await tx(async t => {
    const r = await new sql.Request(t).input("id", sql.UniqueIdentifier, m.documentId).query(`
      UPDATE dbo.DocumentUpload SET Status='PROCESSING',AttemptCount=AttemptCount+1,UpdatedAt=SYSUTCDATETIME()
      OUTPUT inserted.DocumentId WHERE DocumentId=@id AND Status IN ('QUEUED','FAILED','PROCESSING')`);
    if (!r.recordset.length) return false;
    await new sql.Request(t).input("id", sql.UniqueIdentifier, m.documentId).input("type", sql.VarChar(64), "PROCESSING_STARTED")
      .query("INSERT INTO dbo.AuditEvent(DocumentId,EventType) VALUES(@id,@type)");
    return true;
  });
  if (!claimed) { context.log(`Skipping already-settled document ${m.documentId}`); return; }

  try {
    const blob = blobService.getContainerClient(config.UPLOAD_CONTAINER).getBlockBlobClient(m.blobName);
    const target = m.versionId ? blob.withVersion(m.versionId) : blob;
    const download = await target.download();
    const chunks: Buffer[] = [];
    for await (const chunk of download.readableStreamBody ?? []) chunks.push(Buffer.from(chunk));
    const bytes = Buffer.concat(chunks);
    if (hash(bytes).toLowerCase() !== m.sha256.toLowerCase()) throw new Error("SHA256 checksum mismatch");
    const workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(bytes as any);
    const sheet = workbook.worksheets[0];
    if (!sheet) throw new Error("Workbook has no worksheet");
    let invalid = 0;
    for (let rowNo = 2; rowNo <= sheet.rowCount; rowNo++) {
      const row = sheet.getRow(rowNo);
      const values = Array.from({ length: 10 }, (_, i) => String(row.getCell(i + 1).value ?? "").trim());
      if (values.every(v => !v)) continue;
      const id = caseId(m.documentId, rowNo);
      const existing = await pool.request().input("id", sql.UniqueIdentifier, id).query("SELECT Status FROM dbo.[Case] WHERE CaseId=@id");
      if (existing.recordset[0]?.Status === "COMPLETED" || existing.recordset[0]?.Status === "INVALID") continue;
      const reference = await pool.request().input("key", sql.NVarChar(128), values[0]).query("SELECT ExpectedValue FROM dbo.ReferenceData WHERE ExternalKey=@key");
      const compareMatch = reference.recordset.length === 0 || String(reference.recordset[0].ExpectedValue) === values[1];
      const validation = await validate({ caseId: id, rowNumber: rowNo, values, compareMatch });
      if (!validation.valid) invalid++;
      await tx(async t => {
        await new sql.Request(t).input("caseId", sql.UniqueIdentifier, id).input("documentId", sql.UniqueIdentifier, m.documentId)
          .input("rowNo", sql.Int, rowNo).input("externalKey", sql.NVarChar(128), values[0]).input("payload", sql.NVarChar(sql.MAX), JSON.stringify(values))
          .input("compareMatch", sql.Bit, compareMatch).input("validation", sql.NVarChar(sql.MAX), JSON.stringify(validation)).input("status", sql.VarChar(32), validation.valid ? "COMPLETED" : "INVALID")
          .query(`MERGE dbo.[Case] AS T USING (SELECT @caseId CaseId) S ON T.CaseId=S.CaseId
            WHEN MATCHED THEN UPDATE SET PayloadJson=@payload,CompareMatch=@compareMatch,ValidationJson=@validation,Status=@status,UpdatedAt=SYSUTCDATETIME()
            WHEN NOT MATCHED THEN INSERT(CaseId,DocumentId,RowNumber,ExternalKey,PayloadJson,CompareMatch,ValidationJson,Status) VALUES(@caseId,@documentId,@rowNo,@externalKey,@payload,@compareMatch,@validation,@status);`);
        await new sql.Request(t).input("caseId", sql.UniqueIdentifier, id).input("documentId", sql.UniqueIdentifier, m.documentId)
          .query("IF NOT EXISTS(SELECT 1 FROM dbo.DocumentCaseLink WHERE DocumentId=@documentId AND CaseId=@caseId) INSERT dbo.DocumentCaseLink(DocumentId,CaseId) VALUES(@documentId,@caseId)");
      });
    }
    const finalStatus = invalid ? "PARTIAL_SUCCESS" : "COMPLETED";
    await tx(async t => {
      await new sql.Request(t).input("id", sql.UniqueIdentifier, m.documentId).input("status", sql.VarChar(32), finalStatus)
        .query("UPDATE dbo.DocumentUpload SET Status=@status,CompletedAt=SYSUTCDATETIME(),UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@id");
      await new sql.Request(t).input("id", sql.UniqueIdentifier, m.documentId).input("type", sql.VarChar(64), finalStatus)
        .query("INSERT INTO dbo.AuditEvent(DocumentId,EventType) VALUES(@id,@type)");
    });
    await pool.request().input("batchId", sql.UniqueIdentifier, m.batchId).query(`UPDATE b SET Status = CASE WHEN EXISTS(SELECT 1 FROM dbo.DocumentUpload d WHERE d.BatchId=b.BatchId AND d.Status NOT IN ('COMPLETED','PARTIAL_SUCCESS','FAILED','DEAD_LETTERED')) THEN 'PROCESSING' WHEN EXISTS(SELECT 1 FROM dbo.DocumentUpload d WHERE d.BatchId=b.BatchId AND d.Status IN ('FAILED','DEAD_LETTERED','PARTIAL_SUCCESS')) THEN 'PARTIAL_SUCCESS' ELSE 'COMPLETED' END, UpdatedAt=SYSUTCDATETIME() FROM dbo.UploadBatch b WHERE BatchId=@batchId`);
  } catch (e) {
    context.error(e);
    const deliveryCount = Number(context.triggerMetadata?.deliveryCount ?? 1);
    const status = deliveryCount >= 5 ? "DEAD_LETTERED" : "FAILED";
    await pool.request().input("id", sql.UniqueIdentifier, m.documentId).input("status", sql.VarChar(32), status).input("err", sql.NVarChar(2000), String(e))
      .query("UPDATE dbo.DocumentUpload SET Status=@status,LastError=@err,UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@id; INSERT INTO dbo.AuditEvent(DocumentId,EventType,Details) VALUES(@id,@status,@err)");
    throw e;
  }
};

app.serviceBusQueue("processDocument", { connection: "SERVICE_BUS_CONNECTION", queueName: config.SERVICE_BUS_QUEUE, handler: processDocument });
