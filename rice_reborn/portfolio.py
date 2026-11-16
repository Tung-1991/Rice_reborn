"""The same sizing and slot rules serve preview, the bot and backtests."""
from .models import dec,ZERO
from .execution import ACTIVE

def committed(position): return dec(position.get('remaining_cost_usdt',dec(position['entry'])*dec(position['quantity'])))

def entry_block(settings,name,positions,orders):
    active=[o for o in orders if o['status'] in ACTIVE]
    priority=settings['priority'].get(name,{})
    if priority.get('mode')=='ALERT': return 'PRIORITY ALERT'
    if any(o['symbol']==name for o in active): return 'ĐANG CÓ LỆNH CÙNG CẶP'
    own=[p for p in positions if p['symbol']==name]
    if len(own)>=priority.get('max_orders',1): return 'ĐỦ MAX LƯỢT BUY'
    occupied={p['symbol'] for p in positions}|{o['symbol'] for o in active if o['side']=='BUY'}
    reserved={s for s,v in settings['priority'].items() if v['mode']=='AUTO' and dec(v['cap'])>0}
    if name not in occupied and len(occupied|reserved)>=settings['rules']['max_positions'] and name not in reserved: return 'HẾT SLOT BUY'
    return ''

def buy_budget(settings,mode,name,positions,orders,available,market_state,ledger=None):
    r=settings['rules']; capital=dec(settings['paper_capital'] if mode=='PAPER' else settings['capital_usdt'])
    exposure=dec(settings['market_exposure_pct'])/100 if settings['market_override'] else dec(r['exposure'].get(market_state,0))
    pending=sum((dec(o['remaining'])*dec(o['reserve_price']) for o in orders if o['side']=='BUY' and o['status'] in ACTIVE),ZERO)
    room=max(ZERO,capital*exposure-sum((committed(p) for p in positions),ZERO)-pending)
    priority=settings['priority'].get(name,{})
    if priority:
        per_buy=dec(priority['cap'])*dec(priority['use_pct'])/100
        total=min(dec(priority['cap']),per_buy*priority['max_orders'])
        held=sum((committed(p) for p in positions if p['symbol']==name),ZERO)
        budget=max(ZERO,min(per_buy,total-held)) if priority['mode']=='AUTO' else ZERO
    else:
        budget=capital*exposure/r['max_positions']
        occupied={p['symbol'] for p in positions}|{o['symbol'] for o in orders if o['status'] in ACTIVE and o['side']=='BUY'}
        reserved=sum((min(dec(v['cap']),dec(v['cap'])*dec(v['use_pct'])/100*v['max_orders']) for s,v in settings['priority'].items() if v['mode']=='AUTO' and s not in occupied),ZERO)
        budget=min(budget,max(ZERO,room-reserved))
        if r['no_compound_enabled'] and ledger and ledger.get('ceiling') is not None: budget=min(budget,dec(ledger['ceiling']))
    return max(ZERO,min(budget,room,dec(available)))
