import { BlobServiceClient, BlobSASPermissions, SASProtocol, StorageSharedKeyCredential, generateBlobSASQueryParameters } from "@azure/storage-blob";
import { config } from "../config.js";

function parseConnectionString(cs: string) {
  const entries = Object.fromEntries(cs.split(";").filter(Boolean).map(x => x.split("=", 2)));
  if (!entries.AccountName || !entries.AccountKey) throw new Error("Storage connection string must include AccountName and AccountKey");
  return { accountName: entries.AccountName, accountKey: entries.AccountKey };
}

export const blobService = BlobServiceClient.fromConnectionString(config.AzureWebJobsStorage);

export function createUploadSas(blobName: string, minutes = 20): string {
  const { accountName, accountKey } = parseConnectionString(config.AzureWebJobsStorage);
  const credential = new StorageSharedKeyCredential(accountName, accountKey);
  const startsOn = new Date(Date.now() - 60_000);
  const expiresOn = new Date(Date.now() + minutes * 60_000);
  const sas = generateBlobSASQueryParameters({
    containerName: config.UPLOAD_CONTAINER,
    blobName,
    permissions: BlobSASPermissions.parse("cw"),
    startsOn,
    expiresOn,
    protocol: SASProtocol.Https
  }, credential).toString();
  return `${blobService.getContainerClient(config.UPLOAD_CONTAINER).getBlockBlobClient(blobName).url}?${sas}`;
}
