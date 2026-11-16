from copy import deepcopy
from decimal import Decimal
import time
import pytest
from rice_reborn.models import Instrument,dec,new_position,DAY
from rice_reborn.config import DEFAULT_RULES,DEFAULT_SETTINGS,validate_settings
from rice_reborn.execution import Execution
from rice_reborn.exchange import Binance
from rice_reborn.store import Store
from rice_reborn.strategy import Strategy,indicators
from rice_reborn.maths import protect_level

@pytest.fixture
def ex(tmp_path):
    s=Store(tmp_path/'test.db'); e=Execution(s,Binance(),Strategy(s)); e.settings=validate_settings(deepcopy(DEFAULT_SETTINGS))
    e.instruments={'LINKUSDT':Instrument('LINKUSDT','LINK',tick='.01',step='.001',min_notional='5')}
    e.quotes={'LINKUSDT':{'price':'100','at':time.time()}}
    return e

def buy(ex,token='b1',quantity='1'):
    return ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':quantity,'request_id':token})

def bars(values,start=None):
    start=start if start is not None else int(time.time()*1000)//DAY*DAY-DAY*len(values)
    return [{'open_time':start+i*DAY,'close_time':start+(i+1)*DAY-1,'open':v,'high':v*1.01,'low':v*.99,'close':v,'volume':100,'closed':True} for i,v in enumerate(values)]

def test_effective_va():
    r=DEFAULT_RULES
    assert (r['buy_ema_fast'],r['buy_ema_slow'],r['take_profit_pct'],r['take_profit_sell_pct'])==(2,6,9,35)
    assert r['normal_arm_pct']==4.5 and r['normal_giveback_pct']==2.8
    assert r['indicator_exit_policy']=='AUTO' and not r['sell_signal_use_rsi'] and r['dynamic_policy']=='ALERT'
    assert r['buy_ema_cross_sessions']==2 and not r['buy_window_enabled']

def test_manual_ticket_controls_survive_replace_and_restart(ex):
    order=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','type':'LIMIT','price':'99','quantity':'1',
        'request_id':'ticket','entry_plan':{'protect_enabled':False,'e_enabled':False,'take_profit_pct':'11','initial_sl_pct':'-2'}})
    replaced=ex.replace(order['id'],{'price':'98','quantity':'1','request_id':'ticket-replace'})
    again=Execution(ex.store,ex.gateway,ex.strategy)
    again.settings=ex.settings; again.instruments=ex.instruments
    again.quotes={'LINKUSDT':{'price':'98','at':time.time()}}
    again.process(); position=again.positions()[0]
    assert replaced['trade_id']==position['id']
    assert position['overrides']=={'take_profit_pct':11.0,'initial_sl_pct':-2.0}
    assert not position['protect_enabled'] and not position['e_enabled']
    assert position['sl_price']=='0'
    decision=again.strategy.position(position,{'ready':False},DEFAULT_RULES,ex.instruments['LINKUSDT'],'95')
    assert decision['reason']=='SL'

def test_zero_manual_ticket_targets_disable_only_sl_tp(ex):
    ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':'1','request_id':'zero-targets',
        'entry_plan':{'take_profit_pct':'0','initial_sl_pct':'0'}})
    ex.process(); position=ex.positions()[0]
    assert not position['sl_enabled'] and not position['tp_enabled']
    assert position['e_enabled'] and position['protect_enabled']

@pytest.mark.parametrize('plan',[{'initial_sl_pct':-100},{'protect_enabled':'false'},{'take_profit_pct':'NaN'}])
def test_invalid_manual_ticket_does_not_create_order(ex,plan):
    with pytest.raises(ValueError):
        ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':'1','request_id':'invalid-ticket','entry_plan':plan})
    assert not ex.orders()

def test_cancel_is_recorded_once_in_manual_history(ex):
    order=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','type':'LIMIT','price':'90','quantity':'1','request_id':'cancel-history'})
    ex.cancel(order['id']); ex.cancel(order['id'])
    events=ex.store.history('ORDER')
    assert len(events)==2
    assert sum(row['payload']['status']=='CANCELED' for row in events)==1

@pytest.mark.parametrize('value',['nan','inf','-Infinity'])
def test_decimal_rejects_nonfinite(value):
    with pytest.raises(ValueError): dec(value)

