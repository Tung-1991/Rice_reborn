"""Single-process market/strategy coordinator, independent of open browser tabs."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib,json,logging,math,os,queue,threading,time,uuid
from .config import DEFAULT_SETTINGS,RULE_VERSION,validate_settings,symbol
from .models import DAY,Instrument,dec,new_position,wire
from .store import Store
from .exchange import Binance,MarketStreams,ExchangeError
from .strategy import Strategy,market_context
from .execution import Execution,ACTIVE
from .telegram import Telegram
from .portfolio import buy_budget,entry_block

LOG=logging.getLogger(__name__)

class Service:
    def __init__(self,env,gateway=None,recover_jobs=True):
        self.env=env; self.gateway=gateway or Binance(env.get('BINANCE_API_KEY'),env.get('BINANCE_API_SECRET'))
        self.store=Store(env.data/('account-'+self.gateway.fingerprint+'.sqlite3'))
        self.settings=validate_settings(self.store.get('settings',default=deepcopy(DEFAULT_SETTINGS)))
        self.strategy=Strategy(self.store); self.telegram=Telegram(self.store,env); self.telegram.configure(self.settings)
        self.execution=Execution(self.store,self.gateway,self.strategy,self.notice); self.execution.settings=self.settings
        self.streams=MarketStreams(self.gateway,self.on_event); self.stop=threading.Event(); self.lock=self.execution.lock
        self.bars={}; self.decisions={'REAL':{},'PAPER':{}}; self.exits={}; self.context={}; self.errors={}; self.latest={}; self.events=queue.Queue(maxsize=500)
        self.started=time.time(); self.last_scan=0; self.loading=False; self.last_account=0; self.last_reconcile=0; self.last_trace=0; self.history_loaded=set(); self.market_at=0
        self.jobs={}; self.selected_mode='PAPER'; self.override={}; self._threads=[]; self._observed_errors={}
        if recover_jobs:
            from .backtest import atomic
            for path in (env.data/'backtests').glob('*/job.json'):
                job=json.loads(path.read_text(encoding='utf-8'))
                if job['status'] in ('QUEUED','RUNNING'): job.update(status='INTERRUPTED',message='Backend đã khởi động lại; dữ liệu lượt chạy được giữ.'); atomic(path,job)
        with self.store.transaction() as db:
            for row in db.execute('SELECT id,payload FROM documents WHERE kind="memory"').fetchall():
                value=json.loads(row['payload']); value.pop('candidate',None); value.pop('candidate_at',None); self.store.put('memory',row['id'],value)
        if env.demo: self.load_demo()
    def notice(self,key,group,name,text):
        self.store.event('notice:'+key,'NOTICE',{'group':group,'text':text},symbol=name)
        self.telegram.enqueue(key,group,name,text)
    def settings_save(self,values):
        with self.lock:
            settings=validate_settings({**self.settings,**values})
            self.store.put('settings','main',settings); self.settings=settings; self.execution.settings=settings; self.telegram.configure(settings)
            self.history_loaded.clear(); self.override.clear()
        self.refresh_async()
        return settings
    def on_event(self,kind,row):
        if kind=='market':
            name=row.get('s'); at=float(row.get('T',row.get('E',0)))/1000
            if not name: return
            if row.get('e')=='aggTrade':
                if at>=self.latest.get(name,{}).get('at',0):
                    self.latest[name]={'price':row['p'],'at':at,'source':'WS'}; self.execution.quotes[name]=self.latest[name]
            elif row.get('e')=='kline':
                k=row['k']; bar={'open_time':k['t'],'close_time':k['T'],'open':float(k['o']),'high':float(k['h']),'low':float(k['l']),'close':float(k['c']),
                    'volume':float(k['v']),'quote_volume':float(k['q']),'taker_buy_quote_volume':float(k['Q']),'closed':bool(k['x'])}
                self.latest[name+':bar']=bar
                if at>=self.latest.get(name,{}).get('at',0):
                    self.latest[name]={'price':k['c'],'at':at,'source':'WS_KLINE'}; self.execution.quotes[name]=self.latest[name]
        else:
            try: self.events.put_nowait(row)
            except queue.Full: self.errors['user_stream']='Hàng đợi đầy; đang đối soát REST.'
    def refresh_async(self):
        if self.env.demo: return
        if self.loading: return
        self.loading=True
        def run():
            try: self.refresh()
            except Exception as exc:
                LOG.exception('Market refresh failed'); self.errors['market']=str(exc)
            finally: self.loading=False
        t=threading.Thread(target=run,name='market-loader',daemon=True); t.start()
    def refresh(self):
        now=time.time()
        if not self.execution.instruments or now-self.market_at>3600:
            self.execution.instruments=self.gateway.instruments(); self.market_at=now
        if self.gateway.configured:
            try:
                self.gateway.permissions(); account=self.gateway.account()
                self.execution.account=account; self.execution.account_at=time.time(); self.last_account=time.time(); self.errors.pop('account',None)
                # Account permission is an additional restriction, never a replacement for key permission.
                if account.get('canTrade') is False: self.gateway.capabilities['trade']=False
            except ExchangeError as exc: self.errors['account']=str(exc)
            try:
                self.execution.external_orders=self.gateway.orders(); self.execution.orders_at=time.time(); self.errors.pop('orders',None)
            except ExchangeError as exc: self.errors['orders']=str(exc)
        names=set(self.settings['watchlist'])|{'BTCUSDT','ETHUSDT'}|{p['symbol'] for p in self.execution.positions()}
        if self.gateway.configured and 'BNBUSDT' in self.execution.instruments: names.add('BNBUSDT')
        for b in self.execution.account.get('balances',[]):
            if dec(b['free'])+dec(b['locked'])>0 and b['asset']+'USDT' in self.execution.instruments: names.add(b['asset']+'USDT')
        for name in sorted(names):
            if self.stop.is_set(): break
            if name not in self.execution.instruments: self.errors[name]='Không có cặp Spot USDT đang TRADING.'; continue
            try:
                old=self.bars.get(name,[])
                if not old or int(old[-1]['open_time'])//DAY<int(time.time()*1000)//DAY:
                    rows=self.gateway.klines(name); self.bars[name]=rows; self.store.put('bars',name,rows)
                self.errors.pop(name,None)
                if name not in self.history_loaded:
                    self.recover_history(name)
            except (ExchangeError,ValueError) as exc: self.errors[name]=str(exc)
        if names:
            try:
                prices=self.gateway.prices()
                for row in prices:
                    if row['symbol'] in names and time.time()-self.latest.get(row['symbol'],{}).get('at',0)>10:
                        self.latest[row['symbol']]={'price':row['price'],'at':time.time(),'source':'REST'}
            except ExchangeError as exc: self.errors['prices']=str(exc)
        self.streams.start(names & set(self.execution.instruments)); self.last_scan=time.time(); self.errors.pop('market',None)
    def recover_history(self,name):
        r=self.settings['rules']; now=time.time(); lower=(int(now*1000)//DAY-r['buy_ema_cross_sessions']+1)*DAY
        starts=[]
        for mode in ('REAL','PAPER'):
            m=self.strategy.memory(name,mode,now)
            start=max(int(m.get('reset_at',0)*1000),int(m['admitted']*1000) if r['buy_ema_cross_mode']=='VA' and not r['buy_ema_va_lookback_enabled'] else lower)
            starts.append(start)
        start=min(starts); minutes=[]; end=int(now*1000)
        # Keep a bounded preview. Longer VA recovery follows the last confirmed fill/watch date, paginated on disk.
        while start<end:
            rows=self.gateway.klines(name,'1m',1000,start,end)
            if not rows: break
            minutes.extend(rows); next_at=rows[-1]['open_time']+60000
            if next_at<=start: break
            start=next_at
        if not minutes: raise ValueError('Chưa tải đủ giá phút để xác minh EMA lịch sử.')
        days={x['open_time']//DAY for x in minutes}
        if any(day not in days for day in range(min(starts)//DAY,end//DAY+1)): raise ValueError('Thiếu ngày trong dữ liệu EMA lịch sử.')
        for mode in ('REAL','PAPER'): self.strategy.recover(name,mode,self.bars[name],minutes,r,self.execution.instruments[name],now)
        self.history_loaded.add(name)
    def working_rows(self,name,now):
        rows=deepcopy(self.bars.get(name,[])); live=self.latest.get(name+':bar')
        if live:
            if rows and rows[-1]['open_time']==live['open_time']: rows[-1]=dict(live)
            elif not rows or live['open_time']>rows[-1]['open_time']: rows.append(dict(live))
        rows=rows[-300:]; day=int(now*1000)//DAY*DAY
        closed=[x for x in rows if x.get('closed') and x['open_time']<day]
        if not closed or closed[-1]['open_time']!=day-DAY: raise ValueError('Thiếu nến 1D vừa đóng.')
        recent=closed[-50:]
        if any(b['open_time']-a['open_time']!=DAY for a,b in zip(recent,recent[1:])): raise ValueError('Nến ngày thiếu khoảng dữ liệu.')
        if self.settings['signal_mode']=='CLOSED': return closed
        q=self.execution.quotes.get(name,{})
        if now-q.get('at',0)>10: raise ValueError('Giá realtime đã cũ.')
        if not rows or rows[-1]['open_time']!=day: raise ValueError('Chưa nhận nến ngày đang chạy.')
        price=float(q['price']); rows[-1].update(close=price,high=max(rows[-1]['high'],price),low=min(rows[-1]['low'],price),closed=False)
        return rows
    def step(self,now=None):
        now=now or time.time()
        with self.lock:
            for name,q in list(self.latest.items()):
                if not name.endswith(':bar'): self.execution.quotes[name]=dict(q)
            rows_map={}; r=self.settings['rules']
            for name in list(self.bars):
                try: rows_map[name]=self.working_rows(name,now)
                except ValueError as exc:
                    self.errors[name]=str(exc)
                    for mode in ('REAL','PAPER'): self.decisions[mode][name]={'symbol':name,'mode':mode,'action':'WAIT','reason':str(exc),'ready':False}
            planned=[]
            with self.store.transaction():
                for name in ('BTCUSDT','ETHUSDT'):
                    if name in rows_map:
                        old=self.store.get('context',name,{}); result=market_context(rows_map[name],r,old.get('state','TRANSITION'))
                        day=rows_map[name][-1]['open_time']//DAY
                        if not old:
                            closed=[x for x in rows_map[name] if x.get('closed')]
                            old={'state':'TRANSITION','count':0,'day':-1,'candidate':'TRANSITION'}
                            for length in range(max(r['ma_period'],len(closed)-r['confirm_sessions']),len(closed)+1):
                                candidate=market_context(closed[:length],r,old['state'])['state']
                                old['count']=old['count']+1 if candidate==old['candidate'] else 1
                                old.update(candidate=candidate,day=closed[length-1]['open_time']//DAY)
                                if old['count']>=r['confirm_sessions']: old['state']=candidate
                        if result['state']!=old.get('candidate'): old.update(candidate=result['state'],count=1,day=day)
                        elif old.get('day')!=day: old.update(day=day,count=old.get('count',0)+1)
                        if old.get('count',0)>=r['confirm_sessions']: old['state']=result['state']
                        self.context[name]={**result,'candidate':result['state'],'state':old['state'],'confirmation':old['count'],'confirmation_required':r['confirm_sessions']}
                        self.store.put('context',name,old)
                interval={'TICK':1,'1M':60,'2M':120,'5M':300}[self.settings['indicator_interval']]
                for mode in ('REAL','PAPER'):
                    for name,rows in rows_map.items():
                        prior=self.decisions[mode].get(name,{})
                        if now-prior.get('observed_at',0)<interval: continue
                        d=self.strategy.observe(name,mode,rows,r,self.execution.instruments[name],now,override=self.override.get(mode+':'+name,False),max_gap=interval+10); self.decisions[mode][name]=d
                        held=[p for p in self.execution.positions(mode) if p['symbol']==name]
                        capacity=self.settings['priority'].get(name,{}).get('max_orders',1)
                        pending=any(o['symbol']==name and o['status'] in ACTIVE for o in self.execution.orders(mode))
                        d['notifiable_buy']=bool(d.get('technical_buy') and len(held)<capacity and not pending)
                        if d['notifiable_buy']:
                            cross=(d.get('cross') or {}).get('id',str(int(now)//86400)); self.notice(f'signal:{mode}:{name}:{cross}','signals',name,f'{mode} BUY KỸ THUẬT · EMA {r["buy_ema_fast"]}/{r["buy_ema_slow"]} · RSI {d["marks"]["rsi"]:.2f} · {d["reason"]}')
                        elif prior.get('notifiable_buy') and not held and not pending: self.notice(f'lost:{mode}:{name}:{int(now)}','signals',name,mode+' MẤT ĐIỀU KIỆN BUY')
                self.exits={}
                for p in self.execution.positions():
                    name=p['symbol']; mode=p['mode']; q=self.execution.quotes.get(name,{})
                    if now-q.get('at',0)>10: continue
                    can_act=self.execution.bot[mode] and p['managed'] and p.get('auto_sell',True) and p.get('bot_trade',True) and (mode=='PAPER' or self.gateway.capabilities.get('trade'))
                    evaluated=p if can_act else {**p,'overrides':{**p.get('overrides',{}),'normal_policy':'ALERT','dynamic_policy':'ALERT','indicator_exit_policy':'ALERT'}}
                    prior_protect=dict(p.get('metrics',{}).get('protect',{}))
                    ex=self.strategy.position(evaluated,self.decisions[mode].get(name,{}),r,self.execution.instruments[name],q['price'],now)
                    p['metrics']=evaluated['metrics']
                    protect=p.get('metrics',{}).get('protect',{})
                    if protect.get('active') and (not prior_protect.get('active') or protect['stage']!=prior_protect.get('stage')):
                        key=('armed:' if protect['stage']=='ARM' else 'dynamic-start:')+p['id']
                        self.notice(key,'positions',name,f'{mode} {protect["stage"]} ĐÃ KÍCH HOẠT · giá {q["price"]} · mốc bảo vệ {protect["trigger"]:.8g}')
                    self.store.put('position',p['id'],p)
                    if ex:
                        self.exits[p['id']]=ex; self.notice(f'exit:{p["id"]}:{ex["occurrence"]}:{"AUTO" if can_act else "ALERT"}','positions',name,
                            f'{mode} {ex["reason"]} {ex["policy"] if can_act else "ALERT"} · {ex["pct"]}% {ex["basis"]} · giá {q["price"]}')
                        if ex['policy']=='ALERT' and ex['reason'] in ('ARM','DYNAMIC'):
                            track=p['metrics'][ex['reason']+':ALERT']; track.update(count=track['count']+1,anchor=float(q['price']),peak=0); self.store.put('position',p['id'],p)
                        if can_act and ex['policy']=='AUTO' and not any(o['trade_id']==p['id'] and o['side']=='SELL' and o['status'] in ACTIVE for o in self.execution.orders(mode)):
                            inst=self.execution.instruments[name]; base=dec(p['original'] if ex['basis']=='ORIGINAL' else p['quantity']); qty=inst.rounded(min(dec(p['quantity']),base*dec(ex['pct'])/100),True)
                            planned.append((p['id'],{'symbol':name,'side':'SELL','mode':mode,'quantity':str(qty),'trade_id':p['id'],'reason':ex['reason'],'exit':ex,'request_id':'exit:'+p['id']+':'+ex['occurrence']}))
            for pid,data in planned:
                try: self.execution.create(data,'BOT')
                except (ValueError,PermissionError) as exc: self.errors['exit:'+pid]=str(exc)
            for mode in ('REAL','PAPER'):
                if self.execution.bot[mode] and (mode=='PAPER' or self.gateway.capabilities.get('trade')): self.auto_buy(mode,now)
            self.execution.process(self.exits)
            if self.settings['trace_enabled'] and now-self.last_trace>=self.settings['trace_minutes']*60:
                for mode in ('REAL','PAPER'):
                    for name,d in self.decisions[mode].items(): self.store.event(f'trace:{mode}:{name}:{int(now)//(self.settings["trace_minutes"]*60)}','TRACE',d,mode,name,now)
                self.last_trace=now; self.store.prune()
    def auto_buy(self,mode,now):
        r=self.settings['rules']
        if now<self.store.get('pause',mode,0): return
        for name,d in sorted(self.decisions[mode].items(),key=lambda x:x[0] not in self.settings['priority']):
            if name not in self.settings['watchlist']: continue
            if d.get('action')!='BUY' or now-d.get('observed_at',0)>max(10,{'TICK':1,'1M':60,'2M':120,'5M':300}[self.settings['indicator_interval']]+2): continue
            memory=self.strategy.memory(name,mode,now); cross=(d.get('cross') or {}).get('id')
            if memory.get('loss_accounting_pending') or cross in memory['attempted']: continue
            if entry_block(self.settings,name,self.execution.positions(mode),self.execution.orders(mode)): continue
            ledger=self.store.get('capital',mode+':'+name,{})
            try:
                preview=self.preview(mode,name); budget=dec(preview['budget']); qty=dec(preview['quantity'])
                self.execution.create({'symbol':name,'side':'BUY','mode':mode,'quantity':str(qty),'reason':'VA_ENTRY','request_id':'buy:'+str(cross or f'{name}:{int(now)//86400}')},'BOT',d)
                if not ledger: self.store.put('capital',mode+':'+name,{'ceiling':str(budget)})
                self.override.pop(mode+':'+name,None)
            except (ValueError,PermissionError) as exc: d['capital_reason']=str(exc)
    def set_bot(self,mode,on):
        if mode not in ('REAL','PAPER'): raise ValueError('Sổ không hợp lệ.')
        with self.lock:
            if on and mode=='REAL': self.gateway.guard_trade()
            if on and dec(self.settings['capital_usdt'] if mode=='REAL' else self.settings['paper_capital'])<=0: raise ValueError('Cấu hình vốn trước khi bật BOT.')
            self.execution.bot[mode]=bool(on)
            if on: self.execution.bot['PAPER' if mode=='REAL' else 'REAL']=False
            else:
                for order in self.execution.orders(mode):
                    if order['source']=='BOT' and not order.get('broker_id') and order['status'] in ('PENDING','PAUSED','WAITING_BALANCE'):
                        order.update(status='CANCELED',result='BOT OFF'); self.store.put('order',order['id'],order)
    def preview(self,mode,name,side='BUY',trade_id='',kind='MARKET',limit_price=None):
        if mode not in ('REAL','PAPER') or side not in ('BUY','SELL'): raise ValueError('Sổ/chiều không hợp lệ.')
        if kind not in ('MARKET','LIMIT','LOCAL_LIMIT'): raise ValueError('Loại lệnh không hợp lệ.')
        with self.lock:
            inst=self.execution.instruments.get(name)
            if not inst: raise ValueError('Cặp không hỗ trợ.')
            current=self.execution.quote(name)
            price=current if kind=='MARKET' else dec(limit_price or 0)
            if price<=0: raise ValueError('Nhập giá LIMIT dương để tính AUTO.')
            if kind!='MARKET' and dec(inst.tick)>0 and price%dec(inst.tick): raise ValueError('Giá sai tickSize.')
            if side=='SELL':
                p=self.store.get('position',trade_id)
                if not p or p['mode']!=mode or p['symbol']!=name: raise ValueError('Chọn đúng vị thế cần bán.')
                held=sum((dec(o['remaining']) for o in self.execution.orders(mode) if o['side']=='SELL' and o['trade_id']==trade_id and o['status'] in ACTIVE),dec(0))
                q=inst.rounded(max(dec(0),min(dec(p['quantity'])-held,self.execution.available(mode,inst.base))),True)
                return wire({'quantity':q,'notional':q*price,'reason':'Phần vị thế còn khả dụng để bán.'})
            positions=self.execution.positions(mode); ledger=self.store.get('capital',mode+':'+name,{})
            budget=buy_budget(self.settings,mode,name,positions,self.execution.orders(mode),self.execution.available(mode),self.context.get('BTCUSDT',{}).get('state'),ledger)
            market=kind=='MARKET'
            quantity=inst.rounded(budget/(price*dec('1.002')),market)
            ceiling=min([dec(v) for v in (inst.max_qty,inst.market_max if market else 0) if dec(v)>0],default=quantity)
            quantity=inst.rounded(min(quantity,ceiling),market)
            reason='Ngân sách theo P1, hạn mức, tiền khả dụng; chưa xác nhận đủ điều kiện BUY.'
            if quantity>0:
                try: inst.validate(quantity,price,market)
                except ValueError as exc: quantity=dec(0); reason=str(exc)
            return wire({'quantity':quantity,'budget':budget,'price':price,'notional':quantity*price,'reason':reason})
    def remeasure(self,pid,days):
        if not 1<=int(days)<=30: raise ValueError('Đo từ 1 đến 30 ngày.')
        p=self.store.get('position',pid)
        if not p or dec(p['entry'])<=0: raise ValueError('Vị thế/giá vốn không hợp lệ.')
        if p.get('remeasuring'): raise ValueError('Vị thế đang đo lại.')
        p['remeasuring']=True; self.store.put('position',pid,p)
        def work():
            try:
                since=max(int(p['opened_at']*1000)//60000*60000+60000,(int(time.time()*1000)//DAY-int(days)+1)*DAY)
                at=since; values=[]
                if self.env.demo:
                    values=[float(self.latest[p['symbol']]['price'])]
                else:
                    while at<int(time.time()*1000)-60000:
                        rows=self.gateway.klines(p['symbol'],'1m',1000,at)
                        if not rows: raise ValueError('Thiếu dữ liệu phút; giữ metrics cũ.')
                        values.extend(v for row in rows if row['closed'] for v in (row['high'],row['low']))
                        nxt=rows[-1]['close_time']+1
                        if nxt<=at: break
                        at=nxt
                if not values: raise ValueError('Chưa có mẫu sau lúc mở vị thế.')
                with self.lock:
                    current=self.store.get('position',pid); entry=float(current['entry'])
                    current['metrics']['before_remeasure']=dict(current['metrics'])
                    current['metrics'].update(mfe=max(0,(max(values)/entry-1)*100),mae=min(0,(min(values)/entry-1)*100),measurement_since=since/1000)
                    current.update(auto_sell=False,remeasuring=False); self.store.put('position',pid,current)
                self.errors.pop('remeasure:'+pid,None)
            except Exception as exc:
                self.errors['remeasure:'+pid]=str(exc)
                current=self.store.get('position',pid); current['remeasuring']=False; self.store.put('position',pid,current)
        threading.Thread(target=work,name='position-metrics',daemon=True).start()
    def update_position(self,pid,changes):
        with self.lock:
            p=self.store.get('position',pid)
            if not p: raise ValueError('Không tìm thấy vị thế.')
            for key in ('managed','auto_sell','bot_trade','sl_enabled','tp_enabled','protect_enabled','e_enabled'):
                if key in changes:
                    if type(changes[key]) is not bool: raise ValueError('Công tắc phải boolean.')
                    p[key]=changes[key]
            if 'overrides' in changes:
                validated=validate_settings({**self.settings,'rules':{**self.settings['rules'],**changes['overrides']}})
                p['overrides']={k:validated['rules'][k] for k in changes['overrides']}
            for k in ('sl_price','tp_price'):
                if k in changes:
                    if dec(changes[k])<0: raise ValueError('Giá không âm.')
                    p[k]=str(dec(changes[k]))
            self.store.put('position',pid,p); return p
    def adopt(self,data):
        with self.lock:
            name=symbol(data['symbol']); inst=self.execution.instruments.get(name)
            if not inst: raise ValueError('Cặp không hỗ trợ.')
            qty,entry=dec(data['quantity']),dec(data.get('entry',0)); mode='REAL'
            if qty<=0 or entry<0: raise ValueError('Khối lượng dương, giá vốn không âm.')
            held=sum((dec(b['free'])+dec(b['locked']) for b in self.execution.account.get('balances',[]) if b['asset']==inst.base),dec(0))
            assigned=sum((dec(p['quantity']) for p in self.execution.positions(mode) if p['symbol']==name),dec(0))
            if time.time()-self.execution.account_at>20 or qty>held-assigned: raise ValueError('Vượt số dư chưa phân bổ hoặc snapshot cũ.')
            p=new_position(name,qty,entry,mode,cost_known=entry>0,source='ADOPTED',managed=False)
            self.store.put('position',p['id'],p); self.store.event('adopt:'+p['id'],'POSITION',p,mode,name); return p
    def unlock(self,mode,name,kind):
        with self.lock:
            m=self.strategy.memory(name,mode,time.time())
            if kind=='loss': m.update(loss_streak=0,loss_lock_until=0,loss_reset_at=time.time())
            elif kind=='whipsaw': m['whipsaw_reset']={'day':int(time.time()*1000)//DAY*DAY,'pair':[self.settings['rules']['buy_ema_fast'],self.settings['rules']['buy_ema_slow']]}
            else: raise ValueError('Loại khóa không hợp lệ.')
            self.strategy.save(name,mode,m)
    def snapshot(self):
        positions=self.execution.positions(); holdings=[]; total=dec(0)
        for b in self.execution.account.get('balances',[]):
            q=dec(b['free'])+dec(b['locked'])
            if not q: continue
            quote=1 if b['asset']=='USDT' else self.execution.quotes.get(b['asset']+'USDT',{}).get('price')
            value=q*dec(quote) if quote else None
            if value is not None: total+=value
            holdings.append({**b,'value':value})
        for p in positions:
            q=self.execution.quotes.get(p['symbol'],{}); price=q.get('price'); entry=dec(p['entry'])
            p.update(price=price,price_at=q.get('at'),pnl=(dec(price)-entry)*dec(p['quantity']) if price and p.get('cost_known') else None,
                     profit_pct=(dec(price)/entry-1)*100 if price and entry else None,exit=self.exits.get(p['id']))
        try: paper_cash=self.execution.balance('PAPER')
        except ValueError: paper_cash=0
        local_orders=self.execution.orders(); owned={o.get('broker_id') for o in local_orders if o.get('broker_id')}
        outside=[{'id':'EXTERNAL-'+str(o['orderId']),'mode':'REAL','symbol':o['symbol'],'side':o['side'],'type':o['type'],'quantity':o['origQty'],'filled':o['executedQty'],
            'remaining':str(dec(o['origQty'])-dec(o['executedQty'])),'price':o['price'],'status':o['status'],'created_at':o.get('time',0)/1000,'source':'EXTERNAL','reason':'NGOÀI APP','result':'Chỉ theo dõi; chưa liên kết vị thế.'} for o in self.execution.external_orders if str(o['orderId']) not in owned]
        daily={}
        for name,rows in list(self.bars.items()):
            if not rows: continue
            bar=self.latest.get(name+':bar') or rows[-1]
            previous=next((x['close'] for x in reversed(rows) if x['open_time']<bar['open_time']),None)
            quote=self.execution.quotes.get(name,{})
            daily[name]={'open_time':bar['open_time'],'high':max(float(bar['high']),float(quote.get('price',bar['close']))),
                'low':min(float(bar['low']),float(quote.get('price',bar['close']))),'previous_close':previous}
        return wire({'version':RULE_VERSION,'demo':self.env.demo,'uptime':time.time()-self.started,'loading':self.loading,'updated_at':time.time(),
            'capabilities':self.gateway.capabilities,'bot':self.execution.bot,'quotes':self.execution.quotes,'decisions':self.decisions,'context':self.context,
            'positions':positions,'orders':local_orders[-150:]+outside,'holdings':holdings,'nav':total,'paper_cash':paper_cash,'account_at':self.execution.account_at,
            'account_status':'CURRENT' if time.time()-self.execution.account_at<=20 else 'STALE' if self.execution.account_at else 'NOT_CONFIGURED',
            'streams':self.streams.health,'errors':{**self.errors,**({'telegram':self.telegram.error} if self.telegram.error else {})},
            'telegram_configured':self.telegram.configured,'api_configured':self.gateway.configured,'settings':self.settings,'jobs':self.job_status(),
            'chart':{name:[x['close'] for x in rows[-60:]] for name,rows in self.bars.items()},'daily':daily})
    def start(self):
        self.refresh_async()
        def loop():
            while not self.stop.wait(1):
                try:
                    if self.env.demo: self.demo_tick()
                    else:
                        dirty=not self.events.empty()
                        while not self.events.empty():
                            try: self.events.get_nowait()
                            except queue.Empty: break
                        if not self.loading and (time.time()-self.last_scan>15 or dirty): self.refresh_async()
                        if self.gateway.configured and time.time()-self.last_reconcile>5:
                            self.execution.reconcile(); self.last_reconcile=time.time()
                    self.step()
                    self.report_errors()
                except Exception as exc: LOG.exception('Coordinator cycle failed'); self.errors['runtime']=str(exc)
        for name,fn in [('coordinator',loop),('telegram',lambda:self.telegram_loop())]:
            t=threading.Thread(target=fn,name=name,daemon=True); self._threads.append(t); t.start()
    def telegram_loop(self):
        while not self.stop.wait(2):
            try: self.telegram.flush()
            except Exception: LOG.exception('Telegram worker failed')
    def report_errors(self):
        for component,message in list(self.errors.items()):
            if self._observed_errors.get(component,{}).get('message')==message: continue
            key='system:'+component+':'+hashlib.sha256(message.encode()).hexdigest()[:12]+':'+str(int(time.time()))
            self.notice(key,'system','HỆ THỐNG',component+' · '+message)
            self._observed_errors[component]={'message':message,'key':key}
        for component,previous in list(self._observed_errors.items()):
            if component in self.errors: continue
            delivered=False
            for n in self.store.all('outbox'):
                if n['key']!=previous['key']: continue
                if n['status']=='SENT': delivered=True
                elif n['status']=='PENDING': n['status']='DROPPED'; self.store.put('outbox',n['id'],n)
            if delivered: self.notice('recovered:'+previous['key'],'system','HỆ THỐNG',component+' · ĐÃ PHỤC HỒI')
            self._observed_errors.pop(component,None)
    def close(self): self.stop.set(); self.streams.stop()
    def load_demo(self):
        if self.store.get('settings') is None: self.settings['paper_capital']='3000'
        now=time.time(); day=int(now*1000)//DAY*DAY
        prices={'BTCUSDT':67428.50,'ETHUSDT':3516.24,'LINKUSDT':15.42,'SOLUSDT':172.86}
        for name,price in prices.items():
            tick='0.01' if price>1 else '0.000001'; self.execution.instruments[name]=Instrument(name,name[:-4],tick=tick,step='0.000001')
            rows=[]
            for i in range(301):
                x=price*(.68+.001*i+.045*math.sin(i/6)); at=day-(300-i)*DAY
                rows.append({'open_time':at,'close_time':at+DAY-1,'open':x*.996,'high':x*1.018,'low':x*.982,'close':x,'volume':100000+i*500,'closed':i<300})
            rows[-1].update(close=price,high=max(price,rows[-1]['high'])); self.bars[name]=rows; self.latest[name]={'price':str(price),'at':now,'source':'DEMO'}
        if not self.store.all('position'):
            for name,qty,entry in [('BTCUSDT','.012','62300'),('ETHUSDT','.28','3280'),('LINKUSDT','42','14.8')]:
                p=new_position(name,qty,entry,'PAPER',managed=True,source='DEMO'); self.store.put('position',p['id'],p)
            self.store.put('paper','cash','325.80')
        self.gateway.capabilities={'read':True,'trade':False,'status':'DEMO_READ_ONLY'}
        self.execution.account={'balances':[{'asset':'USDT','free':'1240.50','locked':'0'},{'asset':'BTC','free':'.012','locked':'0'},{'asset':'ETH','free':'.28','locked':'0'}]}; self.execution.account_at=now
        self.settings['watchlist']=list(prices); self.execution.settings=self.settings; self.step(now)
    def demo_tick(self):
        now=time.time()
        for name in self.bars:
            previous=self.latest[name]; self.latest[name]={'price':str(float(previous['price'])*(1+.000005*math.sin(now/5))),'at':now,'source':'DEMO'}
        self.execution.account_at=now
    def job_status(self):
        from .backtest import job_status
        return job_status(self.env.data)
