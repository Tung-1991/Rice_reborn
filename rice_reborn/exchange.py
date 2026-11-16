"""Binance Spot gateway: reads and writes have separate capability checks."""
import hashlib,hmac,json,threading,time
from urllib.parse import urlencode
import requests
from .models import Instrument,wire

class ExchangeError(RuntimeError):
    def __init__(self,message,unknown=False,code=None):
        super().__init__(message); self.unknown=unknown; self.code=code

class Binance:
    def __init__(self,key='',secret='',session=None):
        self.session=session or requests.Session(); self.key=key; self.secret=secret
        self.base='https://api.binance.com'; self.offset=0; self.blocked_until=0; self.permission_at=0
        self.capabilities={'read':False,'trade':False,'status':'NOT_CONFIGURED'}
        self.lock=threading.RLock()
    @property
    def configured(self): return bool(self.key and self.secret)
    @property
    def fingerprint(self): return hashlib.sha256(self.key.encode()).hexdigest()[:20] if self.key else 'public'
    def request(self,method,path,params=None,signed=False):
        if method!='GET':
            if (method,path) not in {('POST','/api/v3/order'),('DELETE','/api/v3/order')}: raise PermissionError('Gateway chỉ cho phép giao dịch Binance Spot.')
            self.guard_trade()
        if time.time()<self.blocked_until: raise ExchangeError('Binance đang nghỉ theo Retry-After.')
        data=wire(dict(params or {})); headers={}
        if signed:
            if not self.configured: raise ExchangeError('Chưa cấu hình API Binance.')
            data.update(timestamp=int(time.time()*1000)+self.offset,recvWindow=5000)
            data['signature']=hmac.new(self.secret.encode(),urlencode(data).encode(),hashlib.sha256).hexdigest()
            headers['X-MBX-APIKEY']=self.key
        try:
            response=self.session.request(method,self.base+path,params=data,headers=headers,timeout=(4,12))
            payload=response.json()
        except (requests.RequestException,ValueError):
            raise ExchangeError('Binance: lỗi mạng/JSON; cần đối soát nếu đã gửi lệnh.',unknown=method!='GET') from None
        if response.status_code in (418,429):
            try: delay=max(1,float(response.headers.get('Retry-After',60)))
            except ValueError: delay=60
            self.blocked_until=time.time()+delay
        if response.status_code>=400:
            code=payload.get('code') if isinstance(payload,dict) else None
            if code in (-2014,-2015,-1022):
                self.capabilities={'read':False,'trade':False,'status':'AUTH_ERROR'}; self.permission_at=0
            raise ExchangeError(f'Binance HTTP {response.status_code}, code {code}.',unknown=response.status_code>=500 or code==-1007,code=code)
        return payload
    def sync_time(self):
        self.offset=int(self.request('GET','/api/v3/time')['serverTime'])-int(time.time()*1000)
    def permissions(self,force=False):
        if not self.configured: return self.capabilities
        if force or time.time()-self.permission_at>60:
            try:
                self.sync_time()
                p=self.request('GET','/sapi/v1/account/apiRestrictions',signed=True)
                self.capabilities={'read':bool(p.get('enableReading')),'trade':bool(p.get('enableReading') and p.get('enableSpotAndMarginTrading')),
                  'status':'TRADE_ENABLED' if p.get('enableSpotAndMarginTrading') else 'READ_ONLY',
                  'extra_permissions':any(p.get(k,False) for k in ('enableWithdrawals','enableInternalTransfer','permitsUniversalTransfer','enableFutures','enableMargin'))}
                self.permission_at=time.time()
            except ExchangeError:
                self.capabilities={'read':False,'trade':False,'status':'UNKNOWN'}
                raise
        return self.capabilities
    def guard_trade(self):
        if not self.permissions().get('trade'): raise PermissionError('API chưa có quyền Spot Trade; chức năng đang khóa.')
    def instruments(self):
        data=self.request('GET','/api/v3/exchangeInfo',{'permissions':'SPOT'})
        out={}
        for row in data['symbols']:
            if row['status']!='TRADING' or row['quoteAsset']!='USDT' or not row.get('isSpotTradingAllowed',True): continue
            fs={f['filterType']:f for f in row['filters']}; lot=fs['LOT_SIZE']; market=fs.get('MARKET_LOT_SIZE',{}); notion=fs.get('NOTIONAL',fs.get('MIN_NOTIONAL',{}))
            out[row['symbol']]=Instrument(row['symbol'],row['baseAsset'],tick=fs['PRICE_FILTER']['tickSize'],step=lot['stepSize'],min_qty=lot['minQty'],max_qty=lot['maxQty'],
                min_notional=notion.get('minNotional','0'),max_notional=notion.get('maxNotional','0'),market_step=market.get('stepSize','0'),market_min=market.get('minQty','0'),market_max=market.get('maxQty','0'),filters=row['filters'])
        return out
    def klines(self,name,interval='1d',limit=300,start=None,end=None):
        params={'symbol':name,'interval':interval,'limit':min(1000,limit)}
        if start is not None: params['startTime']=int(start)
        if end is not None: params['endTime']=int(end)
        rows=self.request('GET','/api/v3/klines',params); now=int(time.time()*1000)+self.offset
        return [{'open_time':int(r[0]),'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':float(r[5]),
                 'close_time':int(r[6]),'closed':int(r[6])<now,'quote_volume':float(r[7]),'taker_buy_quote_volume':float(r[10])} for r in rows]
    def prices(self): return self.request('GET','/api/v3/ticker/price')
    def ticker(self): return self.request('GET','/api/v3/ticker/24hr')
    def account(self): return self.request('GET','/api/v3/account',{'omitZeroBalances':'true'},signed=True)
    def orders(self): return self.request('GET','/api/v3/openOrders',signed=True)
    def order(self,name,client_id): return self.request('GET','/api/v3/order',{'symbol':name,'origClientOrderId':client_id},signed=True)
    def trades(self,name,from_id=None):
        p={'symbol':name,'limit':1000}
        if from_id is not None: p['fromId']=from_id
        return self.request('GET','/api/v3/myTrades',p,signed=True)
    def fees(self,name): return self.request('GET','/sapi/v1/asset/tradeFee',{'symbol':name},signed=True)
    def place(self,intent):
        self.guard_trade()
        p={'symbol':intent['symbol'],'side':intent['side'],'type':intent['type'],'quantity':intent['quantity'],'newClientOrderId':intent['client_id'],'newOrderRespType':'FULL'}
        if intent['type']=='LIMIT': p.update(price=intent['price'],timeInForce='GTC')
        return self.request('POST','/api/v3/order',p,signed=True)
    def cancel(self,intent):
        self.guard_trade()
        return self.request('DELETE','/api/v3/order',{'symbol':intent['symbol'],'origClientOrderId':intent['client_id']},signed=True)

