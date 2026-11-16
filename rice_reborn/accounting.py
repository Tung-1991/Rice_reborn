"""Rebuild quote-cost accounting from committed fills; never invent inventory."""
from .models import dec,ZERO

def rebuild(fills,base,seed_quantity=0,seed_entry=0):
    inventory=dec(seed_quantity); cost=inventory*dec(seed_entry); gross_cost=cost
    realized=ZERO; gross=ZERO; pending=False; sells={}
    for row in sorted(fills,key=lambda x:(x['at'],x['seq'])):
        f=row['payload']; q,p=dec(f['qty']),dec(f['price']); fee=dec(f.get('commission',0)); asset=f.get('commissionAsset','USDT')
        quoted=f.get('fee_usdt'); unknown=quoted is None and fee>0 and asset not in ('USDT',base)
        quoted=dec(quoted) if quoted is not None else fee if asset=='USDT' else ZERO
        pending|=unknown
        if f['side']=='BUY':
            net=q-(fee if asset==base else ZERO)
            cost+=q*p+quoted; gross_cost+=net*p; inventory+=net
        else:
            reduction=q+(fee if asset==base else ZERO)
            if inventory<=0 or reduction>inventory: raise ValueError('Fill không đủ bằng chứng để dựng lại giá vốn.')
            part_cost=cost*reduction/inventory; part_gross_cost=gross_cost*reduction/inventory
            pnl=q*p-part_cost-quoted; price_pnl=q*(p-gross_cost/inventory)
            realized+=pnl; gross+=price_pnl; inventory-=reduction; cost-=part_cost; gross_cost-=part_gross_cost
            sells[row['id']]={'net_pnl':str(pnl) if not pending else None,'gross_pnl':str(price_pnl)}
    return {'quantity':inventory,'remaining_cost_usdt':str(cost),'realized_net':str(realized),'realized_gross':str(gross),'fees_pending':pending,'sells':sells}
