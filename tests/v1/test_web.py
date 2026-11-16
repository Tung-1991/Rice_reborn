from werkzeug.security import generate_password_hash
from rice_reborn.config import Environment
from rice_reborn.service import Service
from rice_reborn.web import create_web_app

def post(c,path,data): return c.post(path,json=data,headers={'X-CSRF-Token':c.token})

def test_demo_status_no_secrets(client):
    r=client.get('/api/status'); assert r.status_code==200 and r.json['demo'] and len(r.json['positions'])==3
    assert 'BINANCE_API_SECRET' not in r.text

def test_csrf(client): assert client.post('/api/bot',json={'mode':'PAPER','enabled':True}).status_code==403

def test_readonly_and_paper(client):
    d={'mode':'REAL','side':'BUY','symbol':'LINKUSDT','quantity':'1','request_id':'test'}
    assert post(client,'/api/orders',d).status_code==403
    d['mode']='PAPER'; assert post(client,'/api/orders',d).status_code==200

def test_demo_cannot_save_keys(client): assert post(client,'/api/integrations',{'BINANCE_API_KEY':'fake','BINANCE_API_SECRET':'fake'}).status_code==400

def test_settings_save(client):
    r=client.put('/api/settings',json={'rules':{'take_profit_pct':10}},headers={'X-CSRF-Token':client.token})
    assert r.status_code==200 and client.get('/api/settings').json['rules']['take_profit_pct']==10

def test_preview_is_readonly(client):
    before=len(client.get('/api/status').json['orders']); r=post(client,'/api/preview',{'mode':'PAPER','symbol':'SOLUSDT','side':'BUY'})
    assert r.status_code==200 and float(r.json['quantity'])>0 and len(client.get('/api/status').json['orders'])==before

def test_limit_auto_uses_entered_price_and_limit_lot(client,service):
    import time
    from rice_reborn.models import Instrument,dec
    service.execution.instruments['SOLUSDT']=Instrument('SOLUSDT','SOL',step='.001',market_step='1')
    service.execution.quotes['SOLUSDT']={'price':'100','at':time.time()}
    market=post(client,'/api/preview',{'mode':'PAPER','symbol':'SOLUSDT','type':'MARKET'}).json
    limit=post(client,'/api/preview',{'mode':'PAPER','symbol':'SOLUSDT','type':'LIMIT','price':'50'}).json
    assert dec(limit['quantity'])>2*dec(market['quantity'])
    assert dec(limit['quantity'])%1!=0 and dec(limit['quantity'])%dec('.001')==0
    order=post(client,'/api/orders',{'mode':'PAPER','symbol':'SOLUSDT','side':'BUY','type':'LIMIT','price':'50',
        'quantity':limit['quantity'],'request_id':'auto-limit'})
    assert order.status_code==200 and dec(order.json['reserve_price'])==dec('50.1')
    assert dec(order.json['quantity'])*dec(order.json['reserve_price'])<=dec(limit['budget'])
    service.execution.process()
    assert service.store.get('order',order.json['id'])['status']=='PENDING'
    assert not service.store.history('FILL')
    service.execution.quotes['SOLUSDT']={'price':'49','at':time.time()}
    service.execution.process()
    assert service.store.get('order',order.json['id'])['status']=='FILLED'
    assert service.execution.balance('PAPER')>=0
    assert service.store.history('ORDER')[0]['payload']['status']=='FILLED'

def test_invalid_limit_preview_remains_readonly(client):
    before=len(client.get('/api/status').json['orders'])
    response=post(client,'/api/preview',{'mode':'PAPER','symbol':'SOLUSDT','type':'LIMIT','price':'0'})
    assert response.status_code==400
    assert len(client.get('/api/status').json['orders'])==before

def test_live_login(tmp_path):
    env=Environment(tmp_path); env.values={'RICE_PASSWORD_HASH':generate_password_hash('test-only-password')}
    app=create_web_app(Service(env)); app.config['TESTING']=True; c=app.test_client()
    assert c.get('/api/status').status_code==401 and c.get('/').status_code==302
    c.get('/login')
    with c.session_transaction() as s: token=s['csrf']
    assert c.post('/login',data={'csrf':token,'password':'bad'}).status_code==200
    assert c.post('/login',data={'csrf':token,'password':'test-only-password'}).status_code==302
    assert c.get('/api/status').status_code==200

def test_password_change_revokes_existing_session(tmp_path):
    env=Environment(tmp_path); env.values={'RICE_PASSWORD_HASH':generate_password_hash('old-password-only-test')}
    app=create_web_app(Service(env)); c=app.test_client(); c.get('/login')
    with c.session_transaction() as s: token=s['csrf']
    assert c.post('/login',data={'csrf':token,'password':'old-password-only-test'}).status_code==302
    assert c.get('/api/status').status_code==200
    env.values['RICE_PASSWORD_HASH']=generate_password_hash('new-password-only-test')
    assert c.get('/api/status').status_code==401

def test_https_cookie_is_secure_httponly_and_samesite(tmp_path):
    env=Environment(tmp_path); env.values={'RICE_COOKIE_SECURE':'1'}
    cookie=create_web_app(Service(env)).test_client().get('/login',base_url='https://rice.example').headers['Set-Cookie']
    assert 'Secure;' in cookie and 'HttpOnly;' in cookie and 'SameSite=Strict' in cookie

def test_app_ip_allowlist_ignores_spoofed_forwarded_headers(tmp_path):
    env=Environment(tmp_path); env.values={'RICE_ALLOWED_IPS':'198.51.100.10/32'}
    c=create_web_app(Service(env)).test_client()
    assert c.get('/healthz',environ_overrides={'REMOTE_ADDR':'198.51.100.10'}).status_code==200
    denied=c.get('/healthz',environ_overrides={'REMOTE_ADDR':'203.0.113.2'},headers={'X-Forwarded-For':'198.51.100.10'})
    assert denied.status_code==403

def test_ip_port_access(client): assert client.get('/api/status',headers={'Host':'192.0.2.20:8080'}).status_code==200

def test_demo_not_public(client): assert client.get('/',environ_overrides={'REMOTE_ADDR':'192.0.2.1'}).status_code==403

def test_headers(client):
    r=client.get('/'); assert r.headers['X-Frame-Options']=='DENY' and "script-src 'self'" in r.headers['Content-Security-Policy']

def test_position_settings(client):
    p=client.get('/api/status').json['positions'][0]
    r=client.patch('/api/positions/'+p['id'],json={'auto_sell':False,'overrides':{'normal_policy':'ALERT'}},headers={'X-CSRF-Token':client.token})
    assert r.status_code==200 and not r.json['auto_sell'] and r.json['overrides']['normal_policy']=='ALERT'

def test_real_bot_locked(client): assert post(client,'/api/bot',{'mode':'REAL','enabled':True}).status_code==403

def test_paper_bot_separate(client):
    r=post(client,'/api/bot',{'mode':'PAPER','enabled':True}); assert r.status_code==200 and r.json['bot']=={'REAL':False,'PAPER':True}

def test_download_paths(client): assert client.get('/api/backtests/not-a-job/files/report.xlsx').status_code==404

def test_missing_request_id(client): assert post(client,'/api/orders',{'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':'1'}).status_code==400

def test_invalid_decimal_returns_validation_error(client):
    assert post(client,'/api/orders',{'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':'bad','request_id':'bad-decimal'}).status_code==400
