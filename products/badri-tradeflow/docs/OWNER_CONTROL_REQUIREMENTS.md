# Owner Control: Stock, Reordering, Confirmations, Receivables and Payables
**Badri TradeFlow product extension | November 2026 | design requirement, not yet implemented in the runtime**

## Origin
Product owner provided seven photos: (1) salesman beat bill-value sheet; (2) credit/debit/currency-count sheet; (3) Vijay/Bommidi retailer collection receipt; (4) PA Traders GST purchase invoice; (5) Himalaya Wellness invoice page with batch/expiry and carton detail; (6) Maha Enterprises invoice ME0398; (7) Maha Enterprises multipage purchase invoice MHHIN009140.

The documents are historical transactional evidence, **not** a complete stock ledger, complete customer-AR ledger, supplier-AP ledger, or proof of payment. All cash and invoice numbers need human source review.

## Owner answer panel
- **Saleable on hand**, **reserved**, **available to promise**, **in transit**, **damaged/quarantined**, **expiring**, **stock valuation**.
- **Reorder now**, **reorder soon**, suggested purchase quantity, supplier preference, pack conversion, next expected delivery date.
- **Retailer receivables**: total, due today, overdue buckets (0–7/8–30/31–60/60+), by beat, salesman, invoice, dispute and pending payment verification.
- **Supplier payables**: total, due today/week, overdue buckets, pending matching/approval, purchase returns/credits, by supplier.
- **Cash vs bank**: recorded collections by payment rail, confirmed vs pending UPI, cash in salesperson custody vs handed to owner vs bank deposit.
- **Cash visibility**: 7-/30-day cash forecast, supplier due minus expected collectible, shortages; not equivalent to profit.

### Reordering
Catalog product has preferred supplier, standard purchase pack, lead time in days, rolling daily demand, safety-stock units, target-days cover and minimum order/lot size. Compute per SKU / product + warehouse:
- OnHandSaleable = OpeningPhysicalCount + GRN + saleable returns + verified inward adjustments - dispatch - damaged/writeoff - transfer-out; expired/quarantined excluded.
- AvailableToPromise = OnHandSaleable - allocated/reserved quantity.
- ReorderPoint = ForecastDailyDemand × SupplierLeadTimeDays + SafetyStock.
- Reorder when NetInventoryPosition <= ReorderPoint.
- NetInventoryPosition = AvailableToPromise + confirmedOpenPurchaseOrders (net of canceled/short-shipped units).
- ProposedBuyUnits = max(0, ForecastDailyDemand × TargetCoverDays + SafetyStock - NetInventoryPosition), rounded to pack-size and supplier MOQ, with expiry / dead-stock warning.
- Suggested order is a draft, requiring owner approval. No automated vendor buying.

**Example is synthetic:** 12 units/day × 4-day lead time + 20 safety = 68-unit reorder point. If only 40 units are saleable and no confirmed inbound, show **reorder recommended**, not "must buy" without checking seasons, credit and expiry. For 10-day target cover, suggested quantity before pack rounding is 100 units.

### Retailer acknowledgment — distinct documents
1. **Order acknowledgment**: order reference and quantities agreed, price, promised date, status; retailer may accept/change/reject through secure short-lived link, WhatsApp deep link with consent, SMS, or an assisted salesman action with audit.
2. **Delivery POD**: accepted/refused SKU quantities, receiver name, optional signature/photo/OTP, time, invoice reference and retailer dispute window. POD is not a payment receipt.
3. **Payment receipt**: unique receipt ID, method, amount, applied invoice(s), before/after outstanding, bank reference, payer acknowledgment, verification state. Cash receipt is acknowledged when salesperson accepts the cash, while salesman-to-owner settlement is a separate internal control. UPI/bank should not be marked verified solely on an uploaded screenshot; bank/gateway confirmation or authorized reconciliation is required.
4. Automated delivery of PDF/WhatsApp/SMS only after explicit consent and channel setup; there must be printable and low-connectivity alternatives.

### Customer receivables (AR)
Create immutable invoices, receipts, payment allocations, credit notes, opening balances, adjustments, due dates and disputes. Customer AR = confirmed posted sales invoices + approved opening receivables - cleared allocated payments - approved credits/writeoffs, subject to reversals and aging. Allocate one collection across multiple invoices explicitly. Do not net salesperson cash discrepancies against customer AR. Provide retailer statement, beat and salesman collection plan, overdue and partial payment reports. Preserve idempotency on payment creation and webhook confirmations.