def test_low_coin_precision():
    i=Instrument('TINYUSDT','TINY',tick='.00000001',step='1')
    assert i.indicator('.000001024')==.00000102
    m=indicators(bars([.000001+x*.000000001 for x in range(300)]),DEFAULT_RULES,i)
    assert m['buy_fast']>0 and m['buy_slow']>0

def test_step_and_notional():
    i=Instrument('BTCUSDT','BTC',tick='.01',step='.00001',min_notional='5')
    assert i.rounded('.000129')==Decimal('.00012')
    i.validate('.00012','65000',True)
    with pytest.raises(ValueError): i.validate('.000001','65000',True)
    with pytest.raises(ValueError): i.validate('.00011','65000.001')

def test_paper_fill_survives_reload(ex):
    buy(ex); ex.process(); p=ex.positions()[0]
    assert p['quantity']=='1' and ex.balance('PAPER')<900 and ex.orders()[0]['status']=='FILLED'
    assert Execution(ex.store,ex.gateway,ex.strategy).positions()[0]['id']==p['id']

def test_idempotency(ex):
    assert buy(ex)['id']==buy(ex)['id'] and len(ex.orders())==1
    with pytest.raises(ValueError): buy(ex,quantity='2')

def test_duplicate_fill(ex):
    o=buy(ex); f={'id':'same','qty':'.5','price':'100','commission':'.05','commissionAsset':'USDT'}
    ex.apply_fill(o,f); cash=ex.balance('PAPER'); ex.apply_fill(o,f)
    assert ex.positions()[0]['quantity']=='0.5' and ex.balance('PAPER')==cash

def test_bad_fill_rolls_back_receipt(ex):
    o=buy(ex)
    with pytest.raises(ValueError): ex.apply_fill(o,{'id':'fixable','qty':'2','price':'100'})
    ex.apply_fill(o,{'id':'fixable','qty':'.5','price':'100'})
    assert ex.positions()[0]['quantity']=='0.5'

def test_base_fee_inventory(ex):
    o=buy(ex); ex.apply_fill(o,{'id':'base','qty':'1','price':'100','commission':'.001','commissionAsset':'LINK'})
    assert ex.positions()[0]['quantity']=='0.999'

def test_real_readonly_blocks_below_http(ex):
    ex.gateway.permissions=lambda **kw:{'read':True,'trade':False}
    with pytest.raises(PermissionError): ex.create({'mode':'REAL','symbol':'LINKUSDT','side':'BUY','quantity':'1'})
    assert not ex.orders()

def test_stale_quote(ex):
    ex.quotes['LINKUSDT']['at']=time.time()-30
    with pytest.raises(ValueError): buy(ex)

def test_limit_reserves_until_match(ex):
    ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','type':'LIMIT','price':'90','quantity':'1'})
    ex.process(); assert ex.orders()[0]['status']=='PENDING' and ex.available('PAPER')<1000
    ex.quotes['LINKUSDT']['price']='89'; ex.process(); assert ex.orders()[0]['status']=='FILLED'

def test_sell_reservation(ex):
    buy(ex); ex.process(); p=ex.positions()[0]
    data={'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'.6','trade_id':p['id']}
    ex.create(data)
    with pytest.raises(ValueError): ex.create(data)

def test_manual_sell_only_pauses_buy(ex):
    buy(ex); ex.process(); p=ex.positions()[0]
    ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'.5','trade_id':p['id']}); ex.process()
    assert ex.store.get('pause','PAPER')>time.time() and len(ex.positions())==1

def test_bot_off_cancels_local(ex):
    ex.bot['PAPER']=True; ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','quantity':'1'},'BOT')
    ex.bot['PAPER']=False; ex.process(); assert ex.orders()[0]['status']=='CANCELED'

def test_arm_points(): assert protect_level(100,10,4.5,2.8).trigger_price==pytest.approx(107.2)

def test_dynamic_floor():
    p=protect_level(100,4,4.5,2.8,dynamic_enabled=True,atr_pct=1,atr_multiplier=.8,atr_activation_multiplier=.55,retention_pct=72,retention_until_pct=4.5)
    assert p.active and p.trigger_price>=102.88

