using System.Data;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Azure.Messaging.ServiceBus;
using Azure.Storage.Blobs;
using Azure.Storage.Sas;
using ClosedXML.Excel;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Logging;

namespace DocumentUpload.Functions;

public record PrepareFile(string Name, long Size);
public record PrepareRequest(List<PrepareFile> Files);
public record CompleteRequest(Guid BatchId, Guid DocumentId, string Sha256);
public record DocumentMessage(Guid BatchId, Guid DocumentId, string BlobName, string? VersionId, string Sha256);
public record ValidationRequest(Guid CaseId, int RowNumber, string[] Values, bool CompareMatch);
public record ValidationResponse(bool Valid, string? Reason = null);
public record ValidationBatchItem(Guid CaseId, bool Valid, string? Reason = null);
public record ParsedRow(Guid CaseId, int RowNumber, string[] Values);
public record ProcessedRow(Guid CaseId, int RowNumber, string ExternalKey, string PayloadJson, bool CompareMatch, string ValidationJson, string Status);

public static class Settings
{
    public static string Storage => Environment.GetEnvironmentVariable("AzureWebJobsStorage") ?? throw new InvalidOperationException("AzureWebJobsStorage missing");
    public static string Sql => Environment.GetEnvironmentVariable("SQL_CONNECTION_STRING") ?? throw new InvalidOperationException("SQL_CONNECTION_STRING missing");
    public static string ServiceBus => Environment.GetEnvironmentVariable("SERVICE_BUS_CONNECTION") ?? throw new InvalidOperationException("SERVICE_BUS_CONNECTION missing");
    public static string Queue => Environment.GetEnvironmentVariable("SERVICE_BUS_QUEUE") ?? "document-processing";
    public static string Container => Environment.GetEnvironmentVariable("UPLOAD_CONTAINER") ?? "incoming";
    public static string ValidationUrl => Environment.GetEnvironmentVariable("VALIDATION_API_URL") ?? throw new InvalidOperationException("VALIDATION_API_URL missing");
    public static string ValidationBatchUrl => Environment.GetEnvironmentVariable("VALIDATION_BATCH_API_URL") ?? ValidationUrl.Replace("/mock/validate", "/mock/validate-batch", StringComparison.OrdinalIgnoreCase);
    public static int ValidationBatchSize => int.TryParse(Environment.GetEnvironmentVariable("VALIDATION_BATCH_SIZE"), out var v) && v > 0 ? Math.Min(v, 500) : 200;
    public static long MaxFileBytes => long.TryParse(Environment.GetEnvironmentVariable("MAX_FILE_BYTES"), out var v) ? v : 55L * 1024 * 1024;
}

public static class Db
{
    public static async Task<T> Tx<T>(Func<SqlConnection, SqlTransaction, Task<T>> body)
    {
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        await using var tx = (SqlTransaction)await c.BeginTransactionAsync();
        try
        {
            var value = await body(c, tx);
            await tx.CommitAsync();
            return value;
        }
        catch
        {
            await tx.RollbackAsync();
            throw;
        }
    }
}

