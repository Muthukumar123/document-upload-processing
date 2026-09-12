import { ServiceBusClient } from "@azure/service-bus";
import { config } from "../config.js";

const client = new ServiceBusClient(config.SERVICE_BUS_CONNECTION);
const sender = client.createSender(config.SERVICE_BUS_QUEUE);

export interface DocumentMessage {
  batchId: string;
  documentId: string;
  blobName: string;
  versionId?: string;
  sha256: string;
}

export async function enqueueDocument(message: DocumentMessage): Promise<void> {
  await sender.sendMessages({
    body: message,
    messageId: message.documentId,
    correlationId: message.batchId,
    contentType: "application/json",
    applicationProperties: { schemaVersion: "1" }
  });
}