@pytest.mark.parametrize('sell,tp_pct,expected',[(False,35,'TP'),(True,35,'E'),(True,100,'TP')])
def test_exit_priority(ex,sell,tp_pct,expected):
    p=new_position('LINKUSDT',10,100,'PAPER'); r={**deepcopy(DEFAULT_RULES),'take_profit_sell_pct':tp_pct}; d={'ready':True,'sell_condition':sell,'marks':{'atr_pct':2}}
    assert ex.strategy.position(p,d,r,ex.instruments['LINKUSDT'],110)['reason']==expected

def test_sl_first(ex):
    p=new_position('LINKUSDT',10,100,'PAPER'); d={'ready':True,'sell_condition':True,'marks':{'atr_pct':2}}
    assert ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],95)['reason']=='SL'

def test_e_new_episode(ex):
    p=new_position('LINKUSDT',10,100,'PAPER'); d={'ready':True,'sell_condition':True,'marks':{'atr_pct':2}}
    a=ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],100); p['metrics']['e']['filled']=1
    assert ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],100) is None
    d['sell_condition']=False; ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],100)
    d['sell_condition']=True; b=ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],100)
    assert a['occurrence']=='E:1' and b['occurrence']=='E:2'

def test_used_cross_cannot_reappear(ex):
    rows=bars([100]*298+[99,101]); now=time.time(); i=ex.instruments['LINKUSDT']; d=ex.strategy.observe('LINKUSDT','PAPER',rows,DEFAULT_RULES,i,now)
    assert d['cross']; ex.strategy.consume('LINKUSDT','PAPER',d['cross']['id'],now)
    after=ex.strategy.observe('LINKUSDT','PAPER',rows,DEFAULT_RULES,i,now+1)
    assert after['cross'] is None and not after['ema_valid']

def test_closed_scans_do_not_duplicate(ex):
    rows=bars([100]*298+[99,101]); now=time.time(); i=ex.instruments['LINKUSDT']
    a=ex.strategy.observe('LINKUSDT','PAPER',rows,DEFAULT_RULES,i,now); b=ex.strategy.observe('LINKUSDT','PAPER',rows,DEFAULT_RULES,i,now+2)
    assert a['cross']['id']==b['cross']['id'] and a['cross_count']==b['cross_count']

def test_replay_uses_daily_seed(ex):
    now=time.time(); day=int(now*1000)//DAY*DAY; daily=bars([100]*300,day-300*DAY); minutes=[{'open_time':day+i*60000,'close':p} for i,p in enumerate([99,101])]
    ex.strategy.recover('LINKUSDT','PAPER',daily,minutes,DEFAULT_RULES,ex.instruments['LINKUSDT'],now)
    m=ex.strategy.memory('LINKUSDT','PAPER',now)
    assert m['cross']['source']=='1M_REPLAY' and m['cross']['at']==(day+60000)/1000

def test_recheck_cancels_unsent(ex):
    buy(ex); ex.process(); p=ex.positions()[0]; ex.bot['PAPER']=True
    d={'reason':'E','occurrence':'E:1','policy':'AUTO','price':'100'}
    ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'1','trade_id':p['id'],'exit':d},'BOT')
    ex.process({}); assert ex.orders()[-1]['status']=='CANCELED'

def test_transactions_rollback(tmp_path):
    s=Store(tmp_path/'atomic.db')
    with pytest.raises(ValueError):
        with s.transaction(): s.put('x','1',{'n':1}); raise ValueError('rollback')
    assert s.get('x','1') is None

@pytest.mark.parametrize('changes',[{'capital_usdt':'nan'},{'rules':{'normal_sell_pct':101}},{'rules':{'initial_sl_pct':0}},{'rules':{'buy_window_start':'25:90'}},{'watchlist':[]},{'priority':{'BTCUSDT':{'mode':'INVALID'}}}])
def test_invalid_settings(changes):
    with pytest.raises(ValueError): validate_settings(changes)

def test_pnl_allocates_buy_and_sell_fees(ex):
    o=buy(ex); ex.apply_fill(o,{'id':'cost-buy','qty':'1','price':'100','commission':'.1','commissionAsset':'USDT'})
    p=ex.positions()[0]; ex.quotes['LINKUSDT']['price']='110'
    sell=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'.5','trade_id':p['id']})
    ex.apply_fill(sell,{'id':'cost-sell','qty':'.5','price':'110','commission':'.055','commissionAsset':'USDT'})
    p=ex.positions()[0]
    assert dec(p['realized_net'])==dec('4.895') and dec(p['remaining_cost_usdt'])==dec('50.05')

