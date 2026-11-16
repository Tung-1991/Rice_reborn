from copy import deepcopy
from datetime import datetime,timezone
import json,time
import pytest
from rice_reborn.config import Environment,DEFAULT_SETTINGS
from rice_reborn.exchange import Binance,ExchangeError
from rice_reborn.models import Instrument,DAY,dec
from rice_reborn.telegram import Telegram
from rice_reborn.store import Store
from rice_reborn.backtest import simulate

class Response:
    def __init__(self,data,status=200,headers=None): self.data=data; self.status_code=status; self.headers=headers or {}; self.ok=status<400
    def json(self): return self.data

class FakeSession:
    def __init__(self,trading=False): self.calls=[]; self.trading=trading
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if url.endswith('/time'): return Response({'serverTime':int(time.time()*1000)})
        if url.endswith('/apiRestrictions'): return Response({'enableReading':True,'enableSpotAndMarginTrading':self.trading})
        return Response({'status':'NEW','orderId':123})

def test_permission_is_read_from_api():
    session=FakeSession(); b=Binance('test-key','test-secret',session)
    assert b.permissions()=={'read':True,'trade':False,'status':'READ_ONLY','extra_permissions':False}
    assert all(m=='GET' for m,_,_ in session.calls)
    with pytest.raises(PermissionError): b.request('POST','/api/v3/order',{},signed=True)
    assert all(m=='GET' for m,_,_ in session.calls)

def test_transport_forbids_non_spot_actions():
    b=Binance('test','test',FakeSession(True))
    with pytest.raises(PermissionError): b.request('POST','/sapi/v1/capital/withdraw/apply',{},signed=True)

def test_signatures_and_headers_are_not_plain_secrets():
    session=FakeSession(True); b=Binance('test-key','test-secret',session)
    b.permissions(); b.request('GET','/api/v3/account',signed=True)
    kwargs=session.calls[-1][2]
    assert kwargs['headers']['X-MBX-APIKEY']=='test-key' and len(kwargs['params']['signature'])==64
    assert 'test-secret' not in str(kwargs)

def test_rate_limit_honors_retry_after():
    session=FakeSession(); session.request=lambda *a,**kw:Response({'code':-1003},429,{'Retry-After':'120'})
    b=Binance(session=session)
    with pytest.raises(ExchangeError): b.prices()
    assert b.blocked_until>time.time()+110
    with pytest.raises(ExchangeError,match='Retry-After'): b.prices()

def test_write_timeout_is_unknown():
    import requests
    session=FakeSession(True); b=Binance('test','test',session); b.permissions()
    def fail(*a,**kw): raise requests.Timeout('sensitive-url-must-not-escape')
    session.request=fail
    with pytest.raises(ExchangeError) as caught: b.request('POST','/api/v3/order',{},signed=True)
    assert caught.value.unknown and 'sensitive-url' not in str(caught.value)

def telegram(tmp_path):
    env=Environment(tmp_path); env.demo=False; env.values={'TELEGRAM_BOT_TOKEN':'fake-token','TELEGRAM_CHAT_ID':'fake-chat'}
    t=Telegram(Store(tmp_path/'telegram.db'),env); t.configure(deepcopy(DEFAULT_SETTINGS)); return t

def test_telegram_retry_only_marks_success(tmp_path):
    t=telegram(tmp_path); t.enqueue('one','signals','LINKUSDT','REAL BUY')
    def fail(_): raise RuntimeError('network failed')
    t.send=fail; t.flush(); n=t.store.all('outbox')[0]; assert n['status']=='PENDING'
    n['last_try']=0; t.store.put('outbox',n['id'],n); t.send=lambda _:42; t.flush()
    assert t.store.all('outbox')[0]['status']=='SENT'
    again=Telegram(t.store,t.env); again.configure(deepcopy(DEFAULT_SETTINGS)); again.enqueue('one','signals','LINKUSDT','REAL BUY')
    assert len(t.store.all('outbox'))==1

def test_telegram_off_does_not_replay(tmp_path):
    t=telegram(tmp_path); t.enqueue('old','signals','LINKUSDT','REAL BUY')
    settings=deepcopy(DEFAULT_SETTINGS); settings['telegram_enabled']=False; t.configure(settings)
    assert t.store.all('outbox')[0]['status']=='DROPPED'
    t.enqueue('while-off','signals','LINKUSDT','REAL BUY'); assert len(t.store.all('outbox'))==1
    settings['telegram_enabled']=True; t.configure(settings); t.enqueue('new','signals','LINKUSDT','REAL BUY')
    assert [x['status'] for x in t.store.all('outbox')]==['DROPPED','PENDING']

class HistoricalGateway:
    def __init__(self,start): self.start=start
    def instruments(self): return {'LINKUSDT':Instrument('LINKUSDT','LINK',tick='.01',step='.001'),'BTCUSDT':Instrument('BTCUSDT','BTC')}
    def klines(self,name,interval,limit,start,end):
        assert interval=='1d'
        rows=[]; first=start//DAY*DAY
        for at in range(first,min(end,self.start+10*DAY)+1,DAY):
            delta=(at-self.start)//DAY; values=[99,101,102,111,95,96,100,103,107,108,109]
            value=values[delta] if 0<=delta<len(values) else 100
            rows.append({'open_time':at,'close_time':at+DAY-1,'open':value,'high':value*1.01,'low':value*.99,'close':value,'volume':100,'closed':True})
            if len(rows)>=limit: break
        return rows

def test_backtest_end_to_end_without_credentials(tmp_path):
    from openpyxl import load_workbook
    start=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp()*1000)
    job={'id':'test','symbols':['LINKUSDT'],'start':'2026-09-01','end':'2026-09-10','capital':'1000','simulation':'DAILY','settings':deepcopy(DEFAULT_SETTINGS)}
    job['settings']['watchlist']=['LINKUSDT']
    result=simulate(job,tmp_path,HistoricalGateway(start))
    assert result['fills']>=2 and result['observations']==10 and len(result['files'])==1
    book=load_workbook(tmp_path/result['files'][0]); assert len(book.worksheets)==1
    assert book.active['A1'].value=='LƯỢT' and book.active.max_row>10

def test_backtest_cancel(tmp_path):
    (tmp_path/'cancel').touch(); start=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp()*1000)
    job={'symbols':['LINKUSDT'],'start':'2026-09-01','end':'2026-09-10','capital':'1000','simulation':'DAILY','settings':deepcopy(DEFAULT_SETTINGS)}
    with pytest.raises(InterruptedError): simulate(job,tmp_path,HistoricalGateway(start))
