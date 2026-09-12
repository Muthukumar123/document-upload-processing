targetScope = 'resourceGroup'
param location string = resourceGroup().location
param envName string = 'dev'
@secure() param sqlAdminPassword string
param sqlAdminLogin string = 'docadmin'
param allowedWebOrigins array = ['http://localhost:5173']

var suffix = uniqueString(subscription().id, resourceGroup().id, envName)
var storageName = 'du${take(suffix,18)}'
var functionName = 'document-upload-${envName}-${take(suffix,8)}'
var sbName = 'document-upload-${envName}-${take(suffix,8)}'
var sqlName = 'document-upload-${envName}-${take(suffix,8)}'
var webOrigin = storage.properties.primaryEndpoints.web
var corsOrigins = union(allowedWebOrigins, [webOrigin])

resource log 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'document-upload-${envName}-log'
  location: location
  properties: { retentionInDays: 30 }
}
resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'document-upload-${envName}-appi'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: log.id }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, supportsHttpsTrafficOnly: true, accessTier: 'Hot' }
}
resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    staticWebsite: { enabled: true, indexDocument: 'index.html', error404Document: 'index.html' }
    cors: { corsRules: [{ allowedOrigins: corsOrigins, allowedMethods: ['PUT','GET','OPTIONS'], allowedHeaders: ['*'], exposedHeaders: ['ETag','x-ms-version-id'], maxAgeInSeconds: 3600 }] }
  }
}
resource incoming 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'incoming'
  properties: { publicAccess: 'None' }
}
resource sb 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: sbName
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
  properties: { minimumTlsVersion: '1.2', publicNetworkAccess: 'Enabled' }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: sb
  name: 'document-processing'
  properties: {
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT1H'
    lockDuration: 'PT5M'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P7D'
  }
}
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlName
  location: location
  properties: { administratorLogin: sqlAdminLogin, administratorLoginPassword: sqlAdminPassword, minimalTlsVersion: '1.2', publicNetworkAccess: 'Enabled' }
}
resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: 'documentupload'
  location: location
  sku: { name: 'S0', tier: 'Standard' }
  properties: { zoneRedundant: false }
}
resource azureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'document-upload-${envName}-plan'
  location: location
  kind: 'linux'
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}
resource fn 'Microsoft.Web/sites@2023-12-01' = {
  name: functionName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|22'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      cors: { allowedOrigins: corsOrigins, supportCredentials: false }
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'node' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~22' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${listKeys(storage.id,storage.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}' }
        { name: 'SERVICE_BUS_CONNECTION', value: listKeys('${sb.id}/AuthorizationRules/RootManageSharedAccessKey','2024-01-01').primaryConnectionString }
        { name: 'SERVICE_BUS_QUEUE', value: queue.name }
        { name: 'UPLOAD_CONTAINER', value: incoming.name }
        { name: 'SQL_CONNECTION_STRING', value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Initial Catalog=${sqlDb.name};Persist Security Info=False;User ID=${sqlAdminLogin};Password=${sqlAdminPassword};MultipleActiveResultSets=False;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;Max Pool Size=15;' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
        { name: 'VALIDATION_API_URL', value: 'https://${functionName}.azurewebsites.net/api/mock/validate' }
      ]
    }
  }
}

output functionAppName string = fn.name
output functionBaseUrl string = 'https://${fn.properties.defaultHostName}/api'
output storageAccountName string = storage.name
output staticWebUrl string = webOrigin
output sqlServerName string = sqlServer.name
output sqlDatabaseName string = sqlDb.name
output serviceBusNamespace string = sb.name
