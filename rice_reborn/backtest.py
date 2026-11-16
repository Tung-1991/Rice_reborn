"""Isolated, cancellable backtest worker. It has no live credentials or notification path."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import argparse,json,os,subprocess,sys,time,uuid
from .config import validate_settings,RULE_VERSION
from .models import DAY,dec,wire
from .exchange import Binance
from .store import Store
from .strategy import Strategy,market_context
from .execution import Execution,ACTIVE
from .portfolio import buy_budget,entry_block

def atomic(path,value):
    temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(wire(value),ensure_ascii=False,allow_nan=False),encoding='utf-8'); os.replace(temp,path)

def job_status(data):
    directory=Path(data)/'backtests'
    if not directory.exists(): return []
    rows=[]
    for p in sorted(directory.glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
        try:
            value=json.loads(p.read_text(encoding='utf-8')); value.pop('settings',None); rows.append(value)
        except (OSError,ValueError): continue
    return rows

def start_job(service,options):
    if any(j['status'] in ('QUEUED','RUNNING') for j in service.job_status()): raise ValueError('Một backtest đang chạy; chờ hoàn tất hoặc hủy.')
    names=options.get('symbols',service.settings['watchlist'])
    from .config import symbol
    names=list(dict.fromkeys(symbol(x) for x in (names.split(',') if isinstance(names,str) else names)))
    if not 1<=len(names)<=20: raise ValueError('Mỗi lượt backtest từ 1 đến 20 cặp.')
    start=datetime.strptime(options['start'],'%Y-%m-%d').replace(tzinfo=timezone.utc); end=datetime.strptime(options['end'],'%Y-%m-%d').replace(tzinfo=timezone.utc)
    if end<start or (end-start).days>3660 or end.timestamp()>=int(time.time())//86400*86400: raise ValueError('Khoảng ngày không hợp lệ; kết thúc trước hôm nay UTC, tối đa 10 năm.')
    settings=deepcopy(service.settings)
    if any(v['max_orders']>1 for v in settings['priority'].values()): raise ValueError('Backtest như MayinTien chỉ hỗ trợ MAX LỆNH=1.')
    capital=dec(options.get('capital',settings['paper_capital']))
    if capital<=0: raise ValueError('Vốn mô phỏng phải dương.')
    simulation=options.get('simulation','DAILY')
    if simulation not in ('DAILY','REPLAY'): raise ValueError('Mô phỏng phải DAILY hoặc REPLAY.')
    if simulation=='DAILY': settings['signal_mode']='CLOSED'
    settings['paper_capital']=str(capital); settings['watchlist']=names
    settings['priority']={k:v for k,v in settings['priority'].items() if k in names}
    jobid=uuid.uuid4().hex; directory=service.env.data/'backtests'/jobid; directory.mkdir(parents=True)
    job={'id':jobid,'status':'QUEUED','symbols':names,'start':options['start'],'end':options['end'],'simulation':simulation,'capital':str(capital),
         'progress':0,'created_at':time.time(),'rule_version':RULE_VERSION,'message':'Đang chuẩn bị dữ liệu','settings':settings}
    atomic(directory/'job.json',job)
    env={k:v for k,v in os.environ.items() if not k.startswith(('BINANCE_','TELEGRAM_','DNSE_','RICE_'))}
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    log=open(directory/'worker.log','ab')
    try: process=subprocess.Popen([sys.executable,'-m','rice_reborn.backtest',str(directory/'job.json')],env=env,stdout=log,stderr=log,**flags)
    finally: log.close()
    service.jobs[jobid]=process
    return job

def cancel_job(service,jobid):
    path=service.env.data/'backtests'/jobid/'cancel'
    if not path.parent.exists(): raise ValueError('Job không tồn tại.')
    path.touch()

def download(gateway,name,interval,start,end,cancel=None):
    at=start
    while at<=end:
        if cancel is not None and cancel.exists(): raise InterruptedError('Đã hủy theo yêu cầu.')
        rows=gateway.klines(name,interval,1000,at,end)
        if not rows: break
        for row in rows:
            if start<=row['open_time']<=end: yield row
        nxt=rows[-1]['close_time']+1
        if nxt<=at: break
        at=nxt

def simulate(job,directory,gateway=None):
    gateway=gateway or Binance(); settings=validate_settings(job['settings']); r=settings['rules']
    if job['simulation']=='DAILY' and r['buy_confirmation_enabled']: raise ValueError('Dùng REPLAY phút để kiểm chứng chờ xác nhận BUY.')
    instruments=gateway.instruments(); names=job['symbols']
    if any(n not in instruments for n in names): raise ValueError('Có cặp không còn TRADING; chưa có metadata lịch sử để mô phỏng.')
    start=int(datetime.strptime(job['start'],'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)
    end=int(datetime.strptime(job['end'],'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)+DAY-1
    store=Store(directory/'simulation.sqlite3'); strategy=Strategy(store); clock=[start/1000]
    ex=Execution(store,Binance(),strategy,clock=lambda:clock[0]); ex.settings=settings; ex.instruments=instruments; ex.bot['PAPER']=True
    store.put('paper','cash',job['capital']); contexts={}; processed=0; total=0
    # One source iterator per symbol; merge by time without loading all minute history.
    import heapq
    sources={}; daily={}; heap=[]
    context_bars=list(download(gateway,'BTCUSDT','1d',start-300*DAY,end,directory/'cancel'))
    for name in names:
        daily[name]=list(download(gateway,name,'1d',start-300*DAY,end,directory/'cancel'))
        if len([x for x in daily[name] if x['open_time']<start])<max(50,r['ma_period']): raise ValueError(name+': thiếu nến warmup.')
        interval='1m' if job['simulation']=='REPLAY' else '1d'; it=iter(download(gateway,name,interval,start,end,directory/'cancel')); sources[name]=it
        first=next(it,None)
        if first: heapq.heappush(heap,(first['open_time'],name,first))
    observations={}; partial={}; pending_decisions={}; recorded_days=set()
    while heap:
        at,name,row=heapq.heappop(heap); clock[0]=at/1000
        if processed%100==0:
            if (directory/'cancel').exists(): raise InterruptedError('Đã hủy theo yêu cầu.')
            job.update(status='RUNNING',progress=round((at-start)/max(1,end-start)*100,1),message=f'{name} · {datetime.fromtimestamp(at/1000,timezone.utc):%Y-%m-%d %H:%M}')
            atomic(directory/'job.json',job)
        processed+=1; ex.quotes[name]={'price':str(row['open']),'at':clock[0],'source':'REPLAY'}
        # Entry intents created on the preceding observation execute at the next observed open.
        ex.process(pending_decisions)
        day=at//DAY*DAY
        base=[x for x in daily[name] if x['open_time']<day]
        if job['simulation']=='DAILY': working=[{**row,'closed':True}]; observation_at=(row['close_time'] or at+DAY-1)/1000
        else:
            p=partial.get(name)
            if not p or p['open_time']!=day: p={**row,'open_time':day,'closed':False,'volume':0}
            p.update(high=max(p['high'],row['high']),low=min(p['low'],row['low']),close=row['close'],volume=p['volume']+row['volume']); partial[name]=p
            working=[dict(p)]; observation_at=(at+59999)/1000
        clock[0]=observation_at
        ex.quotes[name]={'price':str(row['close']),'at':clock[0],'source':'REPLAY'}
        observed=base if job['simulation']=='REPLAY' and settings['signal_mode']=='CLOSED' else base+working
        d=strategy.observe(name,'PAPER',observed[-300:],r,instruments[name],clock[0],max_gap=70 if job['simulation']=='REPLAY' else 310); observations[name]=d
        for p in ex.positions('PAPER'):
            if p['symbol']!=name: continue
            # Conservative OHLC path; it cannot reconstruct intra-minute tick order.
            for price in (row['open'],row['low'],row['high'],row['close']):
                ex.quotes[name]={'price':str(price),'at':clock[0],'source':'REPLAY'}
                exit_=strategy.position(p,d,r,instruments[name],price,clock[0]); store.put('position',p['id'],p)
                if not exit_: continue
                pending_decisions[p['id']]=exit_
                if exit_['policy']=='ALERT':
                    if exit_['reason'] in ('ARM','DYNAMIC'):
                        track=p['metrics'][exit_['reason']+':ALERT']; track.update(count=track['count']+1,anchor=float(price),peak=0); store.put('position',p['id'],p)
                    continue
                if any(o['trade_id']==p['id'] and o['status'] in ACTIVE and o['side']=='SELL' for o in ex.orders()): continue
                qty=instruments[name].rounded(min(dec(p['quantity']),dec(p['original'] if exit_['basis']=='ORIGINAL' else p['quantity'])*dec(exit_['pct'])/100),True)
                try:
                    ex.create({'mode':'PAPER','symbol':name,'side':'SELL','quantity':str(qty),'trade_id':p['id'],'reason':exit_['reason'],'exit':exit_,'request_id':p['id']+exit_['occurrence']},'BOT')
                    ex.process(pending_decisions); p=store.get('position',p['id'])
                    if dec(p['quantity'])==0: break
                except ValueError: pass  # Dust stays held and visible in the final result.
        ex.quotes[name]={'price':str(row['close']),'at':clock[0],'source':'REPLAY'}
        state=strategy.memory(name,'PAPER',clock[0]); cross=(d.get('cross') or {}).get('id')
        if d.get('action')=='BUY' and cross not in state['attempted'] and not any(p['symbol']==name for p in ex.positions()) and not any(o['symbol']==name and o['status'] in ACTIVE for o in ex.orders()):
            btc=market_context([x for x in context_bars if x['open_time']<day][-300:],r)
            ledger=store.get('capital','PAPER:'+name,{})
            budget=buy_budget(settings,'PAPER',name,ex.positions(),ex.orders(),ex.available('PAPER'),btc['state'],ledger)
            if not entry_block(settings,name,ex.positions(),ex.orders()):
                q=instruments[name].rounded(budget/(dec(row['close'])*dec('1.002')),True)
                try:
                    ex.create({'mode':'PAPER','symbol':name,'side':'BUY','quantity':str(q),'reason':'VA_ENTRY','request_id':'entry:'+name+':'+str(cross or day)},'BOT',d)
                    if not ledger: store.put('capital','PAPER:'+name,{'ceiling':str(budget)})
                except ValueError: pass
        if (name,day) not in recorded_days or d.get('action')=='BUY':
            store.event(f'observe:{name}:{at}','OBSERVATION',d,'PAPER',name,clock[0]); recorded_days.add((name,day))
        nxt=next(sources[name],None)
        if nxt: heapq.heappush(heap,(nxt['open_time'],name,nxt))
    fills=list(reversed(store.history('FILL',limit=500)))
    # Export full history, not the bounded UI page.
    with store.transaction() as db:
        fills=[{**dict(x),'payload':json.loads(x['payload'])} for x in db.execute('SELECT * FROM events WHERE kind="FILL" ORDER BY seq')]
    outputs=[]
    for name in names:
        path=directory/f'{name}_BACKTEST_{job["start"]}_{job["end"]}.xlsx'; export_excel(path,name,fills,job,store); outputs.append(path.name)
    positions=ex.positions(); nav=ex.balance('PAPER')+sum((dec(p['quantity'])*dec(ex.quotes[p['symbol']]['price']) for p in positions),dec(0))
    return {'fills':len(fills),'ending_nav':str(nav),'pnl':str(nav-dec(job['capital'])),'open_positions':wire(positions),'files':outputs,'observations':processed,
            'assumptions':'PAPER fee 0.1% USDT, slippage 0.05%; DAILY/1M OHLC conservative path, no tick reconstruction; không chứng minh giá khớp LIVE.'}

def export_excel(path,name,fills,job,store):
    from openpyxl import load_workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.utils import get_column_letter
    from datetime import timedelta
    wb=load_workbook(Path(__file__).parent/'templates'/'backtest.xlsx'); ws=wb.active; ws.title=name[:31]
    ws.delete_rows(2,ws.max_row); ws.delete_cols(30,1)
    r=job['settings']['rules']
    headers=['LƯỢT','MÃ','VÀO','GIÁ VÀO','KHỐI LƯỢNG','VỐN USDT','CẮT LỖ',f'EMA{r["buy_ema_fast"]}/{r["buy_ema_slow"]} VÀO','RSI VÀO','RA','NGÀY GIỮ','THOÁT BỞI',f'EMA{r["sell_ema_fast"]}/{r["sell_ema_slow"]} RA','RSI RA','MAE %','MFE %','ATR14 1D %','START MFE %','TRAIL ATR %','ĐỈNH LÚC TÍNH','SÀN GIỮ LÃI','SÀN PROTECT','ARM %','TRẠNG THÁI ARM','TRAIL %','PHÍ BINANCE USDT','PNL CHỐT USDT','%','KẾT QUẢ']
    for col,value in enumerate(headers,1): ws.cell(1,col,value)
    def stamp(at): return datetime.fromtimestamp(at,timezone.utc).astimezone(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S') if at else None
    trades=[p for p in store.all('position') if p['symbol']==name]; orders=store.all('order')
    for index,p in enumerate(trades,1):
        bought=[o for o in orders if o['trade_id']==p['id'] and o['side']=='BUY']; sold=[o for o in orders if o['trade_id']==p['id'] and o['side']=='SELL' and dec(o['filled'])>0]
        entry=(bought[0].get('decision') or {}).get('marks',{}) if bought else {}
        last=sold[-1] if sold else {}; exit_=(last.get('exit') or {}).get('marks',{}); m=p.get('metrics',{}); protect=m.get('protect',{})
        pnl=dec(p.get('realized_net',0)) if not p.get('fees_pending') else None
        spent=sum((dec(o['notional']) for o in bought),dec(0)); fees=dec(p['fees'].get('USDT',0)) if not p.get('fees_pending') else None
        end_at=p.get('closed_at') or datetime.strptime(job['end'],'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()+86400
        ws.append([index,name,stamp(p['opened_at']),float(p['entry']),float(p['original']),float(spent),float(p.get('sl_price',0)),
            f'{entry.get("buy_fast","—")} / {entry.get("buy_slow","—")}',entry.get('rsi'),stamp(p.get('closed_at')),round((end_at-p['opened_at'])/86400,2),
            ', '.join(dict.fromkeys(o['reason'] for o in sold)),f'{exit_.get("sell_fast","—")} / {exit_.get("sell_slow","—")}',exit_.get('rsi'),m.get('mae'),m.get('mfe'),
            exit_.get('atr_pct',entry.get('atr_pct')),r['normal_atr_activation_multiplier']*exit_.get('atr_pct',entry.get('atr_pct',0)),r['normal_atr_multiplier']*exit_.get('atr_pct',entry.get('atr_pct',0)),
            float(p['entry'])*(1+m.get('mfe',0)/100),None,protect.get('trigger'),r['normal_arm_pct'],protect.get('stage'),r['normal_giveback_pct'],
            float(fees) if fees is not None else None,float(pnl) if pnl is not None else None,float(pnl/spent*100) if pnl is not None and spent else None,'ĐÃ ĐÓNG' if dec(p['quantity'])==0 else 'CÒN GIỮ'])
    last_trade=ws.max_row; ws.append([]); ws.append(['INFO','KẾT QUẢ'])
    for label,value in [('Kỳ test',job['start']+' → '+job['end']),('Nguồn test','Binance '+job['simulation']+' · '+RULE_VERSION),('Vốn test',job['capital']+' USDT'),('Lệnh mua/bán',str(sum(1 for f in fills if f['symbol']==name))),('Giả định','Fee 0.1% USDT, slippage 0.05%; OHLC bảo thủ; không tái dựng tick')]: ws.append([label,value])
    ws.append([]); ws.append(['NGÀY','GIỜ','MỐC THỰC TẾ','GIÁ',f'EMA{r["buy_ema_fast"]}',f'EMA{r["buy_ema_slow"]}','RSI','RSI TRƯỚC','NGÀY RSI TRƯỚC','WHIPSAW','BOT','LÝ DO'])
    seen=set()
    with store.transaction() as db:
        for row in db.execute('SELECT at,payload FROM events WHERE kind="OBSERVATION" AND symbol=? ORDER BY at',(name,)):
            d=json.loads(row['payload']); day=int(row['at'])//86400
            if day in seen and d.get('action')!='BUY': continue
            seen.add(day); marks=d.get('marks',{}); local=datetime.fromtimestamp(row['at'],timezone.utc).astimezone(timezone(timedelta(hours=7)))
            ws.append([local.strftime('%Y-%m-%d'),local.strftime('%H:%M:%S'),'KIỂM TRA NGÀY' if d.get('action')!='BUY' else 'ENTRY',marks.get('price'),marks.get('buy_fast'),marks.get('buy_slow'),marks.get('rsi'),marks.get('rsi_previous'),datetime.fromtimestamp(day*86400-86400,timezone.utc).strftime('%Y-%m-%d'),marks.get('whipsaw'),d.get('action'),d.get('reason')])
    for cell in ws[1]: cell.fill=PatternFill('solid',fgColor='172C3B'); cell.font=Font(color='FFFFFF',bold=True)
    ws.freeze_panes='C2'; ws.auto_filter.ref=f'A1:AC{last_trade}'
    for index in range(1,30): ws.column_dimensions[get_column_letter(index)].width=24 if index in (3,10) else 20 if index in (8,12,13,29) else 15
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if isinstance(cell.value,(int,float)): cell.number_format='#,##0.########'
    ws.sheet_properties.pageSetUpPr.fitToPage=True
    ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=ws.PAPERSIZE_A3; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
    wb.save(path)


def main():
    p=argparse.ArgumentParser(); p.add_argument('job'); args=p.parse_args(); path=Path(args.job).resolve(); job=json.loads(path.read_text(encoding='utf-8'))
    try:
        if os.name!='nt':
            os.nice(10)
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
        job.update(status='RUNNING'); atomic(path,job)
        job['result']=simulate(job,path.parent); job.update(status='COMPLETE',progress=100,message='Hoàn tất')
    except InterruptedError as exc: job.update(status='CANCELED',message=str(exc))
    except Exception as exc: job.update(status='FAILED',message=str(exc))
    finally: job['finished_at']=time.time(); atomic(path,job)

if __name__=='__main__': main()
