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

        // Service Bus owns normal QUEUED delivery and FAILED redelivery. Recovery is only for
        // genuinely orphaned PROCESSING records after the normal lock/auto-renewal window.
        var staleSeconds = int.TryParse(Environment.GetEnvironmentVariable("RECOVERY_STALE_SECONDS"), out var configuredStale)
            ? Math.Max(configuredStale, 660)
            : 720;
        var maxAttempts = int.TryParse(Environment.GetEnvironmentVariable("RECOVERY_MAX_ATTEMPTS"), out var configuredAttempts)
            ? Math.Clamp(configuredAttempts, 1, 20)
            : 5;

        var candidates = new List<RecoveryCandidate>();

        await using (var connection = new SqlConnection(Settings.Sql))
        {
            await connection.OpenAsync();

            await using var command = new SqlCommand(@"
SELECT TOP (20)
    DocumentId,
    BatchId,
    BlobName,
    BlobVersionId,
    Sha256,
    AttemptCount
FROM dbo.DocumentUpload WITH (READPAST)
WHERE
    Status = 'PROCESSING'
    AND Sha256 IS NOT NULL
    AND AttemptCount < @maxAttempts
    AND UpdatedAt < DATEADD(second, -@staleSeconds, SYSUTCDATETIME())
ORDER BY UpdatedAt;", connection);
            command.Parameters.AddWithValue("@maxAttempts", maxAttempts);
            command.Parameters.AddWithValue("@staleSeconds", staleSeconds);

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
        }

        if (candidates.Count > 0)
        {
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

                    // Deterministic per recovery attempt: different from the original document MessageId,
                    // but repeated watchdog cycles for the same attempt are suppressed by Service Bus
                    // duplicate detection if the send succeeds and the SQL status update does not.
                    var recoveryMessageId = $"{candidate.DocumentId}:recovery:{candidate.AttemptCount}";
                    var message = new ServiceBusMessage(JsonSerializer.Serialize(messageBody, JsonOptions))
                    {
                        MessageId = recoveryMessageId,
                        Subject = "document-recovery"
                    };

                    await sender.SendMessageAsync(message);

                    await using var connection = new SqlConnection(Settings.Sql);
                    await connection.OpenAsync();
                    await using var command = new SqlCommand(@"
UPDATE dbo.DocumentUpload
SET Status='QUEUED',
    LastError='Recovery watchdog re-queued orphaned PROCESSING document',
    UpdatedAt=SYSUTCDATETIME()
WHERE DocumentId=@documentId
  AND Status='PROCESSING'
  AND AttemptCount=@attemptCount;
IF @@ROWCOUNT = 1
BEGIN
    INSERT dbo.AuditEvent(DocumentId,EventType,Details)
    VALUES(@documentId,'RECOVERY_REQUEUED',@details);
END", connection);
                    command.Parameters.AddWithValue("@documentId", candidate.DocumentId);
                    command.Parameters.AddWithValue("@attemptCount", candidate.AttemptCount);
                    command.Parameters.AddWithValue("@details", $"Recovered orphaned PROCESSING document after attempt {candidate.AttemptCount}; MessageId={recoveryMessageId}.");
                    var changed = await command.ExecuteNonQueryAsync();

                    log.LogWarning(
                        "Recovery sent for stale document {DocumentId} from batch {BatchId} after attempt {AttemptCount}; SQL statements affected {AffectedRows}",
                        candidate.DocumentId,
                        candidate.BatchId,
                        candidate.AttemptCount,
                        changed);
                }
                catch (Exception ex)
                {
                    // Keep PROCESSING unchanged. Because UpdatedAt is not touched, the next watchdog
                    // cycle can safely retry the same deterministic recovery MessageId.
                    log.LogError(ex, "Failed to re-queue stale PROCESSING document {DocumentId}", candidate.DocumentId);
                }
            }
        }

        // Do not manufacture additional Service Bus retries after the configured recovery ceiling.
        // Leave a terminal FAILED state for reconciliation/operations instead of an infinite loop.
        await using (var connection = new SqlConnection(Settings.Sql))
        {
            await connection.OpenAsync();
            await using var command = new SqlCommand(@"
UPDATE dbo.DocumentUpload
SET Status='FAILED',
    LastError='Recovery attempt ceiling reached for orphaned PROCESSING document',
    UpdatedAt=SYSUTCDATETIME()
OUTPUT inserted.DocumentId
WHERE Status='PROCESSING'
  AND AttemptCount >= @maxAttempts
  AND UpdatedAt < DATEADD(second, -@staleSeconds, SYSUTCDATETIME());", connection);
            command.Parameters.AddWithValue("@maxAttempts", maxAttempts);
            command.Parameters.AddWithValue("@staleSeconds", staleSeconds);

            var terminalIds = new List<Guid>();
            await using var reader = await command.ExecuteReaderAsync();
            while (await reader.ReadAsync()) terminalIds.Add(reader.GetGuid(0));

            foreach (var documentId in terminalIds)
            {
                log.LogError(
                    "Recovery ceiling reached for document {DocumentId}; marked FAILED after {MaxAttempts} processing attempts",
                    documentId,
                    maxAttempts);
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
