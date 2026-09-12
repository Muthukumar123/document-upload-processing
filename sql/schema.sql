IF OBJECT_ID('dbo.UploadBatch') IS NULL CREATE TABLE dbo.UploadBatch(
 BatchId uniqueidentifier NOT NULL PRIMARY KEY, TotalDocuments int NOT NULL, Status varchar(32) NOT NULL,
 CreatedAt datetime2 NOT NULL CONSTRAINT DF_UploadBatch_Created DEFAULT SYSUTCDATETIME(), UpdatedAt datetime2 NOT NULL CONSTRAINT DF_UploadBatch_Updated DEFAULT SYSUTCDATETIME());
IF OBJECT_ID('dbo.DocumentUpload') IS NULL CREATE TABLE dbo.DocumentUpload(
 DocumentId uniqueidentifier NOT NULL PRIMARY KEY, BatchId uniqueidentifier NOT NULL, FileName nvarchar(260) NOT NULL, BlobName nvarchar(1024) NOT NULL,
 BlobVersionId nvarchar(128) NULL, FileSize bigint NOT NULL, Sha256 char(64) NULL, Status varchar(32) NOT NULL, AttemptCount int NOT NULL CONSTRAINT DF_Doc_Attempt DEFAULT 0,
 LastError nvarchar(2000) NULL, CreatedAt datetime2 NOT NULL CONSTRAINT DF_Doc_Created DEFAULT SYSUTCDATETIME(), UpdatedAt datetime2 NOT NULL CONSTRAINT DF_Doc_Updated DEFAULT SYSUTCDATETIME(), CompletedAt datetime2 NULL,
 CONSTRAINT FK_Doc_Batch FOREIGN KEY(BatchId) REFERENCES dbo.UploadBatch(BatchId));
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE name='IX_DocumentUpload_Batch_Status') CREATE INDEX IX_DocumentUpload_Batch_Status ON dbo.DocumentUpload(BatchId,Status);
IF OBJECT_ID('dbo.[Case]') IS NULL CREATE TABLE dbo.[Case](
 CaseId uniqueidentifier NOT NULL PRIMARY KEY, DocumentId uniqueidentifier NOT NULL, RowNumber int NOT NULL, ExternalKey nvarchar(128) NOT NULL, PayloadJson nvarchar(max) NOT NULL,
 CompareMatch bit NOT NULL, ValidationJson nvarchar(max) NOT NULL, Status varchar(32) NOT NULL, CreatedAt datetime2 NOT NULL CONSTRAINT DF_Case_Created DEFAULT SYSUTCDATETIME(), UpdatedAt datetime2 NOT NULL CONSTRAINT DF_Case_Updated DEFAULT SYSUTCDATETIME(),
 CONSTRAINT UQ_Case_Document_Row UNIQUE(DocumentId,RowNumber), CONSTRAINT FK_Case_Document FOREIGN KEY(DocumentId) REFERENCES dbo.DocumentUpload(DocumentId));
IF OBJECT_ID('dbo.DocumentCaseLink') IS NULL CREATE TABLE dbo.DocumentCaseLink(DocumentId uniqueidentifier NOT NULL, CaseId uniqueidentifier NOT NULL, CreatedAt datetime2 NOT NULL CONSTRAINT DF_Link_Created DEFAULT SYSUTCDATETIME(), CONSTRAINT PK_DocumentCase PRIMARY KEY(DocumentId,CaseId));
IF OBJECT_ID('dbo.AuditEvent') IS NULL CREATE TABLE dbo.AuditEvent(AuditId bigint IDENTITY PRIMARY KEY, DocumentId uniqueidentifier NOT NULL, EventType varchar(64) NOT NULL, Details nvarchar(2000) NULL, CreatedAt datetime2 NOT NULL CONSTRAINT DF_Audit_Created DEFAULT SYSUTCDATETIME());
IF OBJECT_ID('dbo.ReferenceData') IS NULL CREATE TABLE dbo.ReferenceData(ExternalKey nvarchar(128) NOT NULL PRIMARY KEY, ExpectedValue nvarchar(256) NOT NULL, UpdatedAt datetime2 NOT NULL CONSTRAINT DF_Ref_Updated DEFAULT SYSUTCDATETIME());
