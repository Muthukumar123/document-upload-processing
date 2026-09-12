using Azure.Messaging.ServiceBus;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace DocumentUpload.Functions;

public class RecoveryFunctions
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    [Function("RequeueStuckDocuments")]
    public async Task RequeueStuckDocuments(
        [TimerTrigger("0 */1 * * * *")] TimerInfo timer,
        FunctionContext context)
    {
        var log = context.GetLogger("RequeueStuckDocuments");
        var staleSeconds = int.TryParse(Environment.GetEnvironmentVariable("RECOVERY_STALE_SECONDS"), out var configured)
            ? Math.Max(configured, 60)
            : 90;

        var candidates = new List<RecoveryCandidate>();

        await using (var connection = new SqlConnection(Settings.Sql))
        {
            await connection.OpenAsync();
            await using var transaction = (SqlTransaction)await connection.BeginTransactionAsync();

            await using var command = new SqlCommand(@"
;WITH stale AS
(
    SELECT TOP (20)
        DocumentId,
        BatchId,
        BlobName,
        BlobVersionId,
        Sha256,
        AttemptCount
    FROM dbo.DocumentUpload WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE
        Sha256 IS NOT NULL
        AND (
            (Status IN ('PROCESSING','QUEUED') AND UpdatedAt < DATEADD(second, -@staleSeconds, SYSUTCDATETIME()))
            OR
            (Status = 'FAILED' AND UpdatedAt < DATEADD(second, -@failedSeconds, SYSUTCDATETIME()))
        )
    ORDER BY UpdatedAt
)
UPDATE stale
SET
    Status = 'FAILED',
    LastError = 'Recovery watchdog re-queued stale document',
    UpdatedAt = SYSUTCDATETIME()
OUTPUT
    inserted.DocumentId,
    inserted.BatchId,
    inserted.BlobName,
    inserted.BlobVersionId,
    inserted.Sha256,
    inserted.AttemptCount;", connection, transaction);

            command.Parameters.AddWithValue("@staleSeconds", staleSeconds);
            command.Parameters.AddWithValue("@failedSeconds", Math.Max(staleSeconds * 2, 180));

            await using var reader = await command.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                candidates.Add(new RecoveryCandidate(
                    reader.GetGuid(0),
                    reader.GetGuid(1),
                    reader.GetString(2),
                    reader.IsDBNull(3) ? null : reader.GetString(3),
                    reader.GetString(4),
                    reader.GetInt32(5)));
            }

            await transaction.CommitAsync();
        }

        if (candidates.Count == 0)
            return;

        await using var serviceBus = new ServiceBusClient(Settings.ServiceBus);
        var sender = serviceBus.CreateSender(Settings.Queue);

        foreach (var candidate in candidates)
        {
            try
            {
                var messageBody = new DocumentMessage(
                    candidate.BatchId,
                    candidate.DocumentId,
                    candidate.BlobName,
                    candidate.BlobVersionId,
                    candidate.Sha256);

                var message = new ServiceBusMessage(JsonSerializer.Serialize(messageBody, JsonOptions))
                {
                    // Recovery must not reuse the original deterministic MessageId because
                    // Service Bus duplicate detection would discard it inside the 1-hour window.
                    MessageId = $"{candidate.DocumentId}:recovery:{Guid.NewGuid():N}",
                    Subject = "document-recovery"
                };

                await sender.SendMessageAsync(message);

                await using var connection = new SqlConnection(Settings.Sql);
                await connection.OpenAsync();
                await using var command = new SqlCommand(@"
UPDATE dbo.DocumentUpload
SET LastError='Recovery watchdog re-queued stale document', UpdatedAt=SYSUTCDATETIME()
WHERE DocumentId=@documentId;
INSERT dbo.AuditEvent(DocumentId,EventType,Details)
VALUES(@documentId,'RECOVERY_REQUEUED',@details);", connection);
                command.Parameters.AddWithValue("@documentId", candidate.DocumentId);
                command.Parameters.AddWithValue("@details", $"Recovered stale document after attempt {candidate.AttemptCount}; recovery message uses unique MessageId.");
                await command.ExecuteNonQueryAsync();

                log.LogWarning(
                    "Re-queued stale document {DocumentId} from batch {BatchId} after attempt {AttemptCount}",
                    candidate.DocumentId,
                    candidate.BatchId,
                    candidate.AttemptCount);
            }
            catch (Exception ex)
            {
                // The document remains FAILED, so a later watchdog cycle can try again.
                log.LogError(ex, "Failed to re-queue stale document {DocumentId}", candidate.DocumentId);
            }
        }
    }

    private sealed record RecoveryCandidate(
        Guid DocumentId,
        Guid BatchId,
        string BlobName,
        string? BlobVersionId,
        string Sha256,
        int AttemptCount);
}
