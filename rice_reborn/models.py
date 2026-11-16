from dataclasses import dataclass,asdict
from decimal import Decimal,ROUND_DOWN,ROUND_HALF_UP,InvalidOperation
import math,time,uuid

ZERO=Decimal('0')
DAY=86400000
def dec(value=0):
    try: n=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError): raise ValueError('Giá trị số không hợp lệ.') from None
    if not n.is_finite(): raise ValueError('Giá trị phải hữu hạn.')
    return n
def wire(v):
    if isinstance(v,Decimal): return format(v,'f')
    if hasattr(v,'__dataclass_fields__'): return wire(asdict(v))
    if isinstance(v,dict): return {str(k):wire(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [wire(x) for x in v]
    if isinstance(v,float) and not math.isfinite(v): return None
    return v
@dataclass
class Candle:
    open_time:int; open:float; high:float; low:float; close:float; volume:float
    close_time:int=0; closed:bool=True; quote_volume:float=0; taker_buy_quote_volume:float=0
    def row(self): return asdict(self)
@dataclass
class Instrument:
    symbol:str; base:str; quote:str='USDT'; tick:str='0.01'; step:str='0.000001'
    min_qty:str='0.000001'; max_qty:str='1000000000'; min_notional:str='5'; max_notional:str='0'
    market_step:str='0'; market_min:str='0'; market_max:str='0'; filters:list|None=None
    def quantity_step(self,market=False):
        from math import gcd
        a=dec(self.step) or dec('.00000001'); b=dec(self.market_step) if market else ZERO
        if b<=0: return a
        scale=10**max(0,-a.as_tuple().exponent,-b.as_tuple().exponent); x,y=int(a*scale),int(b*scale)
        return dec(x*y//gcd(x,y))/scale
    def rounded(self,value,market=False):
        step=self.quantity_step(market)
        return (dec(value)/step).to_integral_value(rounding=ROUND_DOWN)*step
    def indicator(self,value):
        q=dec(self.tick) or Decimal('0.00000001')
        return float((dec(value)/q).to_integral_value(rounding=ROUND_HALF_UP)*q)
    def validate(self,quantity,price,market=False):
        q,p=dec(quantity),dec(price)
        if q<=0 or p<=0: raise ValueError('Khối lượng và giá phải dương.')
        if q!=self.rounded(q,market): raise ValueError('Khối lượng sai stepSize.')
        lo=max(dec(self.min_qty),dec(self.market_min) if market else ZERO)
        hi=min([v for v in (dec(self.max_qty),dec(self.market_max) if market else ZERO) if v>0],default=dec('1e30'))
        if not lo<=q<=hi: raise ValueError('Khối lượng ngoài giới hạn cặp.')
        if not market and dec(self.tick)>0 and p%dec(self.tick): raise ValueError('Giá sai tickSize.')
        filters={f['filterType']:f for f in self.filters or []}; nf=filters.get('NOTIONAL',filters.get('MIN_NOTIONAL',{}))
        apply_min=not market or nf.get('applyMinToMarket',nf.get('applyToMarket',True))
        apply_max=not market or nf.get('applyMaxToMarket',True)
        if apply_min and q*p<dec(self.min_notional): raise ValueError('Giá trị lệnh dưới MIN_NOTIONAL.')
        if apply_max and dec(self.max_notional)>0 and q*p>dec(self.max_notional): raise ValueError('Giá trị lệnh vượt MAX_NOTIONAL.')
        return q,p
def new_position(name,quantity,entry,mode,**extra):
    return {'id':uuid.uuid4().hex,'symbol':name,'mode':mode,'original':str(quantity),'quantity':str(quantity),'entry':str(entry),
     'opened_at':time.time(),'managed':False,'auto_sell':True,'bot_trade':True,'sl_enabled':True,'tp_enabled':True,
     'protect_enabled':True,'e_enabled':True,'overrides':{},'metrics':{},'fees':{},'cost_known':True,**extra}