### Supplier payables (AP)
Create supplier bill, invoice/GRN/PO three-way-match state, purchase return credit, opening payable, payment advice, scheduled/actual payment, part payment, due date and disputes. AP = approved bill liabilities + opening payables - cleared supplier payments - approved credit/debit adjustments. Avoid double booking across invoice and GRN. Hold mismatched value/quantity until supplier resolution. Store external bank reference and attachment, prevent duplicate payments, support discounts/TDS only where verified and applicable.

**Source invoices are NOT outstanding balances:** PA Traders invoice A000770 dated 02-Nov-2026 displays gross ₹57,977 and is labelled CREDIT Bill. Maha Enterprises invoice ME0398 dated 07-Nov-2026 displays net ₹26,059. Payment entries, offsets, and opening balances are missing; invoice values must not be labelled "due now." The other Maha invoice page is not a full invoice total; the Himalaya page says continued on page 2.

### Sample-derived pitfalls
- Packing units, unit MRP, supplier PTR/rate, manufacturer batches, manufacturing/expiry and promotional free units cannot be collapsed into a single quantity/rate.
- Different GST tax rates, schemes, price reductions and round-off are line/accounting data, not stock.
- "Entry done" handwriting proves entry intent, not cleared money or actual GRN.
- Retailer sheets include beat, bill numbers, signed/marked entries, credits, debits, and denomination totals; typed categories and reconciliation are necessary.
- Vijay/Bommidi handwritten ₹34,178 subtotal and ₹34,172 cash count suggest an apparent ₹6 gap **if** both are for the same cash scope; the sheet also has additional entries and ambiguous handwriting, so accountant review must precede shortage posting.
- The second cash sheet's handwritten credit, debit and others must not be combined indiscriminately.

## UI specification for Owner Control (new primary navigation)
Tabs: **Today**, **Stock**, **Purchase Suggestions**, **Collect From Retailers**, **Pay Suppliers**, **Confirmations**, **Exceptions**.
1. **Stock**: SKU/warehouse/batch, onhand, reserved, available, inbound, saleable, expiry, valuation; actions "Count Stock", "Open Batch", "Create Purchase Draft".
2. **Purchase Suggestions**: priority, average sell-through, available, inbound, days cover, reorder point, suggested buy, pack count, supplier, landed cost and override reason.
3. **Collect From Retailers**: retailer, beat, invoice, aging, promised date, last verified payment, quick statement/WhatsApp share (opt-in).
4. **Pay Suppliers**: supplier, invoice, GRN variance, payment terms, due date, payable, scheduled, paid, credit notes, bank reference, maker-checker.
5. **Confirmations**: retailer "Accepted order", "Delivered", "Payment acknowledged/verified", "Partially paid", "Disputed" independently.
6. **Owner Daily Closing**: today's expected cash, physical cash, gap, UPI pending, cleared bank payments, supplier payments and cashflow forecast.

## Tests to add before beta
- Opening stock + GRN, dispatch, returns, damage and movement journal exactly reconcile available inventory by batch.
- Reservation reduces available, not on-hand; batch expiry stops picking; no stock changes from purchase approval alone.
- Reorder reflects demand/lead time, confirmed inbound, reorder pack and vendor MOQ; manual override is audited.
- Retailer can confirm only their own order; replayed OTP/short links fail safely.
- POD and payment receipt are separate; quantity discrepancy creates return/delivery exception.
- Bank/UPI pending screenshot cannot settle invoice as verified; cash receipt is distinct from owner cash handover.
- Retailer AR ages by due date and handles split, partial, disputed and overpayments without double counting.
- Supplier AP ages by due date, excludes unapproved bill mismatches, applies GRN and credit notes, handles part payment and idempotent remittance.
- Trial balance/AR/AP/stock quantities reconcile to entries; tenant A data never appears for tenant B.
- Alerts and KPI totals are grounded in posted evidence, never generated from synthetic demo fixtures in a live tenant.

## Current pilot vs future
Current FastAPI demo supports batch stock, commercial invoice approval/GRN separation, order dispatch/POD by sales staff, simple retailer outstanding and recorded cash reconciliation. It **does not yet** provide true stocktake/opening-balance reconciliation, a stock movement journal, automated reordering, retailer self-service / OTP acknowledgments, bank-verified receipts, invoice-aging AR or supplier AP/payment processing. These extensions require development, testing and an updated production data model.