def test_bnb_fee_is_valued_and_labeled(ex):
    ex.quotes['BNBUSDT']={'price':'500','at':time.time()}
    o=buy(ex); ex.apply_fill(o,{'id':'bnb-buy','qty':'1','price':'100','commission':'.001','commissionAsset':'BNB','time':int(time.time()*1000)})
    p=ex.positions()[0]; assert p['fees_estimated'] and not p.get('fees_pending')
    ex.quotes['LINKUSDT']['price']='110'
    sell=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'1','trade_id':p['id']})
    ex.apply_fill(sell,{'id':'bnb-sell','qty':'1','price':'110','commission':'.11','commissionAsset':'USDT'})
    p=ex.store.get('position',p['id']); assert dec(p['realized_net'])==dec('9.39')

def test_partial_buy_replace_keeps_parent(ex):
    old=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','type':'LIMIT','price':'100','quantity':'1'})
    ex.apply_fill(old,{'id':'first-half','qty':'.5','price':'100'}); parent=ex.positions()[0]['id']
    replacement=ex.replace(old['id'],{'price':'99','quantity':'.5','request_id':'amended'})
    assert replacement['trade_id']==parent
    ex.quotes['LINKUSDT']['price']='98'; ex.process()
    assert len(ex.positions())==1 and dec(ex.positions()[0]['quantity'])==1

def test_invalid_replace_does_not_cancel_valid_order(ex):
    old=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'BUY','type':'LIMIT','price':'100','quantity':'1'})
    with pytest.raises(ValueError): ex.replace(old['id'],{'price':'99.001','quantity':'.5','request_id':'invalid'})
    assert ex.store.get('order',old['id'])['status']=='PENDING'

def test_price_gap_cannot_make_paper_cash_negative(ex):
    buy(ex,quantity='9'); ex.quotes['LINKUSDT']['price']='200'; ex.process()
    assert ex.orders()[0]['status']=='REJECTED' and ex.balance('PAPER')==1000 and not ex.positions()

def test_sl_survives_missing_indicators(ex):
    p=new_position('LINKUSDT',10,100,'PAPER')
    assert ex.strategy.position(p,{'ready':False},DEFAULT_RULES,ex.instruments['LINKUSDT'],95)['reason']=='SL'

def test_unknown_e_data_does_not_end_episode(ex):
    p=new_position('LINKUSDT',10,100,'PAPER'); d={'ready':True,'sell_condition':True,'marks':{'atr_pct':2}}
    ex.strategy.position(p,d,DEFAULT_RULES,ex.instruments['LINKUSDT'],100)
    ex.strategy.position(p,{'ready':False},DEFAULT_RULES,ex.instruments['LINKUSDT'],100)
    assert p['metrics']['e']['active'] and p['metrics']['e']['episode']==1

def test_realtime_cold_start_does_not_invent_cross(ex):
    rows=bars([100]*298+[99,101]); rows[-1]['closed']=False
    d=ex.strategy.observe('LINKUSDT','PAPER',rows,DEFAULT_RULES,ex.instruments['LINKUSDT'])
    assert not d['cross'] and not d['ema_valid']

def test_market_lot_intersects_regular_lot():
    i=Instrument('LINKUSDT','LINK',step='.006',market_step='.004',min_notional='0')
    assert i.rounded('.023',True)==dec('.012')
    with pytest.raises(ValueError): i.validate('.008','100',True)
    i.validate('.012','100',True)

def test_future_quote_not_accepted(ex):
    ex.quotes['LINKUSDT']['at']=time.time()+3600
    with pytest.raises(ValueError): buy(ex)

def test_unknown_post_is_not_retried(ex):
    from rice_reborn.exchange import ExchangeError
    ex.gateway.guard_trade=lambda:None; ex.gateway.permissions=lambda **kw:{'trade':True}
    ex.account={'balances':[{'asset':'USDT','free':'1000','locked':'0'}]}; ex.account_at=time.time(); calls=[]
    def fail(order): calls.append(order['id']); raise ExchangeError('timeout',unknown=True)
    ex.gateway.place=fail
    o=ex.create({'mode':'REAL','symbol':'LINKUSDT','side':'BUY','quantity':'1'})
    ex.process(); ex.process()
    assert len(calls)==1 and ex.store.get('order',o['id'])['status']=='UNKNOWN'

