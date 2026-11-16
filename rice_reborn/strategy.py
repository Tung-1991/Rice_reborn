"""One strategy implementation shared by observation, LIVE, PAPER and replay."""
from datetime import datetime,timedelta,timezone
from decimal import ROUND_HALF_UP
import time,math
from .maths import ema,sma,rsi,pivot_points,protect_level
from .models import DAY,dec

def rounded(value): return float(dec(value).quantize(dec('.01'),rounding=ROUND_HALF_UP))

def indicators(rows,r,instrument):
    prices=[float(x['close']) for x in rows]
    required=max(r['buy_ema_slow'],r['sell_ema_slow'],r['rsi_period']+2,30)
    if len(prices)<required or any(not math.isfinite(x) or x<=0 for x in prices): return None
    values={}
    for prefix in ('buy','sell'):
        for part in ('fast','slow'):
            series=ema(prices,r[f'{prefix}_ema_{part}'])
            values[f'{prefix}_{part}']=instrument.indicator(series[-1])
            values[f'{prefix}_{part}_previous']=instrument.indicator(series[-2])
    strength=rsi(prices,r['rsi_period'])
    values.update(rsi=rounded(strength[-1]),rsi_previous=rounded(strength[-2]),price=prices[-1])
    closed=[x for x in rows if x.get('closed',True)]; ranges=[]; previous=0
    for x in closed:
        ranges.append(max(float(x['high'])-float(x['low']),abs(float(x['high'])-previous) if previous else 0,abs(float(x['low'])-previous) if previous else 0)); previous=float(x['close'])
    atr=sum(ranges[:14])/14 if len(ranges)>=14 else 0
    for x in ranges[14:]: atr+=(x-atr)/14
    values['atr']=instrument.indicator(atr); values['atr_pct']=rounded(atr/previous*100) if previous else 0
    values['ma200']=instrument.indicator(sum(prices[-r['ma_period']:])/r['ma_period']) if len(prices)>=r['ma_period'] else None
    fast=ema(prices,r['buy_ema_fast']); slow=ema(prices,r['buy_ema_slow'])
    count=0
    for i in range(max(1,len(prices)-r['whipsaw_x']+1),len(prices)):
        a,b=instrument.indicator(fast[i-1])-instrument.indicator(slow[i-1]),instrument.indicator(fast[i])-instrument.indicator(slow[i])
        count+=int((a<=0<b) or (a>=0>b))
    values['whipsaw']=count
    history=[float(x['volume']) for x in rows[-r['buy_volume_average_sessions']-1:-1]]
    values['volume_ratio']=rounded(float(rows[-1]['volume'])/(sum(history)/len(history))) if history and sum(history)>0 else None
    return values

def market_context(rows,r,previous='TRANSITION'):
    if len(rows)<r['ma_period']: return {'state':'NO_DATA','ma':None,'reason':'Thiếu nến nền MA200'}
    close=float(rows[-1]['close']); ma=sum(float(x['close']) for x in rows[-r['ma_period']:])/r['ma_period']
    highs,lows=pivot_points(rows,r['pivot_left'],r['pivot_right']); state=previous
    if len(highs)>=2 and len(lows)>=2:
        h0,h1=highs[-2][1],highs[-1][1]; l0,l1=lows[-2][1],lows[-1][1]; tol=r['pivot_horizontal_pct']/100
        if h1>h0 and l1>l0 and close>=ma*(1-r['ma_zone_pct']/100): state='UPTREND'
        elif h1<h0 and l1<l0 and close<=ma*(1+r['ma_zone_pct']/100): state='DOWNTREND'
        elif previous=='DOWNTREND' and l1>=l0*(1-tol) and (abs(h1/h0-1)<=tol or abs(l1/l0-1)<=tol): state='ACCUMULATION'
        elif previous=='UPTREND' and h1<=h0*(1+tol) and (abs(h1/h0-1)<=tol or abs(l1/l0-1)<=tol): state='DISTRIBUTION'
    volumes=[float(x['volume']) for x in rows[-r['volume_average_sessions']-1:-1]]
    ratio=float(rows[-1]['volume'])/(sum(volumes)/len(volumes)) if volumes and sum(volumes)>0 else None
    return {'state':state,'ma':ma,'price':close,'highs':highs[-2:],'lows':lows[-2:],'volume_ratio':ratio if r['volume_confirmation'] else None}