class MarketStreams:
    """Bounded latest-value cache. No tick history is retained in RAM."""
    def __init__(self,gateway,on_event):
        self.gateway=gateway; self.on_event=on_event; self.symbols=(); self.stop_event=threading.Event(); self.sockets=[]; self.threads=[]; self.health={}
    def start(self,names):
        names=tuple(sorted(set(names)))
        if names==self.symbols: return
        self.stop(); self.stop_event=threading.Event(); self.symbols=names
        if not names: return
        streams='/'.join(f'{s.lower()}@aggTrade/{s.lower()}@kline_1d' for s in names)
        self._launch('market','wss://stream.binance.com:9443/stream?streams='+streams)
        if self.gateway.configured: self._launch('account','wss://ws-api.binance.com:443/ws-api/v3')
    def _launch(self,kind,url):
        import websocket
        stop=self.stop_event
        def worker():
            delay=1
            while not stop.is_set():
                def opened(ws):
                    self.health[kind]='CONNECTED'
                    if kind=='account':
                        p={'apiKey':self.gateway.key,'timestamp':int(time.time()*1000)+self.gateway.offset}
                        p['signature']=hmac.new(self.gateway.secret.encode(),urlencode(sorted(p.items())).encode(),hashlib.sha256).hexdigest()
                        ws.send(json.dumps({'id':'rice-user','method':'userDataStream.subscribe.signature','params':p}))
                def message(ws,raw):
                    try:
                        data=json.loads(raw); data=data.get('data',data); data=data.get('event',data)
                        if 'error' in data: self.health[kind]='REST_FALLBACK'; return
                        self.on_event(kind,data)
                    except (ValueError,KeyError,TypeError): self.health[kind]='INVALID_MESSAGE'
                ws=websocket.WebSocketApp(url,on_open=opened,on_message=message,on_error=lambda *_:self.health.update({kind:'RECONNECTING'}))
                self.sockets.append(ws)
                try: ws.run_forever(ping_interval=20,ping_timeout=10)
                except Exception: self.health[kind]='RECONNECTING'
                finally:
                    if ws in self.sockets: self.sockets.remove(ws)
                if stop.wait(delay): break
                delay=min(30,delay*2)
        thread=threading.Thread(target=worker,name='binance-'+kind,daemon=True); self.threads.append(thread); thread.start()
    def stop(self):
        self.stop_event.set()
        for ws in list(self.sockets): ws.close()
        self.symbols=()
