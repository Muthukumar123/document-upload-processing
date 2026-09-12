import { z } from "zod";

const schema = z.object({
  AzureWebJobsStorage: z.string().min(1),
  SERVICE_BUS_CONNECTION: z.string().min(1),
  SERVICE_BUS_QUEUE: z.string().default("document-processing"),
  SQL_CONNECTION_STRING: z.string().min(1),
  UPLOAD_CONTAINER: z.string().default("incoming"),
  VALIDATION_API_URL: z.string().url(),
  MAX_FILE_BYTES: z.coerce.number().default(55 * 1024 * 1024),
  RECONCILE_AFTER_MINUTES: z.coerce.number().default(5)
});

export const config = schema.parse(process.env);