class Strategy:
    def __init__(self,store): self.store=store
    def memory(self,name,mode,now):
        return self.store.get('memory',mode+':'+name,{'admitted':now,'cross':None,'counts':{},'used':[],'attempted':[], 'pair':None})
    def save(self,name,mode,state): self.store.put('memory',mode+':'+name,state)
    def recover(self,name,mode,daily,minutes,r,instrument,now):
        """Reconstruct minute observations using the prior daily EMA seed, never minute EMA."""
        state=self.memory(name,mode,now); pair=[r['buy_ema_fast'],r['buy_ema_slow']]
        start=max(int(state.get('reset_at',0)*1000),int(state['admitted']*1000) if not r['buy_ema_va_lookback_enabled'] and r['buy_ema_cross_mode']=='VA' else (int(now*1000)//DAY-r['buy_ema_cross_sessions']+1)*DAY)
        counts={}; cross=state.get('cross') if state.get('pair')==pair else None; previous=None; last_day=None
        for row in minutes:
            at=int(row['open_time']); day=at//DAY*DAY
            if day!=last_day:
                base=[float(x['close']) for x in daily if int(x['open_time'])<day]
                if len(base)<max(pair): continue
                seeds=[ema(base,p)[-1] for p in pair]; last_day=day
                previous=[instrument.indicator(v) for v in seeds]
            values=[instrument.indicator(float(row['close'])*2/(p+1)+seed*(1-2/(p+1))) for p,seed in zip(pair,seeds)]
            if previous and at>=start:
                up=previous[0]<=previous[1] and values[0]>values[1]; down=previous[0]>=previous[1] and values[0]<values[1]
                if up or down:
                    key=str(day); counts[key]=counts.get(key,0)+1
                    if up: cross={'id':f'{name}:{pair}:{at}:UP','at':at/1000,'source':'1M_REPLAY'}
            previous=values
        state.update(pair=pair,counts=counts,cross=cross,history_ready=bool(minutes),history_at=now)
        if previous and (not state.get('sample_at') or state['sample_at']<=minutes[-1]['open_time']/1000): state['previous']=previous
        self.save(name,mode,state)
    def observe(self,name,mode,rows,r,instrument,now=None,override=False,max_gap=310):
        now=now or time.time(); state=self.memory(name,mode,now); marks=indicators(rows,r,instrument)
        if marks is None: return {'symbol':name,'mode':mode,'action':'WAIT','reason':'THIẾU NẾN','ready':False}
        pair=[r['buy_ema_fast'],r['buy_ema_slow']]
        if state.get('pair')!=pair:
            state.update(pair=pair,cross=None,previous=None,history_ready=False,counts={})
        current=[marks['buy_fast'],marks['buy_slow']]
        closed=rows[-1].get('closed',True)
        previous=state.get('previous') or ([marks['buy_fast_previous'],marks['buy_slow_previous']] if closed else current)
        up=previous[0]<=previous[1] and current[0]>current[1]; down=previous[0]>=previous[1] and current[0]<current[1]
        # Repeated CLOSED scans or restarting the same observation cannot create a new cross.
        observation=rows[-1]['open_time'] if rows[-1].get('closed',True) else int(now*1000)
        if (up or down) and state.get('last_observation')!=observation:
            day=str(int(now*1000)//DAY*DAY); state['counts'][day]=state['counts'].get(day,0)+1
            if up:
                at=now if not rows[-1].get('closed',True) else (rows[-1].get('close_time') or observation+DAY-1)/1000
                earliest=max(state.get('reset_at',0),state['admitted'] if r['buy_ema_cross_mode']=='VA' and not r['buy_ema_va_lookback_enabled'] else 0)
                if at>=earliest: state['cross']={'id':f'{name}:{pair}:{int(at*1000)}:UP','at':at,'source':'LIVE' if not closed else 'CLOSED'}
        last_sample=state.get('sample_at',0)
        state['previous']=current; state['last_observation']=observation; state['sample_at']=now
        cross=state.get('cross'); valid=bool(cross and cross['id'] not in state['used'] and marks['buy_fast']>marks['buy_slow'])
        if valid and r['buy_ema_cross_mode']=='SESSIONS': valid=int(cross['at']*1000)//DAY>=int(now*1000)//DAY-r['buy_ema_cross_sessions']+1
        buy_ema=not r['buy_signal_use_ema'] or (marks['buy_fast']>marks['buy_slow'] and (valid or override or not r['buy_signal_require_ema_cross']))
        buy_rsi=not r['buy_signal_use_rsi'] or marks['rsi']>marks['rsi_previous']
        volume=not r['buy_volume_enabled'] or (marks['volume_ratio'] is not None and marks['volume_ratio']>=r['buy_volume_min_ratio'])
        sell_ema=not r['sell_signal_use_ema'] or marks['sell_fast']<marks['sell_slow']
        if r['sell_signal_require_ema_cross'] and r['sell_signal_use_ema']:
            old=state.get('sell_previous',[marks['sell_fast_previous'],marks['sell_slow_previous']]); sell_ema=sell_ema and old[0]>=old[1]
        state['sell_previous']=[marks['sell_fast'],marks['sell_slow']]
        sell=sell_ema and (not r['sell_signal_use_rsi'] or marks['rsi']<marks['rsi_previous'])
        blocked=[]
        if not buy_ema: blocked.append('CHỜ EMA CẮT LÊN' if r['buy_signal_require_ema_cross'] else 'EMA CHƯA ĐẠT')
        if not buy_rsi: blocked.append('RSI CHƯA ĐẠT')
        if not volume: blocked.append('VOLUME CHƯA ĐẠT')
        reset=state.get('whipsaw_reset')
        whip=marks['whipsaw']
        if reset and reset.get('pair')==pair:
            post=[x for x in rows if int(x['open_time'])>reset['day']]
            f=ema([float(x['close']) for x in rows],pair[0]); s=ema([float(x['close']) for x in rows],pair[1]); whip=0
            for i in range(max(1,len(rows)-r['whipsaw_x']+1),len(rows)):
                if int(rows[i]['open_time'])<=reset['day']: continue
                a=instrument.indicator(f[i-1])-instrument.indicator(s[i-1]); b=instrument.indicator(f[i])-instrument.indicator(s[i]); whip+=int((a<=0<b) or (a>=0>b))
        marks['whipsaw']=whip
        if r['whipsaw_enabled'] and r['buy_signal_use_ema'] and whip>=r['whipsaw_n']: blocked.append('KHÓA WHIPSAW')
        lock=state.get('loss_lock_until',0)
        if lock==-1 or lock>now: blocked.append('KHÓA CHUỖI LỖ')
        if r['buy_window_enabled']:
            clock=datetime.fromtimestamp(now,timezone(timedelta(hours=7))).strftime('%H:%M'); a,b=r['buy_window_start'],r['buy_window_end']
            inside=a<=clock<=b if a<=b else clock>=a or clock<=b
            if not inside: blocked.append('CHỜ KHUNG GIỜ BUY')
        confirmed=False
        if r['buy_confirmation_enabled']:
            signature=[r['buy_confirmation_minutes'],r['buy_confirmation_require_ema'],r['buy_confirmation_require_rsi']]
            candidate=state.get('candidate',{})
            # An unobserved gap cannot count as time holding a condition.
            gap=now-last_sample if last_sample else 0
            if candidate.get('signature')!=signature or gap>max_gap: candidate={}
            raw=buy_ema and buy_rsi and volume
            if raw and not candidate: candidate={'at':now,'signature':signature}
            selected=(not r['buy_confirmation_require_ema'] or marks['buy_fast']>marks['buy_slow']) and (not r['buy_confirmation_require_rsi'] or marks['rsi']>marks['rsi_previous'])
            if candidate and not selected:
                candidate={}; blocked.append('ĐIỀU KIỆN XÁC NHẬN BUY CHƯA ĐẠT')
            if candidate:
                confirmed=now-candidate['at']>=r['buy_confirmation_minutes']*60
                if confirmed:
                    blocked=[x for x in blocked if x not in ('CHỜ EMA CẮT LÊN','EMA CHƯA ĐẠT','RSI CHƯA ĐẠT')]
                else: blocked.append('CHỜ XÁC NHẬN BUY')
            state['candidate']=candidate
        else: state.pop('candidate',None)
        self.save(name,mode,state)
        technical=(buy_ema and buy_rsi and volume) or (confirmed and volume)
        return {'symbol':name,'mode':mode,'action':'BUY' if technical and not blocked else 'WAIT','technical_buy':technical,'sell_condition':sell,
                'reason':' · '.join(blocked) or 'EMA/RSI ĐẠT','ready':True,'marks':marks,'cross':cross,'ema_valid':valid,
                'cross_count':sum(v for k,v in state['counts'].items() if int(k)//DAY>=int(now*1000)//DAY-r['buy_ema_cross_sessions']+1),
                'loss_streak':state.get('loss_streak',0),'loss_lock_until':lock,'observed_at':now,'override':override}
    def position(self,position,decision,r,instrument,price,now=None):
        now=now or time.time(); rules={**r,**position.get('overrides',{})}; m=position.setdefault('metrics',{})
        if not position.get('cost_known',True) or dec(position['entry'])<=0: return None
        entry=float(position['entry']); profit=(float(price)/entry-1)*100
        m['mfe']=max(m.get('mfe',0),profit); m['mae']=min(m.get('mae',0),profit); m['price']=str(price)
        sl_override=dec(position.get('sl_price') or 0); tp_override=dec(position.get('tp_price') or 0)
        sl=float(sl_override) if sl_override>0 else entry*(1+rules['initial_sl_pct']/100)
        tp=float(tp_override) if tp_override>0 else entry*(1+rules['take_profit_pct']/100)
        known=decision.get('ready',False)
        condition=bool(decision.get('sell_condition')) if known else False; e=m.setdefault('e',{'active':False,'episode':0,'filled':0})
        if known:
            if condition and not e['active']: e['episode']+=1
            e['active']=condition
        candidates=[]
        def add(reason,pct,basis,policy,trigger,key): candidates.append({'reason':reason,'pct':pct,'basis':basis,'policy':policy,'trigger':trigger,'occurrence':key,'trade_id':position['id'],'symbol':position['symbol'],'mode':position['mode'],'at':now,'price':str(price),'marks':dict(decision.get('marks',{}))})
        if position.get('sl_enabled',True) and float(price)<=float(sl): add('SL',100,'REMAINING','AUTO',sl,'SL')
        if position.get('tp_enabled',True) and not m.get('tp_filled') and float(price)>=float(tp): add('TP',rules['take_profit_sell_pct'],'ORIGINAL','AUTO',tp,'TP')
        if position.get('e_enabled',True) and condition and (e['filled']<e['episode'] if rules['indicator_exit_repeat_enabled'] else not e['filled']):
            add('E',rules['indicator_exit_sell_pct'],rules['indicator_exit_sell_basis'],rules['indicator_exit_policy'],price,'E:'+str(e['episode']))
        if position.get('protect_enabled',True):
            level=protect_level(entry,m['mfe'],rules['normal_arm_pct'],rules['normal_giveback_pct'],dynamic_enabled=rules['normal_dynamic_enabled'],sl_price=float(sl),
                atr_pct=decision.get('marks',{}).get('atr_pct',0),atr_multiplier=rules['normal_atr_multiplier'],atr_activation_multiplier=rules['normal_atr_activation_multiplier'],
                atr_activation_enabled=rules['normal_atr_activation_enabled'],atr_trail_enabled=rules['normal_atr_trail_enabled'],retention_pct=rules['normal_retention_pct'],
                retention_until_pct=rules['normal_retention_until_pct'],retention_enabled=rules['normal_retention_enabled'],retention_until_enabled=rules['normal_retention_until_enabled'],previous_trigger_price=m.get('dynamic_floor',0))
            stage='ARM' if m['mfe']>=rules['normal_arm_pct'] else 'DYNAMIC'; prefix='normal' if stage=='ARM' else 'dynamic'; policy=rules[prefix+'_policy']
            track=m.setdefault(stage+':'+policy,{'count':0,'anchor':0,'peak':0})
            threshold=level.trigger_price
            if stage=='DYNAMIC': m['dynamic_floor']=max(m.get('dynamic_floor',0),threshold)
            if track['count']:
                if stage=='ARM':
                    if track['peak'] or (float(price)>track['anchor'] and profit>=rules['normal_arm_pct']): track['peak']=max(track['peak'],float(price))
                    threshold=max(track['anchor'],track['peak'])-entry*rules['normal_giveback_pct']/100
                else: threshold=min(threshold,track['anchor']*(1-rules['normal_giveback_pct']/100))
                if not rules[prefix+'_repeat_enabled']: threshold=0
                if stage=='ARM' and not rules['normal_repeat_below_arm_enabled'] and profit<rules['normal_arm_pct']: threshold=0
            m['protect']={'stage':stage,'policy':policy,'trigger':threshold,'active':level.active,'count':track['count'],'mfe':m['mfe']}
            if level.active and threshold>0 and entry<float(price)<=threshold:
                add(stage,rules[prefix+'_sell_pct'],rules[prefix+'_sell_basis'],policy,threshold,stage+':'+policy+':'+str(track['count']+1))
        priorities={'SL':5,'E':3,'ARM':1,'DYNAMIC':1,'TP':4 if rules['take_profit_sell_pct']>=100 else 2}
        return max(candidates,key=lambda x:priorities[x['reason']],default=None)
    def consume(self,name,mode,cross_id,now):
        s=self.memory(name,mode,now)
        if cross_id and cross_id not in s['used']: s['used'].append(cross_id)
        s.update(reset_at=now,cross=None); self.save(name,mode,s)
