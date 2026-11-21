# Badri TradeFlow — Trader SaaS pilot

**Status:** Isolated product prototype. Not a production deployment.

This product addresses real Indian FMCG distributor workflows found in the Badri Traders paper samples from Dharmapuri: supplier GST invoices, beat-based deliveries, retailer collections, cash denominations and day-end settlement.

## Explore

Open [demo.html](demo.html) in a browser to review the self-contained, interactive **visual workflow demonstrator**. It stores only synthetic data in your browser and does not call any service.

The independently tested **FastAPI + SQLite** working MVP is delivered separately to the product owner as a downloadable ZIP in the ChatGPT project. It includes the actual backend endpoints, role-sensitive screens and six passing transaction tests. To bring the tested project into this branch, copy the ZIP contents under this directory, replacing this browser-only demo with its integrated app/static frontend.

## Business workflow

1. **Invoice upload:** accept PDF/JPEG/PNG; detect duplicated document hash and supplier/invoice number; require manual review of supplier, HSN, GST, discounts, units, free quantities, batch and expiry.
2. **Approval:** approve commercial invoice without changing inventory.
3. **Goods receipt:** physically count delivered units; post paid/free stock only once through approved GRN; exceptions to owner.
4. **Delivery:** order retailer by beat; reserve stock, dispatch in expiry-first order, obtain proof of delivery.
5. **Collection:** record cash, UPI, bank or cheque against delivered invoice, including partial collections; prevent duplicate payment requests.
6. **Settlement:** reconcile denomination count and cash receipts; exceptions cannot be approved until resolved.
7. **Tenant and role control:** isolate subscribing traders; restrict staff actions and preserve an audit log.

## Product plans — proposals, not market-verified prices

| Tier | Monthly INR | Annual INR | Setup INR | Pilot users |
| --- | ---: | ---: | ---: | ---: |
| Starter | 999 | 9,990 | 2,999 | 2 |
| Growth | 1,999 | 19,990 | 4,999 | 5 |
| Pro | 3,999 | 39,990 | 9,999 | 15 |

Charges exclude applicable GST; extra OCR page processing should have fair-use quotas and transparent billing. Proposals need validation against actual cloud and support spending.

## Production requirements

This branch is a demo and **does not have** Azure multi-tenant deployment, OIDC authentication, secure customer onboarding, actual PDF OCR, IRP/e-invoice validation, WhatsApp delivery, offline synchronization, live payment confirmation, payment gateway subscription collection, or production-grade financial accounting. Never enter real invoices, GST identifiers or customer details into this mock.

Recommended build path: adapt the existing repository's Azure document upload components for invoice ingestion and validation. Implement a separate product domain and tenant data boundary; do not reuse unrelated document-processing case records. Security testing, data retention, backups and operational controls are gates before a public launch.

See [docs/PRODUCT_SCOPE.md](docs/PRODUCT_SCOPE.md) for detailed end-to-end scope and gaps.