public class UploadFunctions
{
    [Function("PrepareUpload")]
    public async Task<HttpResponseData> Prepare([HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "uploads/prepare")] HttpRequestData req)
    {
        var body = await JsonSerializer.DeserializeAsync<PrepareRequest>(req.Body, new JsonSerializerOptions(JsonSerializerDefaults.Web));
        if (body?.Files is null || body.Files.Count is < 1 or > 200 || body.Files.Any(f => string.IsNullOrWhiteSpace(f.Name) || f.Size <= 0 || f.Size > Settings.MaxFileBytes))
            return await Json(req, HttpStatusCode.BadRequest, new { error = "Invalid file list" });

        var batchId = Guid.NewGuid();
        var docs = body.Files.Select(f => new { file = f, id = Guid.NewGuid() }).ToList();
        await Db.Tx(async (c, tx) =>
        {
            await using (var cmd = new SqlCommand("INSERT dbo.UploadBatch(BatchId,TotalDocuments,Status) VALUES(@b,@n,'UPLOADING')", c, tx))
            {
                cmd.Parameters.AddWithValue("@b", batchId);
                cmd.Parameters.AddWithValue("@n", docs.Count);
                await cmd.ExecuteNonQueryAsync();
            }

            foreach (var d in docs)
            {
                var blobName = $"{batchId}/{d.id}/{Safe(d.file.Name)}";
                await using var cmd = new SqlCommand("INSERT dbo.DocumentUpload(DocumentId,BatchId,FileName,BlobName,FileSize,Status) VALUES(@d,@b,@f,@blob,@s,'PREPARED')", c, tx);
                cmd.Parameters.AddWithValue("@d", d.id);
                cmd.Parameters.AddWithValue("@b", batchId);
                cmd.Parameters.AddWithValue("@f", d.file.Name);
                cmd.Parameters.AddWithValue("@blob", blobName);
                cmd.Parameters.AddWithValue("@s", d.file.Size);
                await cmd.ExecuteNonQueryAsync();
            }
            return true;
        });

        var container = new BlobServiceClient(Settings.Storage).GetBlobContainerClient(Settings.Container);
        var responseDocs = docs.Select(d =>
        {
            var blobName = $"{batchId}/{d.id}/{Safe(d.file.Name)}";
            var blob = container.GetBlobClient(blobName);
            var sas = blob.GenerateSasUri(BlobSasPermissions.Create | BlobSasPermissions.Write, DateTimeOffset.UtcNow.AddMinutes(20));
            return new { documentId = d.id, fileName = d.file.Name, uploadUrl = sas.ToString() };
        });
        return await Json(req, HttpStatusCode.Created, new { batchId, documents = responseDocs });
    }

    [Function("CompleteUpload")]
    public async Task<HttpResponseData> Complete([HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "uploads/complete")] HttpRequestData req)
    {
        var input = await JsonSerializer.DeserializeAsync<CompleteRequest>(req.Body, new JsonSerializerOptions(JsonSerializerDefaults.Web));
        if (input is null || input.Sha256.Length != 64) return await Json(req, HttpStatusCode.BadRequest, new { error = "Invalid request" });

        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        string? blobName = null;
        string? status = null;
        await using (var cmd = new SqlCommand("SELECT BlobName,Status FROM dbo.DocumentUpload WHERE DocumentId=@d AND BatchId=@b", c))
        {
            cmd.Parameters.AddWithValue("@d", input.DocumentId);
            cmd.Parameters.AddWithValue("@b", input.BatchId);
            await using var r = await cmd.ExecuteReaderAsync();
            if (await r.ReadAsync())
            {
                blobName = r.GetString(0);
                status = r.GetString(1);
            }
        }
        if (blobName is null) return await Json(req, HttpStatusCode.NotFound, new { error = "Document not found" });
        if (status is "QUEUED" or "PROCESSING" or "COMPLETED" or "PARTIAL_SUCCESS") return await Json(req, HttpStatusCode.OK, new { status, idempotent = true });

        var blob = new BlobServiceClient(Settings.Storage).GetBlobContainerClient(Settings.Container).GetBlobClient(blobName);
        var props = await blob.GetPropertiesAsync();
        await using (var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Sha256=@h,BlobVersionId=@v,Status='UPLOADED',UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@d", c))
        {
            cmd.Parameters.AddWithValue("@h", input.Sha256);
            cmd.Parameters.AddWithValue("@v", (object?)props.Value.VersionId ?? DBNull.Value);
            cmd.Parameters.AddWithValue("@d", input.DocumentId);
            await cmd.ExecuteNonQueryAsync();
        }

        await using var sb = new ServiceBusClient(Settings.ServiceBus);
        var sender = sb.CreateSender(Settings.Queue);
        var msg = new DocumentMessage(input.BatchId, input.DocumentId, blobName, props.Value.VersionId, input.Sha256);
        await sender.SendMessageAsync(new ServiceBusMessage(JsonSerializer.Serialize(msg, new JsonSerializerOptions(JsonSerializerDefaults.Web))) { MessageId = input.DocumentId.ToString() });
        await using (var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Status='QUEUED',UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@d", c))
        {
            cmd.Parameters.AddWithValue("@d", input.DocumentId);
            await cmd.ExecuteNonQueryAsync();
        }
        return await Json(req, HttpStatusCode.Accepted, new { status = "QUEUED" });
    }

    [Function("BatchStatus")]
    public async Task<HttpResponseData> Status([HttpTrigger(AuthorizationLevel.Anonymous, "get", Route = "batches/{batchId:guid}")] HttpRequestData req, Guid batchId)
    {
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        await using var cmd = new SqlCommand(@"SELECT b.BatchId,b.TotalDocuments,b.Status,b.CreatedAt,
COUNT(DISTINCT CASE WHEN d.Status IN ('COMPLETED','PARTIAL_SUCCESS') THEN d.DocumentId END) CompletedDocuments,
COUNT(DISTINCT CASE WHEN d.Status='FAILED' THEN d.DocumentId END) FailedDocuments,
COUNT(DISTINCT CASE WHEN d.Status='DEAD_LETTERED' THEN d.DocumentId END) DeadLetteredDocuments,
COUNT(ca.CaseId) TotalCases
FROM dbo.UploadBatch b LEFT JOIN dbo.DocumentUpload d ON b.BatchId=d.BatchId LEFT JOIN dbo.[Case] ca ON d.DocumentId=ca.DocumentId
WHERE b.BatchId=@b GROUP BY b.BatchId,b.TotalDocuments,b.Status,b.CreatedAt", c);
        cmd.Parameters.AddWithValue("@b", batchId);
        await using var r = await cmd.ExecuteReaderAsync();
        if (!await r.ReadAsync()) return await Json(req, HttpStatusCode.NotFound, new { error = "Batch not found" });
        return await Json(req, HttpStatusCode.OK, new
        {
            batchId = r.GetGuid(0),
            totalDocuments = r.GetInt32(1),
            status = r.GetString(2),
            createdAt = r.GetDateTime(3),
            completedDocuments = Convert.ToInt32(r.GetValue(4)),
            failedDocuments = Convert.ToInt32(r.GetValue(5)),
            deadLetteredDocuments = Convert.ToInt32(r.GetValue(6)),
            totalCases = Convert.ToInt32(r.GetValue(7))
        });
    }

    private static string Safe(string s) => string.Concat(s.Select(ch => char.IsLetterOrDigit(ch) || ".-_".Contains(ch) ? ch : '_'));
    private static async Task<HttpResponseData> Json(HttpRequestData req, HttpStatusCode code, object body)
    {
        var r = req.CreateResponse(code);
        await r.WriteAsJsonAsync(body);
        return r;
    }
}

public class ProcessingFunctions
{
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(30) };
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    [Function("ProcessDocument")]
    public async Task Process([ServiceBusTrigger("%SERVICE_BUS_QUEUE%", Connection = "SERVICE_BUS_CONNECTION", AutoCompleteMessages = false)] ServiceBusReceivedMessage message, ServiceBusMessageActions actions, FunctionContext context)
    {
        var log = context.GetLogger("ProcessDocument");
        var m = JsonSerializer.Deserialize<DocumentMessage>(message.Body, JsonOptions) ?? throw new InvalidOperationException("Invalid message");
        try
        {
            var claimed = await Db.Tx(async (c, tx) =>
            {
                await using var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Status='PROCESSING',AttemptCount=AttemptCount+1,UpdatedAt=SYSUTCDATETIME() OUTPUT inserted.DocumentId WHERE DocumentId=@d AND Status IN ('QUEUED','FAILED','PROCESSING')", c, tx);
                cmd.Parameters.AddWithValue("@d", m.DocumentId);
                var v = await cmd.ExecuteScalarAsync();
                if (v is null) return false;
                await using var audit = new SqlCommand("INSERT dbo.AuditEvent(DocumentId,EventType) VALUES(@d,'PROCESSING_STARTED')", c, tx);
                audit.Parameters.AddWithValue("@d", m.DocumentId);
                await audit.ExecuteNonQueryAsync();
                return true;
            });
            if (!claimed)
            {
                await actions.CompleteMessageAsync(message);
                return;
            }

            var blob = new BlobServiceClient(Settings.Storage).GetBlobContainerClient(Settings.Container).GetBlobClient(m.BlobName);
            if (!string.IsNullOrWhiteSpace(m.VersionId)) blob = blob.WithVersion(m.VersionId);
            await using var ms = new MemoryStream();
            await blob.DownloadToAsync(ms);
            var bytes = ms.ToArray();
            var actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            if (!actual.Equals(m.Sha256, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("SHA256 checksum mismatch");

            var rows = ParseRows(bytes, m.DocumentId);
            var finishedCaseIds = await LoadFinishedCaseIds(m.DocumentId);
            var pending = rows.Where(r => !finishedCaseIds.Contains(r.CaseId)).ToList();

            if (pending.Count > 0)
            {
                var references = await LoadReferenceData(pending.Select(r => r.Values[0]).Distinct(StringComparer.OrdinalIgnoreCase));
                var requests = pending.Select(r =>
                {
                    references.TryGetValue(r.Values[0], out var expected);
                    var compare = expected is null || string.Equals(expected, r.Values[1], StringComparison.Ordinal);
                    return new ValidationRequest(r.CaseId, r.RowNumber, r.Values, compare);
                }).ToList();

                var validationResults = await ValidateInBatches(requests);
                var processed = requests.Select(v =>
                {
                    if (!validationResults.TryGetValue(v.CaseId, out var validation)) throw new InvalidOperationException($"Validation result missing for case {v.CaseId}");
                    return new ProcessedRow(
                        v.CaseId,
                        v.RowNumber,
                        v.Values[0],
                        JsonSerializer.Serialize(v.Values, JsonOptions),
                        v.CompareMatch,
                        JsonSerializer.Serialize(new ValidationResponse(validation.Valid, validation.Reason), JsonOptions),
                        validation.Valid ? "COMPLETED" : "INVALID");
                }).ToList();

                await PersistRows(m.DocumentId, processed);
            }

            var hasInvalid = await HasInvalidCases(m.DocumentId);
            var finalStatus = hasInvalid ? "PARTIAL_SUCCESS" : "COMPLETED";
            await Db.Tx(async (c, tx) =>
            {
                await using var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Status=@s,CompletedAt=SYSUTCDATETIME(),UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@d; INSERT dbo.AuditEvent(DocumentId,EventType) VALUES(@d,@s)", c, tx);
                cmd.Parameters.AddWithValue("@s", finalStatus);
                cmd.Parameters.AddWithValue("@d", m.DocumentId);
                await cmd.ExecuteNonQueryAsync();
                return true;
            });

            await using (var c = new SqlConnection(Settings.Sql))
            {
                await c.OpenAsync();
                await using var cmd = new SqlCommand("UPDATE b SET Status=CASE WHEN EXISTS(SELECT 1 FROM dbo.DocumentUpload d WHERE d.BatchId=b.BatchId AND d.Status NOT IN ('COMPLETED','PARTIAL_SUCCESS','FAILED','DEAD_LETTERED')) THEN 'PROCESSING' WHEN EXISTS(SELECT 1 FROM dbo.DocumentUpload d WHERE d.BatchId=b.BatchId AND d.Status IN ('FAILED','DEAD_LETTERED','PARTIAL_SUCCESS')) THEN 'PARTIAL_SUCCESS' ELSE 'COMPLETED' END,UpdatedAt=SYSUTCDATETIME() FROM dbo.UploadBatch b WHERE BatchId=@b", c);
                cmd.Parameters.AddWithValue("@b", m.BatchId);
                await cmd.ExecuteNonQueryAsync();
            }

            log.LogInformation("Processed document {DocumentId}: {RowCount} rows, {PendingCount} new rows", m.DocumentId, rows.Count, pending.Count);
            await actions.CompleteMessageAsync(message);
        }
        catch (Exception ex)
        {
            log.LogError(ex, "Document processing failed {DocumentId}", m.DocumentId);
            var terminal = message.DeliveryCount >= 5;
            var status = terminal ? "DEAD_LETTERED" : "FAILED";
            await using (var c = new SqlConnection(Settings.Sql))
            {
                await c.OpenAsync();
                await using var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Status=@s,LastError=@e,UpdatedAt=SYSUTCDATETIME() WHERE DocumentId=@d; INSERT dbo.AuditEvent(DocumentId,EventType,Details) VALUES(@d,@s,@e)", c);
                cmd.Parameters.AddWithValue("@s", status);
                cmd.Parameters.AddWithValue("@e", ex.ToString()[..Math.Min(ex.ToString().Length, 2000)]);
                cmd.Parameters.AddWithValue("@d", m.DocumentId);
                await cmd.ExecuteNonQueryAsync();
            }
            if (terminal) await actions.DeadLetterMessageAsync(message, "MaxDeliveryCountReached", ex.Message);
            else await actions.AbandonMessageAsync(message);
        }
    }

    [Function("MockValidation")]
    public async Task<HttpResponseData> Validate([HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "mock/validate")] HttpRequestData req)
    {
        var input = await JsonSerializer.DeserializeAsync<ValidationRequest>(req.Body, JsonOptions);
        var valid = input is not null && input.Values.Length == 10 && input.Values[0].Length > 0 && input.CompareMatch;
        var r = req.CreateResponse(HttpStatusCode.OK);
        await r.WriteAsJsonAsync(new ValidationResponse(valid, valid ? null : "Missing key or SQL comparison failed"));
        return r;
    }

    [Function("MockValidationBatch")]
    public async Task<HttpResponseData> ValidateBatch([HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "mock/validate-batch")] HttpRequestData req)
    {
        var inputs = await JsonSerializer.DeserializeAsync<List<ValidationRequest>>(req.Body, JsonOptions) ?? [];
        if (inputs.Count > 500)
        {
            var bad = req.CreateResponse(HttpStatusCode.BadRequest);
            await bad.WriteAsJsonAsync(new { error = "Maximum validation batch size is 500" });
            return bad;
        }

        var results = inputs.Select(input =>
        {
            var valid = input.Values.Length == 10 && input.Values[0].Length > 0 && input.CompareMatch;
            return new ValidationBatchItem(input.CaseId, valid, valid ? null : "Missing key or SQL comparison failed");
        }).ToList();
        var r = req.CreateResponse(HttpStatusCode.OK);
        await r.WriteAsJsonAsync(results);
        return r;
    }

    [Function("Reconcile")]
    public async Task Reconcile([TimerTrigger("0 */5 * * * *")] TimerInfo timer)
    {
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        await using var cmd = new SqlCommand("UPDATE dbo.DocumentUpload SET Status='FAILED',LastError='Reconciliation marked stuck processing',UpdatedAt=SYSUTCDATETIME() WHERE Status='PROCESSING' AND UpdatedAt<DATEADD(minute,-30,SYSUTCDATETIME())", c);
        await cmd.ExecuteNonQueryAsync();
    }

    private static List<ParsedRow> ParseRows(byte[] bytes, Guid documentId)
    {
        using var wb = new XLWorkbook(new MemoryStream(bytes));
        var ws = wb.Worksheet(1);
        var lastRow = ws.LastRowUsed()?.RowNumber() ?? 1;
        var rows = new List<ParsedRow>(Math.Max(0, lastRow - 1));
        for (var rowNo = 2; rowNo <= lastRow; rowNo++)
        {
            var values = Enumerable.Range(1, 10).Select(i => ws.Cell(rowNo, i).GetValue<string>().Trim()).ToArray();
            if (values.All(string.IsNullOrEmpty)) continue;
            rows.Add(new ParsedRow(DeterministicCaseId(documentId, rowNo), rowNo, values));
        }
        return rows;
    }

    private static async Task<HashSet<Guid>> LoadFinishedCaseIds(Guid documentId)
    {
        var result = new HashSet<Guid>();
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        await using var cmd = new SqlCommand("SELECT CaseId FROM dbo.[Case] WHERE DocumentId=@d AND Status IN ('COMPLETED','INVALID')", c);
        cmd.Parameters.AddWithValue("@d", documentId);
        await using var r = await cmd.ExecuteReaderAsync();
        while (await r.ReadAsync()) result.Add(r.GetGuid(0));
        return result;
    }

    private static async Task<Dictionary<string, string>> LoadReferenceData(IEnumerable<string> keys)
    {
        var uniqueKeys = keys.Where(k => !string.IsNullOrWhiteSpace(k)).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        foreach (var chunk in uniqueKeys.Chunk(500))
        {
            var names = chunk.Select((_, i) => $"@k{i}").ToArray();
            await using var cmd = new SqlCommand($"SELECT ExternalKey,ExpectedValue FROM dbo.ReferenceData WHERE ExternalKey IN ({string.Join(',', names)})", c);
            for (var i = 0; i < chunk.Length; i++) cmd.Parameters.AddWithValue(names[i], chunk[i]);
            await using var r = await cmd.ExecuteReaderAsync();
            while (await r.ReadAsync()) result[r.GetString(0)] = r.GetString(1);
        }
        return result;
    }

    private static async Task<Dictionary<Guid, ValidationBatchItem>> ValidateInBatches(List<ValidationRequest> requests)
    {
        var result = new Dictionary<Guid, ValidationBatchItem>();
        foreach (var chunk in requests.Chunk(Settings.ValidationBatchSize))
        {
            var payload = JsonSerializer.Serialize(chunk, JsonOptions);
            using var resp = await Http.PostAsync(Settings.ValidationBatchUrl, new StringContent(payload, Encoding.UTF8, "application/json"));
            resp.EnsureSuccessStatusCode();
            var values = JsonSerializer.Deserialize<List<ValidationBatchItem>>(await resp.Content.ReadAsStringAsync(), JsonOptions) ?? throw new InvalidOperationException("Invalid batch validation response");
            foreach (var value in values) result[value.CaseId] = value;
        }
        return result;
    }

    private static async Task PersistRows(Guid documentId, List<ProcessedRow> rows)
    {
        if (rows.Count == 0) return;
        await Db.Tx(async (c, tx) =>
        {
            await using (var create = new SqlCommand(@"CREATE TABLE #ProcessedCase(
CaseId uniqueidentifier NOT NULL,
RowNumber int NOT NULL,
ExternalKey nvarchar(128) NOT NULL,
PayloadJson nvarchar(max) NOT NULL,
CompareMatch bit NOT NULL,
ValidationJson nvarchar(max) NOT NULL,
Status varchar(32) NOT NULL);", c, tx))
            {
                await create.ExecuteNonQueryAsync();
            }

            var table = new DataTable();
            table.Columns.Add("CaseId", typeof(Guid));
            table.Columns.Add("RowNumber", typeof(int));
            table.Columns.Add("ExternalKey", typeof(string));
            table.Columns.Add("PayloadJson", typeof(string));
            table.Columns.Add("CompareMatch", typeof(bool));
            table.Columns.Add("ValidationJson", typeof(string));
            table.Columns.Add("Status", typeof(string));
            foreach (var row in rows) table.Rows.Add(row.CaseId, row.RowNumber, row.ExternalKey, row.PayloadJson, row.CompareMatch, row.ValidationJson, row.Status);

            using (var bulk = new SqlBulkCopy(c, SqlBulkCopyOptions.Default, tx) { DestinationTableName = "#ProcessedCase", BatchSize = rows.Count })
            {
                foreach (DataColumn column in table.Columns) bulk.ColumnMappings.Add(column.ColumnName, column.ColumnName);
                await bulk.WriteToServerAsync(table);
            }

            await using var merge = new SqlCommand(@"MERGE dbo.[Case] AS T
USING #ProcessedCase AS S ON T.CaseId=S.CaseId
WHEN MATCHED THEN UPDATE SET PayloadJson=S.PayloadJson,CompareMatch=S.CompareMatch,ValidationJson=S.ValidationJson,Status=S.Status,UpdatedAt=SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT(CaseId,DocumentId,RowNumber,ExternalKey,PayloadJson,CompareMatch,ValidationJson,Status)
VALUES(S.CaseId,@documentId,S.RowNumber,S.ExternalKey,S.PayloadJson,S.CompareMatch,S.ValidationJson,S.Status);
INSERT dbo.DocumentCaseLink(DocumentId,CaseId)
SELECT @documentId,S.CaseId FROM #ProcessedCase S
WHERE NOT EXISTS(SELECT 1 FROM dbo.DocumentCaseLink L WHERE L.DocumentId=@documentId AND L.CaseId=S.CaseId);", c, tx);
            merge.Parameters.AddWithValue("@documentId", documentId);
            await merge.ExecuteNonQueryAsync();
            return true;
        });
    }

    private static async Task<bool> HasInvalidCases(Guid documentId)
    {
        await using var c = new SqlConnection(Settings.Sql);
        await c.OpenAsync();
        await using var cmd = new SqlCommand("SELECT CASE WHEN EXISTS(SELECT 1 FROM dbo.[Case] WHERE DocumentId=@d AND Status='INVALID') THEN 1 ELSE 0 END", c);
        cmd.Parameters.AddWithValue("@d", documentId);
        return Convert.ToInt32(await cmd.ExecuteScalarAsync()) == 1;
    }

    private static Guid DeterministicCaseId(Guid documentId, int row)
    {
        var h = SHA256.HashData(Encoding.UTF8.GetBytes($"{documentId}:{row}"));
        Span<byte> b = stackalloc byte[16];
        h.AsSpan(0, 16).CopyTo(b);
        return new Guid(b);
    }
}
