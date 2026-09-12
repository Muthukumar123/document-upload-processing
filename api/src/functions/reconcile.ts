import { app, InvocationContext, Timer } from "@azure/functions";
import sql from "mssql";
import { db } from "../lib/sql.js";
import { blobService } from "../lib/storage.js";
import { enqueueDocument } from "../lib/serviceBus.js";
import { config } from "../config.js";

export async function reconcile(_: Timer, context: InvocationContext): Promise<void> {
  const pool = await db();
  const rows = await pool.request().input("minutes", sql.Int, config.RECONCILE_AFTER_MINUTES).query(`SELECT TOP 100 BatchId,DocumentId,BlobName,Sha256,BlobVersionId,Status FROM dbo.DocumentUpload WHERE Status IN ('PREPARED','UPLOADED','FAILED') AND UpdatedAt < DATEADD(minute,-@minutes,SYSUTCDATETIME()) ORDER BY UpdatedAt`);
  for (const d of rows.recordset) {
    try {
      const blob = blobService.getContainerClient(config.UPLOAD_CONTAINER).getBlockBlobClient(d.BlobName);
      const exists = await blob.exists();
      if (!exists) continue;
      const props = await blob.getProperties();
      if (!d.Sha256) { context.warn(`Blob ${d.BlobName} exists but has no checksum; client completion is required`); continue; }
      await enqueueDocument({ batchId: d.BatchId, documentId: d.DocumentId, blobName: d.BlobName, versionId: d.BlobVersionId ?? props.versionId, sha256: d.Sha256 });
      await pool.request().input("id", sql.UniqueIdentifier, d.DocumentId).query("UPDATE dbo.DocumentUpload SET Status='QUEUED',UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@id");
    } catch (e) { context.error(`Reconcile failed for ${d.DocumentId}`, e); }
  }
}
app.timer("reconcile", { schedule: "0 */5 * * * *", handler: reconcile });
