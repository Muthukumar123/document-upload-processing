# Product scope and acceptance conditions

## Persona map
| Role | Visibility | Permitted actions |
| --- | --- | --- |
| SaaS platform administrator | Subscription metadata; no default commercial data access | Tenant onboarding, plan, billing and operations |
| Proprietor / owner | Own tenant only | Commercial approvals, overrides with audit, financial reports |
| Purchase / accounts clerk | Own tenant purchase and payments | Capture invoice, reconcile, request approval, collection |
| Warehouse | Own tenant products, stock and deliveries | Count GRN, dispatch and confirm stock movement |
| Salesman | Assigned beats and retailers | Orders, proof of delivery, collections |
| Retailer (optional phase 2) | Only its own orders and invoices | View ledger, raise order, confirm receipt |

## Document-to-ledger controls
- Each invoice upload has immutable original, SHA-256, uploader and timestamp. Attempted duplicates are surfaced before acceptance.
- Extracted fields are a draft until an authorized human reviews source document against supplier, invoice number and tax details.
- Invoice accepted does **not** equal goods received. Stock addition needs physically counted goods receipt note (GRN). Record discrepancies separately.
- Received free units are stock units without being counted as paid purchase quantities.
- Store batch, expiry, HSN, pack-size conversion, PTR, MRP, GST and scheme discounts per line. Multipage invoices are one logical document.
- Purchase returns need credit note and reversible ledger transactions; never delete posted financial history.

## Sales delivery-to-collection controls
- Salesman assigned to beat (Bommidi, GK Road, Dharmapuri); owner can change only with audit.
- Order created -> reserved -> dispatched -> delivered (proof) or returned/failed.
- Dispatch must not exceed available stock; prefer FEFO by batch expiry.
- Retailer credit cannot exceed configured credit limit without owner approval.
- Payment applies to invoice receivable, supports partial amounts and unique idempotency keys.
- Collection modes cash/UPI/bank/cheque. UPI pending is **not** considered cleared until verified.
- Salesman submits denomination breakdown; expected versus physically counted is calculated; shortage/surplus requires resolution, not silent approval.
- Owner approves day closing after cash handover and bank/UPI reconciliation.

## Data model
Tenant, User, Role, Beat, Retailer, Supplier, Product, ProductAlias, PackConversion, PurchaseInvoice, PurchaseLine, OriginalDocument, ExtractionDraft, GRN, StockBatch, StockMovement, SalesOrder, SalesLine, Delivery, POD, Collection, PaymentAllocation, Settlement, SettlementLine, Return, CreditNote, AuditEvent, Plan, TenantSubscription, BillingEvent.

## Critical acceptance tests
1. Tenant A cannot query Tenant B even with guessed ID, including media files and search.
2. Re-uploaded supplier invoice has explicit duplicate warning and cannot post stock twice.
3. Invoice approval leaves inventory unchanged until GRN accepted.
4. GRN overreceipt raises exception, requires authorized resolution, no silent paid-free merging.
5. Dispatch blocks insufficient/non-saleable/expired batches.
6. Delivery without acknowledgment cannot close as delivered.
7. Duplicate collection request returns same receipt, not two ledger entries.
8. Partial payments accurately reduce receivables without exceeding invoice total.
9. Mismatched cash denomination sheet cannot close unreviewed.
10. All financial and inventory changes have immutable audit and idempotency references.

## Architecture pathway
Responsive React UI -> authenticated API -> tenant authorization -> invoice ingestion/validation and core commerce services -> transactional data store + event/outbox + object storage. Azure document processing in the host repository is reusable at the ingestion boundary. Avoid assuming the host repository's Azure SQL case schema is a sufficient inventory ledger. Consider Azure SQL/PostgreSQL for relational stock/finance; evaluate Cosmos DB only with explicit partition/transaction modelling and measured cost. Prefer a PaaS-first cost-conscious rollout.

## Scope status
The GitHub branch now includes the running local FastAPI + SQLite pilot in app/, with six passing workflow tests in tests/. The ZIP supplied in this conversation is a separately downloadable copy of this pilot. The demo.html file is a distinct synthetic browser-only demonstration for quick product walkthrough.

Excluded from pilot: genuine OCR/IRN/GSTN verification, offline device sync, bank connectors, official accounting compliance attestation, secure user provisioning, payment gateway billing, DR/SLO/observability and production Azure hardening.
