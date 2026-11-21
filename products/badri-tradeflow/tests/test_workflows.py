from fastapi.testclient import TestClient
import pytest
import app.main as m

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DB",tmp_path / "pilot.sqlite3")
    monkeypatch.setattr(m,"UPLOADS",tmp_path / "uploads")
    monkeypatch.setattr(m,"DEMO_MODE",True)
    m.startup()
    return TestClient(m.app)

def req(client,url, method='get', role='owner', **kwargs):
    return getattr(client,method)(url,headers={'X-Demo-User':role},**kwargs)

def test_tenant_isolation_and_role_permissions(client):
    assert len(req(client,'/api/retailers').json())==5
    assert len(req(client,'/api/retailers',role='second_owner').json())==1
    assert req(client,'/api/invoices/inv-demo',role='second_owner').status_code==404
    assert req(client,'/api/invoices',role='sales').status_code==403
    assert req(client,'/api/audit',role='sales').status_code==403

def test_invoice_approval_does_not_create_stock_until_grn(client):
    before=sum(x['qty'] for x in req(client,'/api/stock').json())
    r=req(client,'/api/invoices/create-demo','post',json={'supplier_id':'s2'});assert r.status_code==200,r.text
    iid=r.json()['id'];invoice=req(client,f'/api/invoices/{iid}').json()
    # accounts may approve verified invoice; warehouse alone cannot.
    assert req(client,f'/api/invoices/{iid}/approve','post',role='store',json={}).status_code==403
    assert req(client,f'/api/invoices/{iid}/approve','post',role='accounts',json={}).status_code==200
    assert sum(x['qty'] for x in req(client,'/api/stock').json())==before
    line=invoice['lines'][0]
    assert req(client,f'/api/invoices/{iid}/grn','post',role='store',json={'lines':[{'line_id':line['id'],'received_qty':22,'free_received':2}]}).status_code==200
    assert sum(x['qty'] for x in req(client,'/api/stock').json())==before+22
    assert req(client,f'/api/invoices/{iid}/grn','post',role='store',json={'lines':[{'line_id':line['id'],'received_qty':22,'free_received':2}]}).status_code==409

def test_supplier_invoice_duplicate_blocked(client):
    r=req(client,'/api/invoices/create-demo','post',json={'supplier_id':'s3'});iid=r.json()['id'];inv=req(client,f'/api/invoices/{iid}').json()
    line=inv['lines'][0]
    fields={'supplier_id':'s2','number':'HW-DEMO-001','invoice_date':'2026-11-13','amount_paise':11000,'product_id':'p1','qty':1,'free_qty':0,'rate_paise':10000,'gst_bp':1000,'batch':'B','expiry':'2027-12-30'}
    assert req(client,f'/api/invoices/{iid}/review','post',json=fields).status_code==409

def test_order_delivery_payment_and_retry_idempotency(client):
    r=req(client,'/api/orders','post',json={'retailer_id':'r2','product_id':'p1','qty':2});assert r.status_code==200,r.text
    oid=r.json()['id']
    assert req(client,f'/api/orders/{oid}/deliver','post',role='sales',json={'received_by':'Store keeper'}).status_code==409
    assert req(client,f'/api/orders/{oid}/dispatch','post',role='store',json={}).status_code==200
    assert req(client,f'/api/orders/{oid}/deliver','post',role='sales',json={'received_by':'Store keeper'}).status_code==200
    p={'order_id':oid,'amount_paise':2000,'method':'upi','ref':'UTR-TEST','idempotency_key':'retry-key-123456'}
    a=req(client,'/api/collections','post',role='sales',json=p);b=req(client,'/api/collections','post',role='sales',json=p)
    assert a.status_code==b.status_code==200
    assert a.json()['id']==b.json()['id']
    p['idempotency_key']='different-key-456';p['amount_paise']=100000
    assert req(client,'/api/collections','post',role='sales',json=p).status_code==409

def test_cash_variance_cannot_be_silently_approved(client):
    p={'counts':{'500':1},'coins_paise':0,'notes':'test difference'}
    r=req(client,'/api/settlements','post',role='sales',json=p)
    assert r.status_code==200,r.text
    assert r.json()['difference_paise']!=0
    assert req(client,f"/api/settlements/{r.json()['id']}/approve",'post',json={}).status_code==409

def test_upload_bytes_duplicate(client):
    file={'file':('invoice.pdf',b'%PDF-1.4 demo', 'application/pdf')}
    a=req(client,'/api/invoices/upload','post',files=file)
    b=req(client,'/api/invoices/upload','post',files=file)
    assert a.status_code==200
    assert b.status_code==409