# Benchmark Workload Model: Documents, Rows and Cases

This document defines the benchmark terminology used throughout this repository. It is important to distinguish **documents** from **cases** when reading the performance results.

## Key definition

The benchmark uploads **documents**. Each synthetic Excel document contains **153 data rows** and **10 columns**.

For this benchmark, **each non-empty data row becomes one case** in Azure SQL.

Therefore:

```text
Total cases = number of documents x data rows per document
```

For the current standard benchmark:

```text
150 documents
x 153 data rows per document
= 22,950 cases
```

So the statement:

> 150 documents / 22,950 cases

means **150 uploaded Excel files were processed, and those files contained a total of 22,950 row-level case records**. It does **not** mean that 150 cases became 22,950 cases.

## What one benchmark document contains

Each generated workbook contains:

- 1 Excel workbook/document;
- 1 worksheet used by the processor;
- 1 header row;
- 153 data rows;
- 10 columns per data row.

The header row does not become a case. The processor starts from row 2 and treats each non-empty data row as one case-processing unit.

The 10 columns are fields/attributes belonging to that case. They do **not** multiply the case count.

For example:

```text
Document 1
  Row 1 -> Case 1
  Row 2 -> Case 2
  ...
  Row 153 -> Case 153

Document 2
  Row 1 -> Case 154
  ...
  Row 153 -> Case 306
```

Conceptually, each document contributes 153 cases to the benchmark total.

## Benchmark calculations

| Benchmark shape | Calculation | Total cases |
|---|---:|---:|
| 1 document | 1 x 153 | 153 |
| 2 documents | 2 x 153 | 306 |
| 10 documents | 10 x 153 | 1,530 |
| 50 documents | 50 x 153 | 7,650 |
| 3 concurrent batches x 50 documents | 3 x 50 x 153 | 22,950 |
| 150 documents | 150 x 153 | 22,950 |
| 153 documents | 153 x 153 | 23,409 |
| 500 documents, benchmark projection only | 500 x 153 | 76,500 |

## Why the repository reports both documents and cases

The two numbers measure different parts of system capacity:

- **Documents** measure file-level workload: Blob uploads, Service Bus messages, Function invocations, checksum verification and Excel parsing.
- **Cases** measure row-level business workload: reference comparison, validation, SQL persistence, document-case linking and status/audit processing.

This is why the benchmark reports both document throughput and case throughput.

For example, the latest validated result:

```text
150 documents
22,950 cases
42.96 seconds
0 failed documents
0 dead-lettered documents
```

means the complete system processed 150 uploaded files and persisted the expected 22,950 row-level cases successfully.

## Important production interpretation

The **153 rows per document is a synthetic benchmark setting**, not a production rule.

Real documents may contain different numbers of rows. Therefore, production case volume should be calculated from the actual number of non-empty business rows in each uploaded document.

Examples:

```text
150 real documents x 20 rows average   = approximately 3,000 cases
150 real documents x 153 rows average  = approximately 22,950 cases
150 real documents x 500 rows average  = approximately 75,000 cases
```

Actual case creation can also depend on business validation and parsing rules.

## Benchmark terminology used in this repository

When a result says:

```text
PASS: 150 documents / 22950 cases completed in 42.96s
```

interpret it as:

1. 150 Excel documents were uploaded and processed.
2. Each synthetic document contained 153 data rows.
3. Each data row represented one case.
4. The expected case count was `150 x 153 = 22,950`.
5. The final SQL state contained exactly 22,950 cases.
6. No document failed and no document was dead-lettered.

This document is the authoritative definition of the synthetic benchmark workload used by the project's performance results.