def test_decimal_budget_preserves_precision():
    assert validate_settings({'capital_usdt':'0.123456789123456789'})['capital_usdt']=='0.123456789123456789'

def test_priority_usage_is_per_buy_not_total():
    from rice_reborn.portfolio import buy_budget
    settings=validate_settings({'watchlist':['LINKUSDT'],'paper_capital':'1000','priority':{'LINKUSDT':{'mode':'AUTO','cap':'1000','use_pct':50,'max_orders':2}}})
    p=new_position('LINKUSDT',5,100,'PAPER')
    assert buy_budget(settings,'PAPER','LINKUSDT',[p],[],500,'UPTREND')==500

def test_broker_pending_buy_consumes_p1_room():
    from rice_reborn.portfolio import buy_budget
    settings=validate_settings({'paper_capital':'1000'})
    order={'symbol':'BTCUSDT','mode':'PAPER','side':'BUY','remaining':'9','reserve_price':'100','status':'NEW','broker_id':'123'}
    assert buy_budget(settings,'PAPER','LINKUSDT',[],[order],1000,'UPTREND')==100

def test_confirmation_selected_condition_is_enforced(ex):
    r={**deepcopy(DEFAULT_RULES),'buy_signal_use_ema':False,'buy_signal_use_rsi':False,'buy_confirmation_enabled':True,'buy_confirmation_require_ema':True,'buy_confirmation_require_rsi':False}
    d=ex.strategy.observe('LINKUSDT','PAPER',bars([100]*298+[99,98]),r,ex.instruments['LINKUSDT'])
    assert d['action']=='WAIT' and 'XÁC NHẬN' in d['reason']

def test_alert_priority_does_not_reserve_slot():
    from rice_reborn.portfolio import entry_block
    settings=validate_settings({'rules':{'max_positions':1},'priority':{'BTCUSDT':{'mode':'ALERT','cap':0}}})
    assert not entry_block(settings,'LINKUSDT',[],[])

def test_fractional_indicator_period_rejected():
    with pytest.raises(ValueError): validate_settings({'rules':{'buy_ema_fast':2.5}})

def test_only_one_runtime_can_own_execution(tmp_path):
    from rice_reborn.lease import RuntimeLease
    a=RuntimeLease(tmp_path/'runtime.lock')
    try:
        with pytest.raises(RuntimeError): RuntimeLease(tmp_path/'runtime.lock')
    finally: a.close()
    b=RuntimeLease(tmp_path/'runtime.lock'); b.close()

def test_zero_price_override_means_inherit_rule(ex):
    p=new_position('LINKUSDT',10,100,'PAPER',sl_price='0',tp_price='0')
    assert ex.strategy.position(p,{'ready':False},DEFAULT_RULES,ex.instruments['LINKUSDT'],100) is None
    assert ex.strategy.position(p,{'ready':False},DEFAULT_RULES,ex.instruments['LINKUSDT'],96)['reason']=='SL'

def test_late_bnb_valuation_repairs_pnl_and_unblocks_chain(ex):
    o=buy(ex); ex.apply_fill(o,{'id':'late-buy','qty':'1','price':'100','commission':'.001','commissionAsset':'BNB','time':int((time.time()-120)*1000)})
    p=ex.positions()[0]; assert p['fees_pending']
    ex.quotes['LINKUSDT']['price']='110'; sell=ex.create({'mode':'PAPER','symbol':'LINKUSDT','side':'SELL','quantity':'1','trade_id':p['id']})
    ex.apply_fill(sell,{'id':'late-sell','qty':'1','price':'110','commission':'.11','commissionAsset':'USDT'})
    ex.gateway.klines=lambda name,interval,limit,start,end:[{'open_time':start,'close':500,'closed':True}]
    ex.repair_fees(); repaired=ex.store.get('position',p['id'])
    assert not repaired['fees_pending'] and dec(repaired['realized_net'])==dec('9.39')
    assert not ex.strategy.memory('LINKUSDT','PAPER',time.time())['loss_accounting_pending']
