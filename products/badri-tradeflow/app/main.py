"""Badri TradeFlow: local demonstrator with enforced tenant scoping and approval gates.

IMPORTANT: Identity header is accepted ONLY when DEMO_MODE=1. This code does not
claim production security, invoice OCR, IRP validation or Azure persistence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DB = Path(os.getenv("BADRI_DB_PATH", str(ROOT / "data" / "badri_demo.sqlite3")))
UPLOADS = Path(os.getenv("BADRI_UPLOAD_DIR", str(ROOT / "data" / "uploads")))
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"

app = FastAPI(title="Badri TradeFlow — Demo API", version="0.3.0", description="Functional local pilot; not production-ready")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

@contextmanager
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def rows(c, sql, params=()):
    return [dict(x) for x in c.execute(sql, params).fetchall()]

def row(c, sql, params=()):
    r=c.execute(sql, params).fetchone()
    return dict(r) if r else None

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def audit(c, user, verb, target, detail=""):
    c.execute("INSERT INTO audit_events(tenant_id,actor,action,target,details,created_at) VALUES(?,?,?,?,?,?)",(user["tenant_id"],user["id"],verb,target,detail,now()))

def fail(code, message):
    raise HTTPException(code, message)

def get_user(x_demo_user: str | None = Header(None)):
    if not DEMO_MODE:
        fail(503, "Demo identity disabled. Configure a trusted OIDC authentication adapter for production.")
    if not x_demo_user:
        fail(401, "Choose demo user")
    with db() as c:
        u=row(c,"SELECT * FROM users WHERE id=?",(x_demo_user,))
    if not u:
        fail(401,"Unknown demo identity")
    return u

def permit(u, *roles):
    if u["role"] not in roles:
        fail(403,"Not permitted for your role")

def require_tenant(c, table, object_id, tenant_id):
    r=row(c,f"SELECT * FROM {table} WHERE id=? AND tenant_id=?",(object_id, tenant_id))
    if not r: fail(404,"Record not found")
    return r

SCHEMA="""
CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL, city TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retailers(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT NOT NULL, beat TEXT NOT NULL, outstanding_paise INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT NOT NULL,gstin TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT NOT NULL,category TEXT NOT NULL,hsn TEXT NOT NULL,unit TEXT NOT NULL,price_paise INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,supplier_id TEXT NOT NULL,number TEXT NOT NULL,invoice_date TEXT NOT NULL,amount_paise INTEGER NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,sha256 TEXT,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL,approved_by TEXT,UNIQUE(tenant_id,supplier_id,number));
CREATE TABLE IF NOT EXISTS invoice_lines(id TEXT PRIMARY KEY,invoice_id TEXT NOT NULL,product_id TEXT NOT NULL,qty INTEGER NOT NULL,free_qty INTEGER NOT NULL DEFAULT 0,rate_paise INTEGER NOT NULL,gst_bp INTEGER NOT NULL,batch TEXT NOT NULL,expiry TEXT NOT NULL,received_qty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,product_id TEXT NOT NULL,batch TEXT NOT NULL,expiry TEXT NOT NULL,qty INTEGER NOT NULL,free_qty INTEGER NOT NULL DEFAULT 0, UNIQUE(tenant_id,product_id,batch,expiry));
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,retailer_id TEXT NOT NULL,salesman_id TEXT NOT NULL,beat TEXT NOT NULL,status TEXT NOT NULL,amount_paise INTEGER NOT NULL,created_at TEXT NOT NULL,pod_name TEXT,pod_at TEXT);
CREATE TABLE IF NOT EXISTS order_lines(id TEXT PRIMARY KEY,order_id TEXT NOT NULL,product_id TEXT NOT NULL,qty INTEGER NOT NULL,unit_price_paise INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,order_id TEXT NOT NULL,retailer_id TEXT NOT NULL,salesman_id TEXT NOT NULL,amount_paise INTEGER NOT NULL,method TEXT NOT NULL,ref TEXT NOT NULL,created_at TEXT NOT NULL,settlement_id TEXT,idempotency_key TEXT NOT NULL,UNIQUE(tenant_id,idempotency_key));
CREATE TABLE IF NOT EXISTS settlements(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,salesman_id TEXT NOT NULL,expected_paise INTEGER NOT NULL,counted_paise INTEGER NOT NULL,status TEXT NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL,approved_by TEXT);
CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,tenant_id TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
"""

def seed(c):
    if c.execute("SELECT 1 FROM tenants LIMIT 1").fetchone(): return
    c.executemany("INSERT INTO tenants VALUES (?,?,?,?)",[("bt","Badri Traders","Growth","Dharmapuri"),("dt","Dharmapuri Distribution Demo","Starter","Harur")])
    c.executemany("INSERT INTO users VALUES (?,?,?,?)",[("owner","bt","Owner / Proprietor","owner"),("sales","bt","Vijay / Salesman","sales"),("store","bt","Warehouse Staff","store"),("accounts","bt","Accounts Executive","accounts"),("second_owner","dt","Demo Tenant 2 Owner","owner")])
    c.executemany("INSERT INTO retailers VALUES (?,?,?,?,?)",[("r1","bt","Sri Lakshmi Stores","Bommidi",242500),("r2","bt","Kumaran Maligai","G K Road",180000),("r3","bt","RK Medical","Dharmapuri",0),("r4","bt","Sri Vinayaga Department","Bommidi",77000),("r5","bt","Bala Super Market","G K Road",0),("dr1","dt","Harur General Store","Harur",0)])
    c.executemany("INSERT INTO suppliers VALUES (?,?,?,?)",[("s1","bt","PA Traders","DEMO-GSTIN-PA"),("s2","bt","Himalaya Wellness","DEMO-GSTIN-HW"),("s3","bt","Maha Enterprises","DEMO-GSTIN-MA"),("ds1","dt","Demo Supplier","DEMO-GSTIN-DT")])
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)",[("p1","bt","Dettol Soap 125g","Personal Care","3401","piece",6200),("p2","bt","Himalaya Gentle Baby Shampoo 100ml","Baby Care","3305","bottle",11500),("p3","bt","Kopiko Candy Jar","Confectionery","1704","jar",26900),("p4","bt","Colgate Toothpaste 100g","Oral Care","3306","tube",8500),("p5","bt","Nestle KitKat 38g","Confectionery","1806","piece",3800),("dp1","dt","Demo Pack","General","9999","piece",10000)])
    c.executemany("INSERT INTO stock VALUES (?,?,?,?,?,?,?)",[("st1","bt","p1","D-26-11","2027-07-01",140,10),("st2","bt","p2","H-26-10","2027-09-01",44,2),("st3","bt","p3","K-26-11","2027-02-15",22,0),("st4","bt","p4","C-26-11","2028-05-01",98,0),("st5","bt","p5","N-26-11","2027-04-01",85,5),("dst","dt","dp1","X-2026","2027-10-01",32,0)])
    c.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",("ord-demo","bt","r1","sales","Bommidi","delivered",137600,"2026-11-12T08:10:00+00:00","Shop Owner","2026-11-12T10:02:00+00:00"))
    c.execute("INSERT INTO order_lines VALUES (?,?,?,?,?)",("ol-demo","ord-demo","p1",2,68800))
    c.execute("INSERT INTO collections VALUES (?,?,?,?,?,?,?,?,?,?,?)",("col-demo","bt","ord-demo","r1","sales",137600,"cash","REC-DEMO-01","2026-11-12T14:00:00+00:00",None,"seed-collection-1"))
    c.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",("inv-demo","bt","s2","HW-DEMO-001","2026-11-13",2634800,"needs_review","sample-fixture",None,"owner",now(),None))
    c.execute("INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?,?)",("il-demo","inv-demo","p2",24,2,9700,500,"HW2511","2027-08-01",0))

@app.on_event("startup")
def startup():
    with db() as c:
        c.executescript(SCHEMA)
        seed(c)

@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/api/health")
def health():
    return {"status":"ok","mode":"demonstration" if DEMO_MODE else "disabled","version":"0.3.0"}

@app.get("/api/bootstrap")
def bootstrap(u=Depends(get_user)):
    with db() as c:
        t=row(c,"SELECT * FROM tenants WHERE id=?",(u["tenant_id"],))
    return {"user":u,"tenant":t,"demo":True,"warning":"Local demonstration. No real OCR, GST portal verification, live payments or production identity."}

@app.get("/api/dashboard")
def dashboard(u=Depends(get_user)):
    with db() as c:
        tid=u["tenant_id"]
        def total(q):return c.execute(q,(tid,)).fetchone()[0] or 0
        return {"retailers":total("SELECT COUNT(*) FROM retailers WHERE tenant_id=?"),
           "stock_units":total("SELECT SUM(qty+free_qty) FROM stock WHERE tenant_id=?"),
           "pending_invoices":total("SELECT COUNT(*) FROM invoices WHERE tenant_id=? AND status IN ('needs_review','approved')"),
           "active_orders":total("SELECT COUNT(*) FROM orders WHERE tenant_id=? AND status IN ('booked','dispatched')"),
           "outstanding_paise":total("SELECT SUM(outstanding_paise) FROM retailers WHERE tenant_id=?"),
           "unsettled_cash_paise":total("SELECT SUM(amount_paise) FROM collections WHERE tenant_id=? AND method='cash' AND settlement_id IS NULL"),
           "recent_audit":rows(c,"SELECT action,target,created_at FROM audit_events WHERE tenant_id=? ORDER BY id DESC LIMIT 5",(tid,))}

@app.get("/api/retailers")
def retailers(u=Depends(get_user)):
    with db() as c: return rows(c,"SELECT * FROM retailers WHERE tenant_id=? ORDER BY name",(u["tenant_id"],))

@app.get("/api/products")
def products(u=Depends(get_user)):
    with db() as c: return rows(c,"SELECT * FROM products WHERE tenant_id=? ORDER BY name",(u["tenant_id"],))

@app.get("/api/stock")
def stock(u=Depends(get_user)):
    with db() as c:
        return rows(c,"SELECT s.*,p.name as product,p.category,p.unit FROM stock s JOIN products p ON p.id=s.product_id AND p.tenant_id=s.tenant_id WHERE s.tenant_id=? ORDER BY s.expiry",(u["tenant_id"],))

@app.get("/api/invoices")
def invoices(u=Depends(get_user)):
    permit(u,"owner","store","accounts")
    with db() as c:
        return rows(c,"SELECT i.*,s.name supplier,(SELECT COALESCE(SUM(qty+free_qty),0) FROM invoice_lines WHERE invoice_id=i.id) units FROM invoices i JOIN suppliers s ON s.id=i.supplier_id AND s.tenant_id=i.tenant_id WHERE i.tenant_id=? ORDER BY i.created_at DESC",(u["tenant_id"],))

@app.get("/api/invoices/{invoice_id}")
def invoice_detail(invoice_id:str,u=Depends(get_user)):
    permit(u,"owner","store","accounts")
    with db() as c:
        i=require_tenant(c,"invoices",invoice_id,u["tenant_id"])
        i["lines"]=rows(c,"SELECT l.*,p.name product_name FROM invoice_lines l JOIN products p ON p.id=l.product_id WHERE l.invoice_id=? AND p.tenant_id=?",(invoice_id,u["tenant_id"]))
        return i

class DemoInvoice(BaseModel):
    supplier_id:Literal["s1","s2","s3"]="s2"

@app.post("/api/invoices/create-demo")
def create_demo_invoice(p:DemoInvoice,u=Depends(get_user)):
    permit(u,"owner","store","accounts")
    fixtures={"s1":("p3",10,0,22652,500,"PA-DEMO"),"s2":("p2",24,2,9700,500,"HW-DEMO"),"s3":("p1",36,0,5300,1800,"MA-DEMO")}
    if p.supplier_id not in fixtures: fail(422,"Unsupported supplier")
    with db() as c:
        supp=row(c,"SELECT * FROM suppliers WHERE id=? AND tenant_id=?",(p.supplier_id,u["tenant_id"]))
        if not supp: fail(404,"Supplier unavailable")
        prod,qty,free,rate,gst,batch=fixtures[p.supplier_id]
        i=uuid.uuid4().hex[:10]; invnum="DEMO-"+i.upper()
        c.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(i,u["tenant_id"],supp["id"],invnum,date.today().isoformat(),round(qty*rate*(1+gst/10000)),"needs_review","simulated-sample-extraction",None,u["id"],now(),None))
        c.execute("INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex[:10],i,prod,qty,free,rate,gst,batch+"-26","2027-08-01",0))
        audit(c,u,"invoice.extracted_demo",i,"Synthetic fixture, NOT scanned OCR")
    return {"id":i,"number":invnum,"message":"Synthetic example created for human review. No OCR performed."}

class InvoiceUpload(BaseModel):
    pass

@app.post("/api/invoices/upload")
async def invoice_upload(file: UploadFile=File(...),u=Depends(get_user)):
    permit(u,"owner","store","accounts")
    allowed={"application/pdf","image/jpeg","image/png"}
    if file.content_type not in allowed: fail(415,"Only PDF/JPEG/PNG")
    data=await file.read(8*1024*1024+1)
    if len(data)>8*1024*1024: fail(413,"Max 8 MB")
    if not data: fail(422,"Empty document")
    UPLOADS.mkdir(parents=True,exist_ok=True)
    checksum=hashlib.sha256(data).hexdigest()
    extension={"application/pdf":"pdf","image/jpeg":"jpg","image/png":"png"}[file.content_type]
    with db() as c:
        exists=row(c,"SELECT id FROM invoices WHERE tenant_id=? AND sha256=?",(u["tenant_id"],checksum))
        if exists: fail(409,"Duplicate document bytes: "+exists["id"])
        supplier=row(c,"SELECT id FROM suppliers WHERE tenant_id=? ORDER BY name LIMIT 1",(u["tenant_id"],))
        if not supplier: fail(422,"Set up suppliers first")
        iid=uuid.uuid4().hex[:10]
        (UPLOADS / f"{u['tenant_id']}_{iid}.{extension}").write_bytes(data)
        c.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(iid,u["tenant_id"],supplier["id"],"UNVERIFIED-"+iid,date.today().isoformat(),0,"needs_review","manual-file-upload",checksum,u["id"],now(),None))
        audit(c,u,"invoice.uploaded",iid,checksum[:16]+" (unparsed; manual review needed)")
    return {"id":iid,"message":"Original stored. No OCR or verification performed. Manually key and verify before approval."}

class InvoiceReview(BaseModel):
    supplier_id:str
    number:str=Field(min_length=3,max_length=80)
    invoice_date:date
    amount_paise:int=Field(gt=0)
    product_id:str
    qty:int=Field(gt=0)
    free_qty:int=Field(ge=0)
    rate_paise:int=Field(gt=0)
    gst_bp:int=Field(ge=0,le=2800)
    batch:str=Field(min_length=1,max_length=80)
    expiry:date

@app.post("/api/invoices/{invoice_id}/review")
def review_invoice(invoice_id:str,p:InvoiceReview,u=Depends(get_user)):
    permit(u,"owner","store","accounts")
    with db() as c:
        inv=require_tenant(c,"invoices",invoice_id,u["tenant_id"])
        if inv["status"]!="needs_review":fail(409,"Invoice no longer reviewable")
        if not row(c,"SELECT id FROM suppliers WHERE id=? AND tenant_id=?",(p.supplier_id,u["tenant_id"])): fail(422,"Invalid supplier")
        if not row(c,"SELECT id FROM products WHERE id=? AND tenant_id=?",(p.product_id,u["tenant_id"])): fail(422,"Invalid product")
        duplicate=row(c,"SELECT id FROM invoices WHERE tenant_id=? AND supplier_id=? AND number=? AND id<>?",(u["tenant_id"],p.supplier_id,p.number,invoice_id))
        if duplicate:fail(409,"Supplier+invoice number already exists")
        expected=round(p.qty*p.rate_paise*(1+p.gst_bp/10000))
        if abs(expected-p.amount_paise)>100:fail(422,f"Tax/total check failed. Expected INR {expected/100:.2f}. Review discount and GST details.")
        c.execute("UPDATE invoices SET supplier_id=?,number=?,invoice_date=?,amount_paise=? WHERE id=?",(p.supplier_id,p.number,p.invoice_date.isoformat(),p.amount_paise,invoice_id))
        c.execute("DELETE FROM invoice_lines WHERE invoice_id=?",(invoice_id,))
        c.execute("INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex[:10],invoice_id,p.product_id,p.qty,p.free_qty,p.rate_paise,p.gst_bp,p.batch,p.expiry.isoformat(),0))
        audit(c,u,"invoice.reviewed",invoice_id,"Manual verification completed; awaiting approval")
    return {"id":invoice_id,"status":"needs_review","expected_paise":expected}

@app.post("/api/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id:str,u=Depends(get_user)):
    permit(u,"owner","accounts")
    with db() as c:
        inv=require_tenant(c,"invoices",invoice_id,u["tenant_id"])
        if inv["status"]!="needs_review":fail(409,"Invoice cannot be approved")
        lines=rows(c,"SELECT * FROM invoice_lines WHERE invoice_id=?",(invoice_id,))
        if not lines or inv["amount_paise"]<=0 or inv["number"].startswith("UNVERIFIED"):
            fail(422,"Invoice must have verified supplier, number, totals and line items")
        c.execute("UPDATE invoices SET status='approved',approved_by=? WHERE id=?",(u["id"],invoice_id))
        audit(c,u,"invoice.approved",invoice_id,"Approval does NOT update stock; GRN required")
    return {"id":invoice_id,"status":"approved","stock_updated":False}

class GRNLine(BaseModel):
    line_id:str
    received_qty:int=Field(ge=0)
    free_received:int=Field(ge=0,default=0)
class GRN(BaseModel):
    lines:list[GRNLine]=Field(min_length=1)

@app.post("/api/invoices/{invoice_id}/grn")
def grn(invoice_id:str,p:GRN,u=Depends(get_user)):
    permit(u,"owner","store")
    with db() as c:
        inv=require_tenant(c,"invoices",invoice_id,u["tenant_id"])
        if inv["status"]!="approved":fail(409,"Invoice must be approved first")
        source=rows(c,"SELECT * FROM invoice_lines WHERE invoice_id=?",(invoice_id,))
        mapping={x["id"]:x for x in source}
        if len({l.line_id for l in p.lines}) != len(p.lines) or set(mapping)!= {l.line_id for l in p.lines}:
            fail(422,"GRN must cover each invoice line exactly once")
        for l in p.lines:
            s=mapping[l.line_id]
            if l.received_qty> s["qty"] or l.free_received> s["free_qty"]:
                fail(422,"Over-receipt requires a formal purchase exception workflow")
            c.execute("UPDATE invoice_lines SET received_qty=? WHERE id=?",(l.received_qty,l.line_id))
            existing=row(c,"SELECT * FROM stock WHERE tenant_id=? AND product_id=? AND batch=? AND expiry=?",(u["tenant_id"],s["product_id"],s["batch"],s["expiry"]))
            if existing:
                c.execute("UPDATE stock SET qty=qty+?,free_qty=free_qty+? WHERE id=?",(l.received_qty,l.free_received,existing["id"]))
            else:
                c.execute("INSERT INTO stock VALUES (?,?,?,?,?,?,?)",(uuid.uuid4().hex[:12],u["tenant_id"],s["product_id"],s["batch"],s["expiry"],l.received_qty,l.free_received))
        c.execute("UPDATE invoices SET status='received' WHERE id=?",(invoice_id,))
        audit(c,u,"stock.grn_posted",invoice_id,"Physically received quantities posted once")
    return {"id":invoice_id,"status":"received","stock_updated":True}

@app.get("/api/orders")
def orders(u=Depends(get_user)):
    with db() as c:
        sql="SELECT o.*,r.name retailer FROM orders o JOIN retailers r ON r.id=o.retailer_id AND r.tenant_id=o.tenant_id WHERE o.tenant_id=?"
        params=[u["tenant_id"]]
        if u["role"]=="sales":sql+=" AND o.salesman_id=?";params.append(u["id"])
        sql+=" ORDER BY o.created_at DESC"
        return rows(c,sql,params)

class OrderCreate(BaseModel):
    retailer_id:str
    product_id:str
    qty:int=Field(gt=0,le=10000)

@app.post("/api/orders")
def create_order(p:OrderCreate,u=Depends(get_user)):
    permit(u,"owner","sales")
    with db() as c:
        retailer=require_tenant(c,"retailers",p.retailer_id,u["tenant_id"])
        product=require_tenant(c,"products",p.product_id,u["tenant_id"])
        available=c.execute("SELECT COALESCE(SUM(qty),0) FROM stock WHERE tenant_id=? AND product_id=? AND expiry>=?",(u["tenant_id"],p.product_id,date.today().isoformat())).fetchone()[0]
        reserved=c.execute("SELECT COALESCE(SUM(l.qty),0) FROM order_lines l JOIN orders o ON o.id=l.order_id WHERE o.tenant_id=? AND l.product_id=? AND o.status='booked'",(u["tenant_id"],p.product_id)).fetchone()[0]
        if available-reserved < p.qty:fail(409,"Insufficient available unreserved nonexpired stock")
        oid=uuid.uuid4().hex[:10]
        salesman="sales" if u["role"]=="owner" else u["id"]
        c.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",(oid,u["tenant_id"],retailer["id"],salesman,retailer["beat"],"booked",p.qty*product["price_paise"],now(),None,None))
        c.execute("INSERT INTO order_lines VALUES (?,?,?,?,?)",(uuid.uuid4().hex[:10],oid,p.product_id,p.qty,product["price_paise"]))
        audit(c,u,"order.created",oid,f"{p.qty} x {product['name']}")
    return {"id":oid,"status":"booked"}

@app.post("/api/orders/{order_id}/dispatch")
def dispatch_order(order_id:str,u=Depends(get_user)):
    permit(u,"owner","store")
    with db() as c:
        o=require_tenant(c,"orders",order_id,u["tenant_id"])
        if o["status"]!="booked":fail(409,"Order not booked")
        lines=rows(c,"SELECT * FROM order_lines WHERE order_id=?",(order_id,))
        for l in lines:
            left=l["qty"]
            batches=rows(c,"SELECT * FROM stock WHERE tenant_id=? AND product_id=? AND qty>0 AND expiry>=? ORDER BY expiry,id",(u["tenant_id"],l["product_id"],date.today().isoformat()))
            if sum(b["qty"] for b in batches)<left:fail(409,"Stock insufficient at dispatch")
            for b in batches:
                take=min(left,b["qty"])
                if take:c.execute("UPDATE stock SET qty=qty-? WHERE id=?",(take,b["id"]))
                left-=take
                if not left:break
        c.execute("UPDATE orders SET status='dispatched' WHERE id=?",(order_id,))
        audit(c,u,"order.dispatched",order_id,"FEFO stock deduction")
    return {"id":order_id,"status":"dispatched"}

class Proof(BaseModel):
    received_by:str=Field(min_length=2,max_length=100)

@app.post("/api/orders/{order_id}/deliver")
def deliver_order(order_id:str,p:Proof,u=Depends(get_user)):
    permit(u,"owner","sales")
    with db() as c:
        o=require_tenant(c,"orders",order_id,u["tenant_id"])
        if u["role"]=="sales" and o["salesman_id"]!=u["id"]:fail(403,"Not your beat order")
        if o["status"]!="dispatched":fail(409,"Order must be dispatched")
        c.execute("UPDATE orders SET status='delivered',pod_name=?,pod_at=? WHERE id=?",(p.received_by,now(),order_id))
        c.execute("UPDATE retailers SET outstanding_paise=outstanding_paise+? WHERE id=? AND tenant_id=?",(o["amount_paise"],o["retailer_id"],u["tenant_id"]))
        audit(c,u,"delivery.pod",order_id,p.received_by)
    return {"id":order_id,"status":"delivered","proof":p.received_by}

@app.get("/api/collections")
def collections(u=Depends(get_user)):
    with db() as c:
        sql="SELECT col.*,r.name retailer FROM collections col JOIN retailers r ON r.id=col.retailer_id WHERE col.tenant_id=?"
        params=[u["tenant_id"]]
        if u["role"]=="sales":sql+=" AND col.salesman_id=?";params.append(u["id"])
        return rows(c,sql+" ORDER BY created_at DESC",params)

class Collect(BaseModel):
    order_id:str
    amount_paise:int=Field(gt=0)
    method:Literal["cash","upi","bank","cheque"]="cash"
    ref:str=Field(min_length=3,max_length=120)
    idempotency_key:str=Field(min_length=8,max_length=100)

@app.post("/api/collections")
def collect(p:Collect,u=Depends(get_user)):
    permit(u,"owner","sales","accounts")
    with db() as c:
        existing=row(c,"SELECT id FROM collections WHERE tenant_id=? AND idempotency_key=?",(u["tenant_id"],p.idempotency_key))
        if existing:return {"id":existing["id"],"idempotent":True}
        o=require_tenant(c,"orders",p.order_id,u["tenant_id"])
        if o["status"]!="delivered":fail(409,"Collection needs delivery confirmation")
        if u["role"]=="sales" and o["salesman_id"]!=u["id"]:fail(403,"Not your beat order")
        paid=c.execute("SELECT COALESCE(SUM(amount_paise),0) FROM collections WHERE tenant_id=? AND order_id=?",(u["tenant_id"],o["id"])).fetchone()[0]
        if paid+p.amount_paise>o["amount_paise"]:fail(409,"Collection would exceed order outstanding")
        cid=uuid.uuid4().hex[:10]
        c.execute("INSERT INTO collections VALUES (?,?,?,?,?,?,?,?,?,?,?)",(cid,u["tenant_id"],o["id"],o["retailer_id"],o["salesman_id"],p.amount_paise,p.method,p.ref,now(),None,p.idempotency_key))
        c.execute("UPDATE retailers SET outstanding_paise=MAX(0,outstanding_paise-?) WHERE id=? AND tenant_id=?",(p.amount_paise,o["retailer_id"],u["tenant_id"]))
        audit(c,u,"collection.recorded",cid,f"{p.method} {p.amount_paise}")
    return {"id":cid,"status":"recorded"}

@app.get("/api/settlements")
def settlements(u=Depends(get_user)):
    permit(u,"owner","sales","accounts")
    with db() as c: return rows(c,"SELECT * FROM settlements WHERE tenant_id=? ORDER BY created_at DESC",(u["tenant_id"],))

class Settlement(BaseModel):
    counts:dict[str,int]
    coins_paise:int=Field(ge=0,le=99999)
    notes:str=""

@app.post("/api/settlements")
def settle(p:Settlement,u=Depends(get_user)):
    permit(u,"owner","sales","accounts")
    salesman="sales" if u["role"]!="sales" else u["id"]
    denominations={"500":50000,"200":20000,"100":10000,"50":5000,"20":2000,"10":1000}
    if set(p.counts)-set(denominations) or any(v<0 or v>5000 for v in p.counts.values()):fail(422,"Invalid denominations")
    counted=sum(denominations[k]*p.counts.get(k,0) for k in denominations)+p.coins_paise
    with db() as c:
        ids=rows(c,"SELECT id,amount_paise FROM collections WHERE tenant_id=? AND salesman_id=? AND method='cash' AND settlement_id IS NULL",(u["tenant_id"],salesman))
        if not ids:fail(409,"No unsettled cash collections")
        expected=sum(a["amount_paise"] for a in ids)
        sid=uuid.uuid4().hex[:10]
        status="pending_approval" if counted!=expected else "matched_pending_approval"
        c.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?,?)",(sid,u["tenant_id"],salesman,expected,counted,status,p.notes,now(),None))
        for record in ids:c.execute("UPDATE collections SET settlement_id=? WHERE id=?",(sid,record["id"]))
        audit(c,u,"settlement.submitted",sid,f"Expected {expected} counted {counted}")
    return {"id":sid,"expected_paise":expected,"counted_paise":counted,"difference_paise":counted-expected,"status":status}

@app.post("/api/settlements/{settlement_id}/approve")
def settlement_approve(settlement_id:str,u=Depends(get_user)):
    permit(u,"owner")
    with db() as c:
        s=require_tenant(c,"settlements",settlement_id,u["tenant_id"])
        if s["status"] not in ("pending_approval","matched_pending_approval"):fail(409,"Settlement already reviewed")
        if s["expected_paise"]!=s["counted_paise"]:fail(409,"Resolve discrepancy before owner can approve; no silent write-offs")
        c.execute("UPDATE settlements SET status='approved',approved_by=? WHERE id=?",(u["id"],settlement_id))
        audit(c,u,"settlement.approved",settlement_id)
    return {"id":settlement_id,"status":"approved"}

@app.get("/api/audit")
def audit_list(u=Depends(get_user)):
    permit(u,"owner","accounts")
    with db() as c:return rows(c,"SELECT actor,action,target,details,created_at FROM audit_events WHERE tenant_id=? ORDER BY id DESC LIMIT 80",(u["tenant_id"],))

@app.get("/api/plans")
def plans(u=Depends(get_user)):
    return [
      {"name":"Starter","monthly":999,"annual":9990,"users":2,"included_pages":30,"target":"Retail / small wholesaler"},
      {"name":"Growth","monthly":1999,"annual":19990,"users":5,"included_pages":100,"target":"Beat-based distributor"},
      {"name":"Pro","monthly":3999,"annual":39990,"users":15,"included_pages":300,"target":"Multi-beat distributor"}
    ]