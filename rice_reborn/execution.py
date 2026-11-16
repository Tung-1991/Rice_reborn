"""Durable orders and fill accounting shared by REAL and PAPER."""
import hashlib,json,threading,time,uuid
from .models import dec,ZERO,new_position,wire
from .exchange import ExchangeError

FINAL={'FILLED','CANCELED','REJECTED','EXPIRED','CANCELLED'}
ACTIVE={'PENDING','SENDING','NEW','PARTIALLY_FILLED','UNKNOWN','CANCEL_PENDING','ACCOUNTING_PENDING','WAITING_BALANCE','PAUSED'}

class Execution:
    def __init__(self,store,gateway,strategy,notify=lambda *a,**k:None,clock=time.time):
        self.store=store; self.gateway=gateway; self.strategy=strategy; self.notify=notify; self.lock=threading.RLock(); self.clock=clock
        self.instruments={}; self.quotes={}; self.account={}; self.account_at=0; self.external_orders=[]; self.orders_at=0; self.settings={}; self.bot={'REAL':False,'PAPER':False}
        for order in store.all('order'):
            if order['status']=='SENDING': order['status']='UNKNOWN'; store.put('order',order['id'],order)
            elif order['source']=='BOT' and order['status']=='PENDING': order['status']='CANCELED'; store.put('order',order['id'],order)
    def save_order(self,order):
        # Keep UI/manual history aligned with the committed state transition.
        with self.store.transaction():
            previous=self.store.get('order',order['id'])
            self.store.put('order',order['id'],order)
            fields=('status','filled','result','broker_id')
            if previous and any(previous.get(k)!=order.get(k) for k in fields):
                signature=hashlib.sha256(json.dumps(wire({k:order.get(k) for k in fields}),sort_keys=True).encode()).hexdigest()[:24]
                self.store.event('order-state:'+order['id']+':'+signature,'ORDER',order,order['mode'],order['symbol'],self.clock())
    def positions(self,mode=None): return [p for p in self.store.all('position') if dec(p['quantity'])>0 and (not mode or p['mode']==mode)]
    def orders(self,mode=None): return [o for o in self.store.all('order') if not mode or o['mode']==mode]
    def quote(self,name):
        q=self.quotes.get(name,{})
        if not -5<=self.clock()-q.get('at',0)<=10 or dec(q.get('price',0))<=0: raise ValueError('Chờ giá thị trường mới (tối đa 10 giây).')
        return dec(q['price'])
    def balance(self,mode,asset='USDT'):
        if mode=='PAPER':
            if asset=='USDT': return dec(self.store.get('paper','cash',self.settings.get('paper_capital','1000')))
            return sum((dec(p['quantity']) for p in self.positions(mode) if p['symbol']==asset+'USDT'),ZERO)
        if self.clock()-self.account_at>20: raise ValueError('Snapshot tài khoản cũ; chờ đồng bộ.')
        return next((dec(x['free']) for x in self.account.get('balances',[]) if x['asset']==asset),ZERO)
    def reserved(self,mode,side,name=None):
        # Broker working orders are already excluded from Binance free balances.
        return sum((dec(o['remaining'])*(dec(o['reserve_price']) if side=='BUY' else 1) for o in self.orders(mode)
            if o['side']==side and o['status'] in ACTIVE and (not name or o['symbol']==name)
            and (mode=='PAPER' or not o.get('broker_id'))),ZERO)
    def available(self,mode,asset='USDT'):
        return max(ZERO,self.balance(mode,asset)-self.reserved(mode,'BUY' if asset=='USDT' else 'SELL',None if asset=='USDT' else asset+'USDT'))
    def create(self,data,source='MANUAL',decision=None,parent_trade=None):
        with self.lock:
            mode=data.get('mode'); side=data.get('side'); name=data['symbol']; kind=data.get('type','MARKET')
            if mode not in ('REAL','PAPER') or side not in ('BUY','SELL') or kind not in ('MARKET','LIMIT','LOCAL_LIMIT'): raise ValueError('Sổ/chiều/loại lệnh không hợp lệ.')
            entry_plan=data.get('entry_plan',{})
            if not isinstance(entry_plan,dict) or set(entry_plan)-{'protect_enabled','e_enabled','take_profit_pct','initial_sl_pct'}:
                raise ValueError('Cấu hình lượt mua không hợp lệ.')
            if entry_plan and (side!='BUY' or source!='MANUAL'): raise ValueError('Cấu hình riêng chỉ dùng cho lượt mua tay.')
            for key,value in entry_plan.items():
                if key.endswith('_enabled'):
                    if type(value) is not bool: raise ValueError('E/M phải là boolean.')
                elif not (dec('-99.99')<=dec(value)<=0 if key=='initial_sl_pct' else 0<=dec(value)<=10000):
                    raise ValueError('SL/TP riêng ngoài giới hạn.')
            token=str(data.get('request_id') or uuid.uuid4().hex)
            if len(token)>100: raise ValueError('Request ID quá dài.')
            oid=hashlib.sha256((mode+':'+token).encode()).hexdigest()[:28]
            signature=hashlib.sha256(json.dumps(wire({k:v for k,v in data.items() if k!='request_id'}),sort_keys=True).encode()).hexdigest()
            existing=self.store.get('order',oid)
            if existing:
                if existing['signature']!=signature: raise ValueError('Request ID đã dùng cho nội dung khác.')
                return existing
            if mode=='REAL': self.gateway.guard_trade()
            instrument=self.instruments.get(name)
            if not instrument: raise ValueError('Cặp Spot USDT không được hỗ trợ.')
            current=self.quote(name); price=dec(data.get('price') or current); qty=dec(data['quantity'])
            instrument.validate(qty,current if kind=='MARKET' else price,kind=='MARKET')
            p=None
            if side=='SELL':
                p=self.store.get('position',data.get('trade_id',''))
                if not p or p['mode']!=mode or p['symbol']!=name: raise ValueError('Phải chọn đúng vị thế/sổ để bán.')
                allocated=sum((dec(o['remaining']) for o in self.orders(mode) if o.get('trade_id')==p['id'] and o['side']=='SELL' and o['status'] in ACTIVE),ZERO)
                if qty>dec(p['quantity'])-allocated: raise ValueError('Khối lượng vượt phần vị thế chưa dành cho SELL.')
                if qty>self.available(mode,instrument.base): raise ValueError('Coin khả dụng không đủ (có thể đang khóa ở lệnh khác).')
            reserve=(current if kind=='MARKET' else price)*dec('1.002')
            if side=='BUY' and qty*reserve>self.available(mode): raise ValueError('USDT khả dụng không đủ gồm dự phòng phí.')
            if source=='BOT':
                if not self.bot[mode]: raise ValueError('BOT đang OFF.')
                if side=='SELL' and (not p['managed'] or not p.get('auto_sell',True) or not p.get('bot_trade',True)): raise ValueError('Vị thế chưa bật quản lý/BOT/tự bán.')
                priority=self.settings.get('priority',{}).get(name,{})
                if priority.get('mode')=='ALERT': raise ValueError('Mã Priority ALERT chỉ nhận tín hiệu.')
                if mode=='REAL':
                    owned={o.get('broker_id') for o in self.orders(mode) if o.get('broker_id')}
                    if side=='BUY' and any(x['symbol']==name and str(x['orderId']) not in owned for x in self.external_orders): raise ValueError('Có lệnh ngoài app đang hoạt động cùng cặp.')
                    physical=sum((dec(x['free'])+dec(x['locked']) for x in self.account.get('balances',[]) if x['asset']==instrument.base),ZERO)
                    recorded=sum((dec(p['quantity']) for p in self.positions(mode) if p['symbol']==name),ZERO)
                    if recorded>physical: raise ValueError('CHỜ ĐỐI SOÁT: số dư Binance thấp hơn sổ vị thế.')
            now=self.clock()
            order={'id':oid,'signature':signature,'client_id':'RR'+oid,'symbol':name,'mode':mode,'side':side,'type':'LIMIT' if kind in ('LIMIT','LOCAL_LIMIT') else 'MARKET',
                'local_trigger':kind=='LOCAL_LIMIT','price':str(price),'reserve_price':str(reserve),'quantity':str(qty),'remaining':str(qty),'filled':'0','notional':'0',
                'status':'PENDING','source':source,'reason':data.get('reason','MANUAL'),'trade_id':p['id'] if p else parent_trade or uuid.uuid4().hex,
                'created_at':now,'updated_at':now,'broker_id':'','cross_id':(decision or {}).get('cross',{}).get('id','') if (decision or {}).get('cross') else '',
                'decision':decision or {},'exit':data.get('exit'),'entry_plan':entry_plan, 'result':'CHƯA GỬI SÀN'}
            with self.store.transaction():
                self.save_order(order); self.store.event('order:'+oid,'ORDER',order,mode,name)
                if source=='BOT' and side=='BUY' and order['cross_id']:
                    m=self.strategy.memory(name,mode,now); m['attempted'].append(order['cross_id']); self.strategy.save(name,mode,m)
            self.notify('order:'+oid,'orders',name,f'{mode} {side} {qty} · CHỜ GỬI · {order["reason"]}')
            return order
    def cancel(self,oid):
        with self.lock:
            order=self.store.get('order',oid)
            if not order: raise ValueError('Không tìm thấy lệnh.')
            if order['status'] in FINAL: return order
            if order['mode']=='REAL': self.gateway.guard_trade()
            if order['status'] in ('UNKNOWN','ACCOUNTING_PENDING'): raise ValueError('Lệnh chưa rõ; cần đối soát trước khi hủy/sửa.')
            if order['mode']=='REAL' and order.get('broker_id'):
                order['status']='CANCEL_PENDING'; self.save_order(order)
                try: self.report(order,self.gateway.cancel(order))
                except ExchangeError as exc:
                    order['result']=str(exc); self.save_order(order); raise
            else:
                order.update(status='CANCELED',result='Đã hủy tại local',updated_at=time.time()); self.save_order(order)
            current=self.store.get('order',oid)
            return current
    def replace(self,oid,data):
        with self.lock:
            old=self.store.get('order',oid)
            if not old or old['type']!='LIMIT': raise ValueError('Chỉ sửa lệnh LIMIT.')
            self.instruments[old['symbol']].validate(data.get('quantity',old['remaining']),data.get('price',old['price']))
            self.cancel(oid); old=self.store.get('order',oid)
            if old['status'] not in FINAL: raise ValueError('Chưa xác nhận hủy; chưa tạo lệnh thay.')
            if old['mode']=='REAL': self.account=self.gateway.account(); self.account_at=self.clock()
            # Explicit cancel-then-new avoids interpreting a partially successful cancelReplace as success.
            return self.create({'mode':old['mode'],'side':old['side'],'symbol':old['symbol'],'type':'LIMIT','quantity':data.get('quantity',old['remaining']),
                'price':data.get('price',old['price']),'trade_id':old['trade_id'],'request_id':data['request_id'],'reason':'REPLACE:'+oid,
                'entry_plan':old.get('entry_plan',{})},parent_trade=old['trade_id'])
    def process(self,exit_decisions=None):
        exit_decisions=exit_decisions or {}
        with self.lock:
            for order in self.orders():
                if order['status'] not in ('PENDING','NEW','PARTIALLY_FILLED','WAITING_BALANCE'): continue
                mode=order['mode']
                if order['source']=='BOT' and not self.bot[mode] and not order.get('broker_id'):
                    order.update(status='CANCELED',result='BOT OFF'); self.save_order(order); continue
                try:
                    price=self.quote(order['symbol'])
                    if order['side']=='BUY' and not order.get('broker_id'):
                        own=dec(order['remaining'])*dec(order['reserve_price'])
                        bounded_price=price if order['type']=='MARKET' else dec(order['price'])
                        if dec(order['remaining'])*bounded_price*dec('1.002')>self.available(mode)+own:
                            order.update(status='REJECTED',result='Sức mua thay đổi trước gửi/khớp'); self.save_order(order); continue
                    if order['source']=='BOT' and order['side']=='SELL' and not order.get('broker_id'):
                        p=self.store.get('position',order['trade_id']); current=exit_decisions.get(order['trade_id']); original=order.get('exit') or {}
                        if not p or not p['managed'] or not p.get('auto_sell',True) or not p.get('bot_trade',True) or not current or current['occurrence']!=original.get('occurrence') or current['policy']!='AUTO':
                            order.update(status='CANCELED',result='RECHECK: điều kiện/quyền đã đổi'); self.save_order(order); continue
                    if order.get('local_trigger') and ((order['side']=='BUY' and price>dec(order['price'])) or (order['side']=='SELL' and price<dec(order['price']))): continue
                    if mode=='PAPER':
                        if order['type']=='LIMIT' and ((order['side']=='BUY' and price>dec(order['price'])) or (order['side']=='SELL' and price<dec(order['price']))): continue
                        fill_price=price*(dec('1.0005') if order['side']=='BUY' else dec('.9995'))
                        if order['type']=='LIMIT': fill_price=min(fill_price,dec(order['price'])) if order['side']=='BUY' else max(fill_price,dec(order['price']))
                        q=dec(order['remaining']); self.apply_fill(order,{'id':'paper:'+order['id'],'qty':str(q),'price':str(fill_price),'commission':str(q*fill_price*dec('.001')),'commissionAsset':'USDT','time':int(self.clock()*1000)})
                        order=self.store.get('order',order['id']); order.update(status='FILLED',result='PAPER khớp mô phỏng'); self.save_order(order)
                    elif not order.get('broker_id'):
                        self.gateway.guard_trade()
                        self.quote(order['symbol'])
                        if time.time()-self.account_at>20: continue
                        order.update(status='SENDING',updated_at=time.time()); self.save_order(order)
                        try: self.report(order,self.gateway.place(order))
                        except ExchangeError as exc:
                            order.update(status='UNKNOWN' if exc.unknown else 'REJECTED',result=str(exc)); self.save_order(order)
                except (ValueError,PermissionError) as exc:
                    if not order.get('broker_id'): order.update(status='PAUSED',result=str(exc)); self.save_order(order)
    def report(self,order,row,trades=None):
        current=self.store.get('order',order['id']); expected=current['client_id']
        if row.get('symbol',current['symbol'])!=current['symbol'] or row.get('origClientOrderId',row.get('clientOrderId',expected))!=expected: raise ValueError('Phản hồi không đúng lệnh.')
        current['broker_id']=str(row.get('orderId',current.get('broker_id','')))
        fills=row.get('fills',[]) if trades is None else [x for x in trades if str(x.get('orderId'))==current['broker_id']]
        for f in fills:
            fid=f.get('tradeId',f.get('id'))
            if fid is None: continue
            fill={**f,'id':current['symbol']+':'+str(fid),'qty':f.get('qty',f.get('quantity')),'time':f.get('time',row.get('transactTime',int(time.time()*1000)))}
            self.apply_fill(current,fill)
        current=self.store.get('order',order['id']); current['broker_id']=str(row.get('orderId',current.get('broker_id','')))
        executed=dec(row.get('executedQty',current['filled']))
        status=row.get('status','UNKNOWN')
        current.update(status='ACCOUNTING_PENDING' if executed!=dec(current['filled']) else status,result='CHỜ ĐỐI SOÁT KHỚP' if executed!=dec(current['filled']) else status,updated_at=time.time())
        self.save_order(current)
    def reconcile(self):
        for order in self.orders('REAL'):
            if order['status'] not in ACTIVE or order['status']=='PENDING': continue
            try:
                row=self.gateway.order(order['symbol'],order['client_id'])
                trades=self.gateway.trades(order['symbol']) if dec(row.get('executedQty',0))>dec(order['filled']) else []
                self.report(order,row,trades)
            except ExchangeError as exc:
                # A not-found after an unknown POST is not proof it was never accepted.
                order['result']=str(exc); self.save_order(order)
        self.repair_fees()
    def repair_fees(self):
        from .accounting import rebuild
        pending=[p for p in self.store.all('position') if p.get('fees_pending')]
        for p in pending[:5]:
            with self.store.transaction() as db:
                rows=[{**dict(x),'payload':json.loads(x['payload'])} for x in db.execute('SELECT * FROM events WHERE kind="FILL" AND json_extract(payload,"$.trade_id")=? ORDER BY at,seq',(p['id'],))]
            changed=False
            for row in rows:
                f=row['payload']; asset=f.get('commissionAsset','USDT')
                if f.get('fee_usdt') is not None or dec(f.get('commission',0))==0: continue
                try:
                    minute=int(row['at']*1000)//60000*60000; bars=self.gateway.klines(asset+'USDT','1m',1,minute,minute+59999)
                    if not bars or bars[0]['open_time']!=minute or not bars[0]['closed']: continue
                    f.update(fee_usdt=str(dec(f['commission'])*dec(bars[0]['close'])),fee_valuation='ESTIMATED_MINUTE_CLOSE'); changed=True
                except ExchangeError: continue
            if not changed: continue
            with self.lock,self.store.transaction() as db:
                current=self.store.get('position',p['id'])
                # Fills may have arrived while the public price was being read.
                latest=[{**dict(x),'payload':json.loads(x['payload'])} for x in db.execute('SELECT * FROM events WHERE kind="FILL" AND json_extract(payload,"$.trade_id")=? ORDER BY at,seq',(p['id'],))]
                repairs={x['id']:x['payload'] for x in rows}
                for row in latest:
                    if row['id'] in repairs: row['payload']=repairs[row['id']]
                seeded=current.get('source')=='ADOPTED'
                result=rebuild(latest,self.instruments[p['symbol']].base,current['original'] if seeded else 0,current['entry'] if seeded else 0)
                if result.pop('quantity')!=dec(current['quantity']): continue
                sale_values=result.pop('sells'); current.update(result,fees_estimated=True)
                for row in latest:
                    f=row['payload']
                    if row['id'] in sale_values: f.update(sale_values[row['id']])
                    db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(wire(f),ensure_ascii=False),row['id']))
                self.store.put('position',p['id'],current)
                self.store.event('fees-repaired:'+p['id']+':'+str(len(latest)),'FEE_ADJUSTMENT',{'trade_id':p['id'],'source':'ESTIMATED_MINUTE_CLOSE'},p['mode'],p['symbol'])
                if not current['fees_pending'] and dec(current['quantity'])==0:
                    memory=self.strategy.memory(p['symbol'],p['mode'],self.clock()); reset=memory.get('loss_reset_at',0)
                    closed=sorted([x for x in self.store.all('position') if x['symbol']==p['symbol'] and x['mode']==p['mode'] and x.get('closed_at',0)>reset],key=lambda x:x['closed_at'],reverse=True)
                    streak=0; unresolved=any(x.get('fees_pending') for x in closed)
                    for item in closed:
                        if item.get('fees_pending') or dec(item.get('realized_net',0))>=0: break
                        streak+=1
                    memory.update(loss_streak=streak,loss_accounting_pending=unresolved)
                    if streak>=self.settings['rules']['loss_lock_count']:
                        memory['loss_lock_until']=-1 if self.settings['rules']['loss_lock_mode']=='MANUAL' else closed[0]['closed_at']+self.settings['rules']['loss_lock_hours']*3600
                    self.strategy.save(p['symbol'],p['mode'],memory)
    def apply_fill(self,order,fill):
        # Convert a non-quote commission before entering the financial transaction.
        # Historical minute close is an explicitly estimated valuation, never a new fill.
        asset=fill.get('commissionAsset','USDT'); fee=dec(fill.get('commission',0)); base=self.instruments[order['symbol']].base
        fee_quote=fee if asset=='USDT' else ZERO if asset==base or fee==0 else None
        at=float(fill.get('time',self.clock()*1000))/1000
        if fee_quote is None:
            q=self.quotes.get(asset+'USDT',{})
            if abs(q.get('at',0)-at)<=10 and dec(q.get('price',0))>0: fee_quote=fee*dec(q['price'])
            elif order['mode']=='REAL' and self.gateway.configured:
                try:
                    minute=int(at*1000)//60000*60000; rows=self.gateway.klines(asset+'USDT','1m',1,minute,minute+59999)
                    if rows and rows[0]['open_time']==minute and rows[0]['closed']: fee_quote=fee*dec(rows[0]['close'])
                except ExchangeError: pass
        estimated=asset not in ('USDT',base) and fee_quote is not None
        fill={**fill,'fee_usdt':str(fee_quote) if fee_quote is not None else None,'fee_valuation':'ESTIMATED_MARKET_PRICE' if estimated else 'QUOTE_OR_BASE' if fee_quote is not None else 'UNKNOWN'}
        with self.store.transaction():
            key=order['mode']+':fill:'+str(fill['id'])
            if not self.store.receipt(key): return
            o=self.store.get('order',order['id']); q,p=dec(fill['qty']),dec(fill['price']); fee=dec(fill.get('commission',0)); asset=fill.get('commissionAsset','USDT'); base=self.instruments[o['symbol']].base
            if q<=0 or p<=0 or q>dec(o['remaining']): raise ValueError('Fill không hợp lệ hoặc vượt lượng còn lại.')
            at=float(fill.get('time',time.time()*1000))/1000
            first=dec(o['filled'])==0
            position=self.store.get('position',o['trade_id'])
            if o['side']=='BUY':
                if position is None:
                    m=self.strategy.memory(o['symbol'],o['mode'],at); r=self.settings['rules']
                    position=new_position(o['symbol'],0,p,o['mode'],id=o['trade_id'],managed=True,source=o['source'],opened_at=at,
                        sl_price=str(p*(1+dec(r['reentry_sl_pct'] if m.get('loss_streak',0)>0 else r['initial_sl_pct'])/100)))
                    plan=o.get('entry_plan',{})
                    for key in ('protect_enabled','e_enabled'):
                        if key in plan: position[key]=plan[key]
                    for key in ('take_profit_pct','initial_sl_pct'):
                        if key in plan: position['overrides'][key]=float(dec(plan[key]))
                    if 'initial_sl_pct' in plan:
                        position.update(sl_price='0',sl_enabled=dec(plan['initial_sl_pct'])<0)
                    if 'take_profit_pct' in plan: position['tp_enabled']=dec(plan['take_profit_pct'])>0
                old=dec(position['original']); net=q-(fee if asset==base else ZERO)
                position['entry']=str((dec(position['entry'])*old+p*net)/(old+net)); position['original']=str(old+net); position['quantity']=str(dec(position['quantity'])+net)
                position['remaining_cost_usdt']=str(dec(position.get('remaining_cost_usdt',dec(position['entry'])*old))+q*p+(fee_quote or ZERO))
                if fee_quote is None: position['fees_pending']=True
                if first and old==0: self.strategy.consume(o['symbol'],o['mode'],o['cross_id'],at)
            else:
                if not position: raise ValueError('Fill SELL thiếu vị thế liên kết.')
                reduction=q+(fee if asset==base else ZERO)
                if reduction>dec(position['quantity']): raise ValueError('SELL vượt sổ vị thế; cần đối soát.')
                gross=q*(p-dec(position['entry']))
                cost=dec(position.get('remaining_cost_usdt',dec(position['entry'])*dec(position['quantity'])))
                allocated=cost*reduction/dec(position['quantity'])
                net=q*p-allocated-(fee_quote or ZERO)
                position['remaining_cost_usdt']=str(cost-allocated)
                position['quantity']=str(dec(position['quantity'])-reduction); position['realized_gross']=str(dec(position.get('realized_gross',0))+gross)
                position['realized_net']=str(dec(position.get('realized_net',0))+net)
                if fee_quote is None: position['fees_pending']=True
                m=position.setdefault('metrics',{}); ex=o.get('exit') or {}; reason=ex.get('reason',o['reason'])
                if first:
                    if reason=='TP': m['tp_filled']=True
                    if reason=='E': m.setdefault('e',{})['filled']=int(ex['occurrence'].split(':')[-1])
                    if reason in ('ARM','DYNAMIC'):
                        track=m.setdefault(reason+':AUTO',{'count':0,'anchor':0,'peak':0}); track.update(count=track['count']+1,anchor=min(float(p),float(ex['price'])),peak=0)
                if dec(position['quantity'])==0:
                    position['closed_at']=at; self.strategy.consume(o['symbol'],o['mode'],'',at)
                    memory=self.strategy.memory(o['symbol'],o['mode'],at); pnl=dec(position.get('realized_net',0))
                    if position.get('fees_pending'): memory['loss_accounting_pending']=True
                    else:
                        memory['loss_streak']=memory.get('loss_streak',0)+1 if pnl<0 else 0
                        if memory['loss_streak']>=self.settings['rules']['loss_lock_count']:
                            memory['loss_lock_until']=-1 if self.settings['rules']['loss_lock_mode']=='MANUAL' else at+self.settings['rules']['loss_lock_hours']*3600
                    self.strategy.save(o['symbol'],o['mode'],memory)
                    capital_key=o['mode']+':'+o['symbol']; ledger=self.store.get('capital',capital_key)
                    if ledger and pnl<0 and not self.settings.get('priority',{}).get(o['symbol']):
                        ledger['ceiling']=str(max(ZERO,dec(ledger['ceiling'])+pnl)); self.store.put('capital',capital_key,ledger)
                if o['source']=='MANUAL': self.store.put('pause',o['mode'],at+self.settings['manual_sell_pause_minutes']*60)
                fill={**fill,'gross_pnl':str(gross),'net_pnl':str(net) if fee_quote is not None and not position.get('fees_pending') else None}
            if estimated: position['fees_estimated']=True
            position['fees'][asset]=str(dec(position['fees'].get(asset,0))+fee)
            o.update(filled=str(dec(o['filled'])+q),remaining=str(dec(o['remaining'])-q),notional=str(dec(o['notional'])+q*p),updated_at=time.time())
            if dec(o['remaining'])==0: o['status']='FILLED'
            if o['mode']=='PAPER':
                cash=self.balance('PAPER'); cash+=q*p-fee if o['side']=='SELL' else -(q*p+fee); self.store.put('paper','cash',str(cash))
            self.store.put('position',position['id'],position); self.save_order(o)
            self.store.event(key,'FILL',{**fill,'order_id':o['id'],'trade_id':o['trade_id'],'side':o['side'],'symbol':o['symbol']},o['mode'],o['symbol'],at)
        self.notify(key,'orders',o['symbol'],f'{o["mode"]} {o["side"]} KHỚP {q} @ {p} · {o["reason"]}')
