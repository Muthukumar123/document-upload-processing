using Azure.Messaging.ServiceBus;
using Microsoft.Azure.Functions.Worker;

namespace DocumentUpload.Functions;

public static class ServiceBusMessageActionsExtensions
{
    public static Task DeadLetterMessageAsync(
        this ServiceBusMessageActions actions,
        ServiceBusReceivedMessage message,
        string deadLetterReason,
        string deadLetterErrorDescription,
        CancellationToken cancellationToken = default)
    {
        return actions.DeadLetterMessageAsync(
            message,
            propertiesToModify: null,
            deadLetterReason: deadLetterReason,
            deadLetterErrorDescription: deadLetterErrorDescription,
            cancellationToken: cancellationToken);
    }
}
