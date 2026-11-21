# Badri TradeFlow — Trader SaaS pilot

**Status:** Isolated product prototype. Not a production deployment.

This product addresses real Indian FMCG distributor workflows found in the Badri Traders paper samples from Dharmapuri: supplier GST invoices, beat-based deliveries, retailer collections, cash denominations and day-end settlement.

## Explore

Open [demo.html](demo.html) in a browser to review the self-contained, interactive **visual workflow demonstrator**. It stores only synthetic data in your browser and does not call any service.

The **integrated, locally tested FastAPI + SQLite MVP** is now included in this branch under `app/main.py` and `app/static/`, with six transaction tests in `tests/test_workflows.py`. The separately supplied ZIP contains the same tested pilot. `demo.html` is an optional no-server walkthrough; the real pilot's browser UI runs with its FastAPI server.

## Run integrated local pilot

```bash
cd products/badri-tradeflow
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
DEMO_MODE=1 uvicorn app.main:app --host 127.0.0.1 --port 8000
# Visit http://127.0.0.1:8000
# Run tests separately: python -m pytest -q
```

**Never run `DEMO_MODE=1` on a public network.** It enables a local demo user selector only, with no trusted production identity. The integrated app includes manually reviewed invoice uploads and simulated extraction fixtures; it has no OCR service.

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

This branch is a functional local pilot and **does not have** Azure multi-tenant deployment, OIDC authentication, secure customer onboarding, actual PDF OCR, IRP/e-invoice validation, WhatsApp delivery, offline synchronization, live payment confirmation, payment gateway subscription collection, or production-grade financial accounting. Never enter real invoices, GST identifiers or customer details into this mock.

Recommended build path: adapt the existing repository's Azure document upload components for invoice ingestion and validation. Implement a separate product domain and tenant data boundary; do not reuse unrelated document-processing case records. Security testing, data retention, backups and operational controls are gates before a public launch.

See [docs/PRODUCT_SCOPE.md](docs/PRODUCT_SCOPE.md) for detailed end-to-end scope and gaps.
