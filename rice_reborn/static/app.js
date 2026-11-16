'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const csrf=$('meta[name=csrf-token]').content;
const uid=()=>globalThis.crypto?.randomUUID?globalThis.crypto.randomUUID():Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
let state=null, mode=document.body.dataset.demo==='true'?'PAPER':'REAL', selected='BTCUSDT', modalName='', polling=false, requestId=uid(), historyOffset=0;
const side='BUY';
let autoTicket={key:'',at:0,quantity:null,reason:''}, autoBusy=false;
let entryProtect=false, entryE=false, infoTab='preview', previewTab='overview', lastLogRead=0, selectionAnchor=null;
const selectedRows=new Set();
const activeOrder=o=>!['FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'].includes(o.status);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=2)=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const qty=v=>v===null||v===undefined?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:8});
const price=v=>v===null||v===undefined?'—':num(v,Number(v)<.01?8:Number(v)<1?5:2);
const signed=(v,d=2)=>v===null||v===undefined?'—':(Number(v)>0?'+':'')+num(v,d);
const color=v=>Number(v)>0?'positive':Number(v)<0?'negative':'';
const date=v=>v?new Date(v*1000).toLocaleString('vi-VN',{timeZone:'Asia/Bangkok',hour12:false}):'—';
function toast(text,error=false){const e=$('#toast');e.textContent=text;e.hidden=false;e.classList.toggle('error-text',error);clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.hidden=true,7000);}
async function api(path,method='GET',data){const r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});if(r.status===401){location.href='/login';throw Error('Cần đăng nhập.');}const result=await r.json();if(!r.ok)throw Error(result.error||'Yêu cầu không thành công.');return result;}

function askDialog(message,value,question=false){
 const dialog=document.querySelector('#confirm-modal');
 if(dialog.open)return Promise.reject(Error('Đang có yêu cầu xác nhận.'));
 dialog.innerHTML='<form method="dialog"><h2></h2><p class="confirm-message"></p><label class="confirm-input-label" hidden>Giá trị<input id="confirm-input" autocomplete="off"></label><div class="modal-actions"><button type="button" class="cancel-action">Hủy</button><button type="submit" value="accept" class="primary">XÁC NHẬN</button></div></form>';
 dialog.querySelector('h2').textContent=question?'Nhập giá trị':'Xác nhận thao tác';
 dialog.querySelector('p').textContent=message;
 const input=dialog.querySelector('input');dialog.querySelector('label').hidden=!question;input.value=value??'';input.required=question;
 dialog.querySelector('.cancel-action').onclick=()=>{dialog.returnValue='cancel';dialog.close();};
 dialog.returnValue='cancel';
 return new Promise(resolve=>{dialog.onclose=()=>resolve(dialog.returnValue==='accept'?(question?input.value:true):(question?null:false));dialog.showModal();if(question)input.focus();});
}
function askConfirm(message){return askDialog(message,'',false);}
function askInput(message,value){return askDialog(message,value,true);}

function setText(id,value){const e=$(id);if(e)e.textContent=value;}
function decision(){return state?.decisions?.[mode]?.[selected]||{};}
async function poll(){if(polling)return;polling=true;try{state=await api('/api/status');render();if(modalName==='backtest')renderJobs();}catch(e){setText('#health-title','MẤT KẾT NỐI · '+e.message);}finally{polling=false;}}
function render() {
 const ps=state.positions.filter(p=>p.mode===mode), os=state.orders.filter(o=>o.mode===mode), q=state.quotes[selected], d=decision(), m=d.marks||{}, r=state.settings.rules;
 for(const prefix of ['mode','book'])for(const book of ['REAL','PAPER'])$('#'+prefix+'-'+book.toLowerCase()).classList.toggle('active',mode===book);
 const nav=mode==='REAL'?Number(state.nav):Number(state.paper_cash)+ps.reduce((sum,p)=>sum+Number(p.quantity)*Number(p.price||p.entry),0);
 $('#nav-value').innerHTML=num(nav)+'<small>USDT</small>';
 const current=mode==='PAPER'||state.account_status==='CURRENT';
 setText('#account-status',mode==='PAPER'?'MÔ PHỎNG':current?'BACKEND · OK':state.account_status==='STALE'?'DỮ LIỆU CŨ':'CHƯA CÓ API');
 setText('#account-book',mode);setText('#position-count',ps.length+' vị thế');
 setText('#free-cash',num(mode==='REAL'?state.holdings.find(x=>x.asset==='USDT')?.free:state.paper_cash));
 const pnl=ps.some(p=>p.pnl===null)?null:ps.reduce((sum,p)=>sum+Number(p.pnl),0);
 setText('#unrealized',signed(pnl));$('#unrealized').className=color(pnl);
 const fees={};ps.forEach(p=>Object.entries(p.fees||{}).forEach(([asset,value])=>fees[asset]=(fees[asset]||0)+Number(value)));
 setText('#fees-value',Object.entries(fees).map(([asset,value])=>qty(value)+' '+asset).join(' · ')||'0');
 const canTrade=mode==='PAPER'||state.capabilities.trade;
 setText('#api-rights',state.demo?'DEMO · READ ONLY':state.capabilities.status);
 setText('#trade-lock',canTrade?mode==='PAPER'?'PAPER':'SPOT TRADE':'CHỈ ĐỌC');
 $('#submit-order').disabled=!canTrade;$('#bot-toggle').disabled=!canTrade;
 $('#bot-toggle').className=state.bot[mode]?'bot-on':'bot-off';
 setText('#bot-toggle','BOT · '+(state.bot[mode]?'ON':'OFF'));setText('#bot-status','BOT · '+(state.bot[mode]?'ON':'OFF'));
 setText('#order-hint',mode==='PAPER'?'PAPER · phí mô phỏng 0,1%, trượt giá 0,05%.':canTrade?'REAL · gửi Binance sau khi xác nhận.':'API Read: theo dõi/thông báo; đặt, sửa, hủy REAL cần quyền Spot Trade.');
 setText('#telegram-status',state.telegram_configured?(state.settings.telegram_enabled?'TELEGRAM ON':'TELEGRAM OFF'):'TELEGRAM CHƯA CẤU HÌNH');
 const names=[...new Set([...state.settings.watchlist,...state.positions.map(p=>p.symbol)])], selector=$('#order-symbol');
 if([...selector.options].map(x=>x.value).join()!==names.join())selector.innerHTML=names.map(x=>`<option>${esc(x)}</option>`).join('');
 selector.value=selected;
 setText('#quantity-unit',selected.replace(/USDT$/,''));setText('#market-symbol',selected.replace(/USDT$/,'')+' / USDT');
 setText('#market-price',price(q?.price));setText('#market-source',q?.source||'CHỜ GIÁ');
 const day=state.daily?.[selected], today=Math.floor(Date.now()/86400000)*86400000, validDay=day?.open_time===today;
 setText('#day-low',validDay?price(day.low):'—');setText('#day-high',validDay?price(day.high):'—');
 const change=day?.previous_close&&q?(Number(q.price)/Number(day.previous_close)-1)*100:null;
 setText('#day-change',signed(change)+'%');$('#day-change').className=color(change);
 setText('#market-note',q?'Mẫu giá '+date(q.at)+' · '+(Date.now()/1000-q.at>10?'GIÁ CŨ':'nến 1D UTC'):'Chưa có giá hợp lệ');
 for(const [id,key]of [['ema-fast','buy_fast'],['ema-slow','buy_slow'],['ema-sell-fast','sell_fast'],['ema-sell-slow','sell_slow']])setText('#'+id,price(m[key]));
 setText('#rsi',num(m.rsi));setText('#atr',price(m.atr));setText('#atr-pct',num(m.atr_pct)+'%');
 setText('#signal-mode',state.settings.signal_mode+' / '+state.settings.indicator_interval);
 const exposure=state.settings.market_override?state.settings.market_exposure_pct:(r.exposure[state.context.BTCUSDT?.state]||0)*100;
 setText('#p1-value',num(exposure,0)+'% COIN / '+num(100-exposure,0)+'% TIỀN');
 setText('#p1-mode',state.settings.market_override?'CHỌN TAY':'AUTO BTC');
 setText('#btc-context','BTC '+(state.context.BTCUSDT?.state||'CHỜ'));setText('#eth-context','ETH '+(state.context.ETHUSDT?.state||'CHỜ'));
 const own=ps.filter(p=>p.symbol===selected), pending=os.filter(o=>o.symbol===selected&&activeOrder(o));
 setText('#p2-state',own.length?'ĐANG GIỮ · '+qty(own.reduce((sum,p)=>sum+Number(p.quantity),0)):pending.length?'ĐANG CÓ LỆNH':d.ema_valid?'EMA ĐẠT':d.ready?'CHỜ CẮT LÊN':'CHỜ DỮ LIỆU');
 $('#p2-state').className=own.length||pending.length?'amber-text':d.ema_valid?'positive':'';
 setText('#p2-count',(d.ready?d.cross_count:'—')+' LẦN CẮT · '+r.buy_ema_cross_sessions+' NGÀY · '+r.buy_ema_cross_mode);
 setText('#p2-last',d.cross?'↑ '+date(d.cross.at)+' · '+d.cross.source:'Chưa có mốc cắt được xác minh');setText('#p2-reason',d.reason||'CHỜ DỮ LIỆU');
 setText('#whipsaw',(m.whipsaw??'—')+'/'+r.whipsaw_n+' · '+r.whipsaw_x+' ngày');
 $('#whipsaw').className=r.whipsaw_enabled&&m.whipsaw>=r.whipsaw_n?'negative':'positive';
 setText('#loss-streak',(d.loss_streak??'—')+'/'+r.loss_lock_count+(d.loss_lock_until===-1?' · KHÓA':d.loss_lock_until>Date.now()/1000?' · KHÓA':' · OK'));
 $('#unlock-whipsaw').disabled=!(r.whipsaw_enabled&&m.whipsaw>=r.whipsaw_n);$('#unlock-loss').disabled=!(d.loss_lock_until===-1||d.loss_lock_until>Date.now()/1000);
 setText('#p3-capital',ps.length+'/'+r.max_positions+' mã · '+num(mode==='PAPER'?state.settings.paper_capital:state.settings.capital_usdt)+' USDT · '+(state.settings.priority[selected]?.mode||'THƯỜNG'));
 const form=$('#order-form').elements;
 form.take_profit.placeholder='+'+r.take_profit_pct+'%';form.stop_loss.placeholder=r.initial_sl_pct+'%';
 $('#entry-protect').classList.toggle('em-on',entryProtect);$('#entry-e').classList.toggle('em-on',entryE);
 setText('#entry-protect','PROTECT · '+(entryProtect?'ON':'OFF'));setText('#entry-e','E · '+(entryE?'ON':'OFF'));
 renderTable(ps,os);updateEstimate();fitPreview();refreshAutoTicket();
 const errors=Object.entries(state.errors);
 setText('#errors',errors.map(([key,value])=>key+': '+value).join(' · '));
 setText('#health-title',state.loading?'ĐANG ĐỒNG BỘ':errors.length?'CẦN KIỂM TRA':'BACKEND OK');
 setText('#health-time',state.demo?'DEMO · OFFLINE':Object.entries(state.streams).map(([key,value])=>key+': '+value).join(' · ')||'REST · CHỜ WS');
 if(infoTab!=='preview'&&Date.now()-lastLogRead>5000)loadSystemLog();
}
function table(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;}
function fitPreview(){
 if(innerWidth<=780||previewTab!=='overview'||$('.right-column').classList.contains('info-collapsed'))return;
 const contentHeight=e=>[...e.children].reduce((sum,child)=>{const css=getComputedStyle(child);return sum+child.getBoundingClientRect().height+(parseFloat(css.marginTop)||0)+(parseFloat(css.marginBottom)||0);},0);
 const needed=Math.ceil(Math.max(contentHeight($('#rule-preview')),contentHeight($('#order-preview')))+106);
 const right=$('.right-column'), value=Math.max(330,needed)+'px';
 if(right.style.getPropertyValue('--system-height')!==value)right.style.setProperty('--system-height',value);
}
function positionLevels(p){const r={...state.settings.rules,...p.overrides}, entry=Number(p.entry);return {r,sl:Number(p.sl_price)>0?Number(p.sl_price):entry*(1+r.initial_sl_pct/100),tp:Number(p.tp_price)>0?Number(p.tp_price):entry*(1+r.take_profit_pct/100)};}
function renderTable(ps,os){
 const orders=os.filter(activeOrder), ids=new Set([...ps.map(p=>'p:'+p.id),...orders.map(o=>'o:'+o.id)]);
 for(const id of selectedRows)if(!ids.has(id))selectedRows.delete(id);
 const headers=['Ticket','Thời gian','Lệnh / Vị thế','SL / TP / PROTECT','Phí mua / bán','Rủi ro/Kỳ vọng (%)','PnL giữ / chốt · MAE / MFE','Trạng thái','BOT','Override','STT'];
 let index=0;
 const rows=ps.map(p=>{
  const {r,sl,tp}=positionLevels(p), protect=p.metrics?.protect, risk=p.cost_known&&p.sl_enabled?(1-sl/Number(p.entry))*100:null, expected=p.cost_known&&p.tp_enabled?(tp/Number(p.entry)-1)*100:null;
  const fees=Object.entries(p.fees||{}).map(([asset,value])=>qty(value)+' '+asset).join(' + ')||'CHƯA CÓ DỮ LIỆU PHÍ';
  const status=p.exit?.reason?`${p.exit.reason} · ${p.exit.policy}`:p.managed?'ĐANG THEO DÕI':'CHƯA NHẬN QUẢN LÝ';
  const id='p:'+p.id;
  return `<tr data-row="${esc(id)}" data-symbol="${esc(p.symbol)}" class="${Number(p.pnl)>0?'profit':Number(p.pnl)<0?'loss':''} ${selectedRows.has(id)?'row-selected':''}"><td class="ticket-cell">#${esc(p.id.slice(0,12))}</td><td>${date(p.opened_at)}</td><td><strong>BUY ${esc(p.symbol)}</strong> · KL ${qty(p.quantity)}/${qty(p.original)} @ ${p.cost_known?price(p.entry):'THIẾU VỐN'}<small>${esc(p.mode)} · ${esc(p.source||'BOT')} · ${num(Number(p.quantity)*Number(p.entry))} USDT</small></td><td>SL ${p.sl_enabled?price(sl):'OFF'} · TP ${p.tp_enabled?price(tp):'OFF'}<small>PROTECT ${p.protect_enabled?protect?esc(protect.stage+' '+protect.policy)+' @ '+price(protect.trigger):'CHỜ':'OFF'} · E ${p.e_enabled?esc(r.indicator_exit_policy):'OFF'}</small></td><td>${esc(fees)}<small>${p.fees_pending?'THIẾU PHÍ':p.fees_estimated?'PHÍ QUY ĐỔI ƯỚC TÍNH':Object.keys(p.fees||{}).length?'ĐÃ GHI PHÍ THỰC THU':'THIẾU NGUỒN PHÍ'}</small></td><td>R ${num(risk)}% · E ${num(expected)}%<small>${risk>0?'1:'+num(expected/risk):'—'}</small></td><td>PnL GIỮ <strong>${signed(p.pnl)} USDT (${signed(p.profit_pct)}%)</strong><small>GIỮ TRƯỚC PHÍ · CHỐT RÒNG ${p.fees_pending?'THIẾU PHÍ':signed(p.realized_net||0)} · MAE ${signed(p.metrics?.mae)}% / MFE ${signed(p.metrics?.mfe)}%</small></td><td>${esc(status)}<small>CÒN GIỮ ${qty(p.quantity)} · GIÁ ${price(p.price)}</small><small>${esc(r.normal_policy)} ARM · ${esc(r.dynamic_policy)} DYN · ${p.auto_sell?'TỰ BÁN ON':'TỰ BÁN OFF'}</small></td><td><button data-position="${esc(p.id)}">${p.bot_trade&&p.managed?'CHO PHÉP':'TẠM DỪNG'}</button><small>BOT ${state.bot[mode]?'ON':'OFF'}</small></td><td><button data-position="${esc(p.id)}">THIẾT LẬP</button><small><button data-sell="${esc(p.id)}" ${mode==='REAL'&&!state.capabilities.trade?'disabled':''}>BÁN</button></small></td><td>${++index}</td></tr>`;
 });
 for(const o of orders){const id='o:'+o.id;rows.push(`<tr data-row="${esc(id)}" data-symbol="${esc(o.symbol)}" class="${o.source==='EXTERNAL'?'external':'pending'} ${selectedRows.has(id)?'row-selected':''}"><td class="ticket-cell">#${esc(o.id.slice(0,12))}</td><td>${date(o.created_at)}</td><td><strong>${esc(o.side)} ${esc(o.symbol)}</strong> · KL ${qty(o.filled)}/${qty(o.quantity)}<small>${esc(o.type)} @ ${price(o.price)} · ${esc(o.source)}</small></td><td>—</td><td>THEO KHỚP THỰC TẾ</td><td>—</td><td>CHỜ KHỚP<small>CÒN ${qty(o.remaining)}</small></td><td>${esc(o.status)}<small>${esc(o.result||'')}</small><small>${esc(o.reason||'')}</small></td><td>${o.source==='BOT'?'BOT':'TAY'}</td><td>${o.source==='EXTERNAL'?'NGOÀI APP':`<button data-cancel="${esc(o.id)}" ${mode==='REAL'&&!state.capabilities.trade?'disabled':''}>HỦY</button>${o.type==='LIMIT'?` <button data-replace="${esc(o.id)}">SỬA</button>`:''}`}</td><td>${++index}</td></tr>`);}
 for(const name of state.settings.watchlist.filter(name=>!ps.some(p=>p.symbol===name)&&!orders.some(o=>o.symbol===name))){const d=state.decisions[mode][name]||{};rows.push(`<tr class="reference" data-symbol="${esc(name)}"><td class="ticket-cell">THAM KHẢO</td><td>${d.cross?date(d.cross.at):'—'}</td><td><button data-select="${esc(name)}">${esc(name)}</button><small>GIÁ ${price(state.quotes[name]?.price)} · ${esc(state.settings.priority[name]?.mode||'THƯỜNG')}</small></td><td>THEO RULE VA</td><td>CHƯA MUA</td><td>—</td><td>—</td><td>${esc(d.reason||'CHỜ DỮ LIỆU')}<small>EMA ${price(d.marks?.buy_fast)} / ${price(d.marks?.buy_slow)} · RSI ${num(d.marks?.rsi)}</small></td><td>${state.bot[mode]?'BOT ON':'BOT OFF'}</td><td><button data-select="${esc(name)}">XEM</button></td><td>${++index}</td></tr>`);}
 $('#running-table').innerHTML=rows.length?table(headers,rows):'<div class="empty-state">Chưa có lệnh hoặc vị thế trong sổ '+mode+'</div>';
 setText('#running-count',ps.length+orders.length);setText('#book-label',mode);setText('#table-summary',ps.length+' vị thế · '+orders.length+' lệnh chờ · '+selectedRows.size+' đã chọn');
 $('#cancel-selected').disabled=!os.some(o=>selectedRows.has('o:'+o.id)&&activeOrder(o)&&o.source!=='EXTERNAL')||(mode==='REAL'&&!state.capabilities.trade);
 $('#sell-selected').disabled=!ps.some(p=>selectedRows.has('p:'+p.id))||(mode==='REAL'&&!state.capabilities.trade);
}
function syncSelection(){
 if(!state)return;const ps=state.positions.filter(p=>p.mode===mode),os=state.orders.filter(o=>o.mode===mode);
 $$('#running-table tr[data-row]').forEach(row=>row.classList.toggle('row-selected',selectedRows.has(row.dataset.row)));
 setText('#table-summary',ps.length+' vị thế · '+os.filter(activeOrder).length+' lệnh chờ · '+selectedRows.size+' đã chọn');
 $('#cancel-selected').disabled=!os.some(o=>selectedRows.has('o:'+o.id)&&activeOrder(o)&&o.source!=='EXTERNAL')||(mode==='REAL'&&!state.capabilities.trade);
 $('#sell-selected').disabled=!ps.some(p=>selectedRows.has('p:'+p.id))||(mode==='REAL'&&!state.capabilities.trade);
}
function selectSymbol(name){const form=$('#order-form').elements;if(name!==selected)form.price.value='';selected=name;requestId=uid();form.quantity.value='';if(state)render();}
function updateEstimate(){
 if(!state)return;
 const f=$('#order-form').elements, r=state.settings.rules, live=Number(state.quotes[selected]?.price), entry=f.type.value==='MARKET'?live:Number(f.price.value), cached=autoTicket.key===autoTicketKey()?autoTicket.quantity:null, quantity=Number(f.quantity.value||cached||0), notional=entry*quantity;
 f.price.disabled=f.type.value==='MARKET';f.take_profit.disabled=side==='SELL';f.stop_loss.disabled=side==='SELL';
 const take=f.take_profit.value===''?r.take_profit_pct:Number(f.take_profit.value), stop=f.stop_loss.value===''?r.initial_sl_pct:Number(f.stop_loss.value), fee=notional*.001;
 setText('#order-notional',num(notional+(side==='BUY'?fee:-fee))+' USDT');setText('#ticket-tp',notional?signed(notional*take/100):'—');setText('#ticket-sl',notional?signed(notional*stop/100):'—');setText('#ticket-fee',notional?'~'+num(fee):'—');
 setText('#preview-title',selected+' · '+mode+' · '+side+' · '+f.type.value);setText('#preview-live',price(live));setText('#preview-entry',price(entry||null));setText('#preview-quantity',quantity?qty(quantity):'CHỜ AUTO');setText('#preview-cash',notional?num(notional+fee):'—');setText('#preview-fee',notional?'~'+num(fee):'—');setText('#preview-route',f.type.value==='LOCAL_LIMIT'?'LOCAL':f.type.value);
 setText('#preview-tp',entry?price(entry*(1+take/100)):'—');setText('#preview-tp-money',notional?signed(notional*take/100)+' USDT':take+'%');setText('#preview-sl',entry?price(entry*(1+stop/100)):'—');setText('#preview-sl-money',notional?signed(notional*stop/100)+' USDT':stop+'%');
 const own=state.positions.find(p=>p.mode===mode&&p.symbol===selected), protect=own?.metrics?.protect;
 setText('#protect-caption','PROTECT · '+((own?own.protect_enabled:entryProtect)?'ON':'OFF'));
 setText('#preview-protect',protect?protect.stage+' '+protect.policy+' @ '+price(protect.trigger):'ARM '+r.normal_arm_pct+'% · '+r.normal_policy);
 setText('#preview-protect-detail',own?'MFE '+signed(own.metrics?.mfe)+'% · MAE '+signed(own.metrics?.mae)+'%':'Lượt mua mới · DYNAMIC '+r.dynamic_policy+' · lùi '+r.normal_giveback_pct+' điểm %');
 setText('#preview-e',own?.exit?.reason==='E'?'ĐANG CÓ ĐIỀU KIỆN':decision().sell_condition?'ĐẠT ĐIỀU KIỆN':'CHỜ ĐIỀU KIỆN');setText('#preview-e-detail',r.indicator_exit_policy+' · '+r.indicator_exit_sell_pct+'% '+(r.indicator_exit_sell_basis==='ORIGINAL'?'GỐC':'CÒN'));
 setText('#preview-status',mode==='REAL'&&!state.capabilities.trade?'CHỈ ĐỌC':mode==='PAPER'?'MÔ PHỎNG':'SẴN SÀNG');
}
$('#order-symbol').onchange=e=>selectSymbol(e.target.value);$('#order-form').oninput=e=>{if(e.target.name==='type'&&e.target.value==='MARKET')$('#order-form').elements.price.value='';requestId=uid();updateEstimate();refreshAutoTicket();};
function changeBook(book){mode=book;selectedRows.clear();selectionAnchor=null;requestId=uid();if(state)render();}
for(const prefix of ['mode','book'])for(const book of ['REAL','PAPER'])$('#'+prefix+'-'+book.toLowerCase()).onclick=()=>changeBook(book);
$('#entry-protect').onclick=()=>{entryProtect=!entryProtect;requestId=uid();render();};$('#entry-e').onclick=()=>{entryE=!entryE;requestId=uid();render();};
function autoTicketKey(){const f=$('#order-form').elements;return [mode,selected,f.type.value,f.type.value==='MARKET'?'':f.price.value].join(':');}
function previewTicket(){const f=$('#order-form').elements;return api('/api/preview','POST',{mode,symbol:selected,side:'BUY',type:f.type.value,price:f.price.value});}
async function refreshAutoTicket(force=false){
 if(!state||autoBusy||$('#order-form').elements.quantity.value!=='')return;
 const key=autoTicketKey();if(!force&&autoTicket.key===key&&Date.now()-autoTicket.at<5000)return;
 autoBusy=true;autoTicket={key,at:Date.now(),quantity:null,reason:''};
 try{const preview=await previewTicket();if(key!==autoTicketKey())return;autoTicket={key,at:Date.now(),quantity:preview.quantity,reason:preview.reason};updateEstimate();$('#auto-quantity').title=preview.reason;}
 catch(e){if(key===autoTicketKey()){autoTicket={key,at:Date.now(),quantity:null,reason:e.message};$('#auto-quantity').title=e.message;updateEstimate();}}
 finally{autoBusy=false;}
}
$('#order-form').onsubmit=async e=>{
 e.preventDefault();const f=e.target.elements,button=$('#submit-order');button.disabled=true;
 try{
  const quantity=f.quantity.value===''?(await previewTicket()).quantity:f.quantity.value;
  if(Number(quantity)<=0)throw Error('AUTO chưa có khối lượng hợp lệ. Kiểm tra vốn và giới hạn cặp.');
  const data={mode,side:'BUY',symbol:selected,type:f.type.value,quantity,price:f.type.value==='MARKET'?'':f.price.value,request_id:requestId,entry_plan:{protect_enabled:entryProtect,e_enabled:entryE}};
  if(f.take_profit.value!=='')data.entry_plan.take_profit_pct=f.take_profit.value;
  if(f.stop_loss.value!=='')data.entry_plan.initial_sl_pct=f.stop_loss.value;
  if(mode==='REAL'&&!await askConfirm('GỬI LỆNH MUA THẬT '+quantity+' '+selected+' · '+f.type.value+'?'))return;
  const result=await api('/api/orders','POST',data);toast('Đã tiếp nhận lệnh '+result.id.slice(0,8));requestId=uid();autoTicket.at=0;await poll();
 }catch(error){toast(error.message,true);}finally{button.disabled=mode==='REAL'&&!state?.capabilities.trade;}
};
$('#auto-quantity').onclick=async()=>{autoTicket.at=0;await refreshAutoTicket(true);if(autoTicket.reason)toast(autoTicket.reason,!Number(autoTicket.quantity));};
$('#refresh').onclick=async()=>{try{await api('/api/refresh','POST',{});toast('Đang cập nhật dữ liệu.');}catch(e){toast(e.message,true);}};
$('#bot-toggle').onclick=async()=>{if(!state)return;const on=!state.bot[mode];if(on&&mode==='REAL'&&!await askConfirm('Bật BOT REAL: cho phép mua và bán tự động theo RULE trên Binance?'))return;try{await api('/api/bot','POST',{mode,enabled:on});toast('BOT '+mode+' '+(on?'ON':'OFF'));await poll();}catch(e){toast(e.message,true);}};
for(const kind of ['loss','whipsaw'])$('#unlock-'+kind).onclick=async()=>{if(!await askConfirm(`Mở khóa ${kind==='loss'?'chuỗi lỗ':'WHIPSAW'} cho ${selected} / ${mode}?`))return;try{await api('/api/unlock','POST',{mode,symbol:selected,kind});toast('Đã mở đúng khóa được chọn.');poll();}catch(e){toast(e.message,true);}};
$('#ema-bypass').onclick=async()=>{try{await api('/api/ema-override','POST',{mode,symbol:selected,enabled:!decision().override});toast('Đã đổi bỏ cắt tạm thời; các điều kiện BUY khác vẫn áp dụng.');poll();}catch(e){toast(e.message,true);}};
$('#logout').onclick=async()=>{await api('/logout','POST',{});location.href='/login';};
const labels={ma_period:'MA bối cảnh (ngày)',pivot_left:'Pivot trái',pivot_right:'Pivot phải',pivot_horizontal_pct:'Sai số đi ngang (%)',ma_zone_pct:'Vùng MA (%)',confirm_sessions:'Xác nhận bối cảnh (ngày)',volume_confirmation:'Hiển thị xác nhận volume',volume_average_sessions:'Volume nền (ngày)',high_volume_ratio:'Volume cao (lần)',low_volume_ratio:'Volume thấp (lần)',buy_ema_fast:'EMA BUY nhanh',buy_ema_slow:'EMA BUY chậm',sell_ema_fast:'EMA SELL nhanh',sell_ema_slow:'EMA SELL chậm',rsi_period:'RSI chu kỳ',buy_signal_use_ema:'BUY dùng EMA',buy_signal_use_rsi:'BUY dùng RSI',buy_signal_require_ema_cross:'BUY cần EMA cắt lên',buy_ema_cross_mode:'Bộ nhớ giao cắt',buy_ema_cross_sessions:'Cửa sổ giao cắt (ngày)',buy_ema_va_lookback_enabled:'VA quét trước',buy_volume_enabled:'Lọc volume BUY',buy_volume_average_sessions:'Volume BUY nền (ngày)',buy_volume_min_ratio:'Volume BUY tối thiểu (lần)',sell_signal_use_ema:'E dùng EMA SELL',sell_signal_use_rsi:'E dùng RSI',sell_signal_require_ema_cross:'E cần cắt xuống mới',indicator_exit_policy:'E: AUTO / ALERT',indicator_exit_sell_pct:'E bán (%)',indicator_exit_sell_basis:'E cơ sở khối lượng',indicator_exit_repeat_enabled:'E lặp theo đợt điều kiện',max_positions:'Số mã tối đa',initial_sl_pct:'SL lần đầu (%)',reentry_sl_pct:'SL vào lại (%)',loss_lock_count:'Khóa sau số lượt lỗ',loss_lock_hours:'Thời gian khóa lỗ (giờ)',loss_lock_mode:'Kiểu khóa lỗ',no_compound_enabled:'Không cộng dồn vốn từ lãi',take_profit_pct:'TP đạt lãi (%)',take_profit_sell_pct:'TP bán (% gốc)',normal_arm_pct:'ARM kích hoạt (%)',normal_sell_pct:'ARM bán (%)',normal_sell_basis:'ARM cơ sở khối lượng',normal_giveback_pct:'ARM lùi / bước lặp (điểm %)',normal_atr_activation_multiplier:'DYNAMIC START × ATR',normal_atr_multiplier:'DYNAMIC TRAIL × ATR',normal_atr_activation_enabled:'Bật DYNAMIC START',normal_atr_trail_enabled:'Bật sàn ATR',normal_retention_pct:'DYNAMIC giữ lãi (%)',normal_retention_until_pct:'Giữ lãi đến MFE (%)',normal_retention_enabled:'Bật giữ lãi',normal_retention_until_enabled:'Giới hạn giữ lãi đến MFE',normal_policy:'ARM: AUTO / ALERT',dynamic_policy:'DYNAMIC: AUTO / ALERT',normal_dynamic_enabled:'DYNAMIC trước ARM',normal_repeat_enabled:'ARM lặp',normal_repeat_below_arm_enabled:'ARM lặp dưới ngưỡng kích hoạt',dynamic_sell_pct:'DYNAMIC bán (%)',dynamic_sell_basis:'DYNAMIC cơ sở khối lượng',dynamic_repeat_enabled:'DYNAMIC lặp',whipsaw_enabled:'Bật khóa WHIPSAW',whipsaw_n:'Số giao cắt gây khóa',whipsaw_x:'Cửa sổ WHIPSAW (ngày)',buy_confirmation_enabled:'Chờ xác nhận BUY',buy_confirmation_minutes:'Chờ BUY (phút)',buy_confirmation_require_ema:'Xác nhận BUY cần EMA',buy_confirmation_require_rsi:'Xác nhận BUY cần RSI',buy_window_enabled:'Giới hạn giờ BUY',buy_window_start:'Mua từ (UTC+7)',buy_window_end:'Mua đến (UTC+7)'};
const choices={buy_ema_cross_mode:{VA:'VA · giữ đến khi sử dụng',SESSIONS:'X NGÀY · có hạn'},normal_policy:{AUTO:'AUTO',ALERT:'ALERT'},dynamic_policy:{AUTO:'AUTO',ALERT:'ALERT'},indicator_exit_policy:{AUTO:'AUTO',ALERT:'ALERT'},normal_sell_basis:{ORIGINAL:'GỐC',REMAINING:'CÒN LẠI'},dynamic_sell_basis:{ORIGINAL:'GỐC',REMAINING:'CÒN LẠI'},indicator_exit_sell_basis:{ORIGINAL:'GỐC',REMAINING:'CÒN LẠI'},loss_lock_mode:{TIMED:'KHÓA GIỜ',MANUAL:'MỞ TAY'}};
function field(name,value,label,options){if(typeof value==='boolean')return `<label class="check-field"><input name="${name}" type="checkbox" ${value?'checked':''}>${esc(label)}</label>`;if(options)return `<label>${esc(label)}<select name="${name}">${Object.entries(options).map(([k,v])=>`<option value="${esc(k)}" ${k===String(value)?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;return `<label>${esc(label)}<input name="${name}" type="${typeof value==='number'?'number':name.includes('window_')?'time':'text'}" step="any" value="${esc(value)}"></label>`;}
function collectRule(form,baseline){const r=structuredClone(baseline);for(const [k,v]of Object.entries(r)){const f=form.elements['r.'+k];if(!f)continue;r[k]=typeof v==='boolean'?f.checked:typeof v==='number'?Number(f.value):f.value;}for(const k of Object.keys(r.exposure))r.exposure[k]=Number(form.elements['exposure.'+k].value)/100;return r;}
function showModal(title,body){setText('#modal-title',title);$('#modal-body').innerHTML=body;if(!$('#modal').open)$('#modal').showModal();}
function closeModal(){$('#modal').close();modalName='';}
$('#modal-close').onclick=closeModal;$('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))closeModal();});$('#modal').addEventListener('close',()=>modalName='');
async function openModal(name){if(!state)return;modalName=name;try{
 if(name==='rules')renderRules();
 if(['info','portfolio','legend','health','order-help'].includes(name))renderUtility(name);
 if(name==='priority')renderPriority();
 if(name==='connections')renderConnections();
 if(name==='history'){historyOffset=0;showModal('Lịch sử & tín hiệu',`<div class="toolbar"><label>Nguồn<select id="history-kind"><option value="">Tất cả sự kiện</option><option value="FILL">Khớp lệnh & PNL</option><option value="ORDER">Lệnh</option><option value="NOTICE">Thông báo</option><option value="TRACE">TRACE định kỳ</option><option value="EXTERNAL_FILL">Lịch sử Binance</option></select></label><button id="history-load">Làm mới</button><button id="history-sync">Đọc lịch sử ${esc(selected)}</button></div><div id="history-content" class="table-scroll"></div><div class="modal-actions"><button id="history-prev">← Trước</button><span id="history-page"></span><button id="history-next">Sau →</button><button id="history-csv">Xuất CSV</button></div>`);$('#history-load').onclick=()=>{historyOffset=0;loadHistory();};$('#history-kind').onchange=$('#history-load').onclick;$('#history-prev').onclick=()=>{historyOffset=Math.max(0,historyOffset-100);loadHistory();};$('#history-next').onclick=()=>{historyOffset+=100;loadHistory();};$('#history-sync').onclick=async()=>{try{const r=await api('/api/sync-history','POST',{symbol:selected});toast(r.imported+' bản ghi mới. '+r.message);loadHistory();}catch(e){toast(e.message,true);}};loadHistory();}
 if(name==='volume')renderVolume();
 if(name==='backtest')renderBacktest();
}catch(e){toast(e.message,true);}}
function tabStrip(group,items,phase=false){return `<div class="tab-strip ${phase?'phase-tabs':''}" role="tablist">${items.map(([id,label],i)=>`<button type="button" role="tab" aria-selected="${i===0}" data-tab-group="${group}" data-tab="${id}" class="${i===0?'active':''}">${esc(label)}</button>`).join('')}</div>`;}
function card(title,body){return `<section class="rule-card"><h3 class="group-title">${esc(title)}</h3>${body}</section>`;}
function ruleFields(r,keys){return '<div class="settings-grid">'+keys.map(k=>field('r.'+k,r[k],labels[k]||k,choices[k])).join('')+'</div>';}
function priorityEditor(s){return `<label>Các cặp theo dõi<input name="watchlist" value="${esc(s.watchlist.join(', '))}"></label><div class="toolbar"><button type="button" data-priority-rebuild>CẬP NHẬT DANH SÁCH</button><button type="button" data-modal="volume">LỌC VOLUME</button></div><div data-priority-rows></div>`;}
function bindPriority(form,s){
 const render=()=>{
  const previous={};form.querySelectorAll('[data-priority]').forEach(row=>{const n=row.dataset.priority;previous[n]={mode:form.elements['p.mode.'+n].value,cap:form.elements['p.cap.'+n].value,use_pct:form.elements['p.use.'+n].value,max_orders:form.elements['p.max.'+n].value};});
  const names=[...new Set(form.elements.watchlist.value.split(/[,;\s]+/).filter(Boolean).map(x=>x.toUpperCase().endsWith('USDT')?x.toUpperCase():x.toUpperCase()+'USDT'))];
  form.querySelector('[data-priority-rows]').innerHTML='<div class="table-scroll">'+table(['Cặp','Chế độ','Hạn mức USDT','Sử dụng %','MAX lượt'],names.map(n=>{const v=previous[n]||s.priority[n]||{};return `<tr data-priority="${esc(n)}"><td>${esc(n)}</td><td><select name="p.mode.${esc(n)}"><option value="">THƯỜNG</option><option value="AUTO" ${v.mode==='AUTO'?'selected':''}>AUTO</option><option value="ALERT" ${v.mode==='ALERT'?'selected':''}>ALERT</option></select></td><td><input name="p.cap.${esc(n)}" type="number" min="0" step="any" value="${esc(v.cap||0)}"></td><td><input name="p.use.${esc(n)}" type="number" min="0" max="100" value="${esc(v.use_pct??100)}"></td><td><input name="p.max.${esc(n)}" type="number" min="1" max="20" value="${esc(v.max_orders??1)}"></td></tr>`;}))+'</div>';
 };form.querySelector('[data-priority-rebuild]').onclick=render;render();
}
function collectPriority(form){const priority={};form.querySelectorAll('[data-priority]').forEach(row=>{const n=row.dataset.priority,f=form.elements,mode=f['p.mode.'+n].value;if(mode)priority[n]={mode,cap:f['p.cap.'+n].value,use_pct:Number(f['p.use.'+n].value),max_orders:Number(f['p.max.'+n].value)};});return {watchlist:form.elements.watchlist.value,priority};}
function renderRules(preset){
 const s=structuredClone(state.settings);if(preset)s.rules=preset;const r=s.rules;
 const context=Object.keys(r).filter(k=>!k.startsWith('buy_')&&!k.startsWith('sell_')&&!k.startsWith('indicator_')&&!k.startsWith('normal_')&&!k.startsWith('dynamic_')&&!k.startsWith('take_profit')&&!k.includes('_sl_pct')&&!k.startsWith('loss_')&&!k.startsWith('whipsaw_')&&!['exposure','rsi_period','max_positions','no_compound_enabled'].includes(k));
 const entry=Object.keys(r).filter(k=>k.startsWith('buy_')||k==='rsi_period');
 const guard=Object.keys(r).filter(k=>k.startsWith('loss_')||k.startsWith('whipsaw_')||['max_positions','no_compound_enabled'].includes(k));
 const arm=['normal_policy','normal_arm_pct','normal_giveback_pct','normal_sell_pct','normal_sell_basis','normal_repeat_enabled','normal_repeat_below_arm_enabled'];
 const dynamic=Object.keys(r).filter(k=>(k.startsWith('normal_')&&!arm.includes(k))||k.startsWith('dynamic_'));
 const indicator=Object.keys(r).filter(k=>k.startsWith('sell_')||k.startsWith('indicator_'));
 showModal('Rice Reborn · RULE',`<form id="rules-form">${tabStrip('rule-main',[['business','NGHIỆP VỤ'],['em','E/M'],['execution','THỰC THI']])}<div class="rule-content">
 <div class="tab-pane" data-pane-group="rule-main" data-pane="business">${tabStrip('rule-phase',[['p1','PHASE 1 · THỊ TRƯỜNG'],['p2','PHASE 2 · ENTRY BUY'],['p3','PHASE 3 · VỐN & BẢO VỆ']],true)}
 <div data-pane-group="rule-phase" data-pane="p1"><div class="rule-summary">P1 · Phân bổ vốn theo BTC. ETH đối chiếu. Override VA mặc định 100%.</div>${card('TỶ TRỌNG COIN / TIỀN',`<div class="settings-grid">${field('market_override',s.market_override,'CHỌN TAY tỷ trọng')}${field('market_exposure_pct',s.market_exposure_pct,'Tỷ trọng coin (%)')}</div><div class="settings-grid">${Object.entries(r.exposure).map(([k,v])=>field('exposure.'+k,v*100,k+' (%)')).join('')}</div>`)}${card('BỐI CẢNH THỊ TRƯỜNG · BTC',ruleFields(r,context))}</div>
 <div data-pane-group="rule-phase" data-pane="p2" hidden><div class="rule-summary">ENTRY · EMA / RSI nến 1D UTC. VA giữ giao cắt chưa dùng; X ngày giới hạn tuổi cắt.</div>${card('EMA / RSI · BỘ NHỚ & XÁC NHẬN BUY',ruleFields(r,entry))}</div>
 <div data-pane-group="rule-phase" data-pane="p3" hidden>${card('VỐN & KHÓA BUY',ruleFields(r,guard))}${card('PRIORITY · NGÂN SÁCH USDT',`<div class="settings-grid">${field('capital_usdt',s.capital_usdt,'Ngân sách REAL')}${field('paper_capital',s.paper_capital,'Ngân sách PAPER')}</div>${priorityEditor(s)}`)}</div></div>
 <div class="tab-pane" data-pane-group="rule-main" data-pane="em" hidden>${card('SL / TP · THOÁT VỊ THẾ',ruleFields(r,['initial_sl_pct','reentry_sl_pct','take_profit_pct','take_profit_sell_pct']))}<div class="em-cards">${card('PROTECT · ARM',ruleFields(r,arm))}${card('DYNAMIC · TRƯỚC ARM',ruleFields(r,dynamic))}${card('E · TÍN HIỆU CHỈ BÁO',ruleFields(r,indicator))}</div></div>
 <div class="tab-pane" data-pane-group="rule-main" data-pane="execution" hidden>${card('THỰC THI · BINANCE SPOT',`<div class="settings-grid">${field('signal_mode',s.signal_mode,'Mode nến',{REALTIME:'REALTIME',CLOSED:'CLOSED'})}${field('indicator_interval',s.indicator_interval,'Nhịp quan sát',{TICK:'TICK','1M':'1 phút','2M':'2 phút','5M':'5 phút'})}${field('manual_sell_pause_minutes',s.manual_sell_pause_minutes,'Nghỉ BUY sau bán tay (phút)')}${field('trace_enabled',s.trace_enabled,'Lưu TRACE')}${field('trace_minutes',s.trace_minutes,'Nhịp TRACE (phút)')}</div>`)}<div class="rule-summary">REAL cần API có quyền Spot Trade. BOT khởi động OFF. PAPER dùng sổ riêng. Crypto giao dịch 24/7; không có ATO/ATC hoặc T+2.</div></div></div>
 <div class="modal-actions"><span class="small-muted">Backend nhận cấu hình sau khi lưu.</span><button type="button" id="reset-va">NẠP PRESET VA</button><button class="primary" type="submit">LƯU THAY ĐỔI</button></div></form>`);
 bindPriority($('#rules-form'),s);
 $('#reset-va').onclick=async()=>{const p=await api('/api/preset');renderRules(p.rules);};
 $('#rules-form').onsubmit=async e=>{e.preventDefault();try{const f=e.target,data={rules:collectRule(f,r),...collectPriority(f)};for(const k of ['signal_mode','indicator_interval','market_override','market_exposure_pct','capital_usdt','paper_capital','trace_enabled','trace_minutes','manual_sell_pause_minutes'])data[k]=typeof s[k]==='boolean'?f.elements[k].checked:typeof s[k]==='number'?Number(f.elements[k].value):f.elements[k].value;await api('/api/settings','PUT',data);toast('Đã lưu RULE.');closeModal();poll();}catch(e){toast(e.message,true);}};
}
function renderPriority(){const s=state.settings;showModal('Rice Reborn · Cặp giao dịch / Priority',`<form id="priority-form">${card('WATCHLIST / PRIORITY',priorityEditor(s))}<div class="modal-actions"><button class="primary">LƯU WATCHLIST / PRIORITY</button></div></form>`);bindPriority($('#priority-form'),s);$('#priority-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/settings','PUT',collectPriority(e.target));toast('Đã lưu danh sách.');closeModal();poll();}catch(e){toast(e.message,true);}};}
function renderConnections(){
 const s=state.settings;
 showModal('Rice Reborn · KẾT NỐI',`${tabStrip('connection',[['binance','BINANCE'],['symbols','CẶP GIAO DỊCH'],['telegram','TELEGRAM']])}<div class="rule-content">
 <div data-pane-group="connection" data-pane="binance">${state.demo?'<div class="warning-box">DEMO · Không lưu API hoặc gửi Telegram.</div>':''}<form data-integration-form>${card('API BINANCE · HMAC',`<div class="settings-grid"><label>API KEY<input name="BINANCE_API_KEY" type="password" autocomplete="off" placeholder="${state.api_configured?'Đã lưu':'Chưa cấu hình'}"></label><label>API SECRET<input name="BINANCE_API_SECRET" type="password" autocomplete="new-password" placeholder="Để trống giữ nguyên"></label></div><div class="modal-actions"><button type="button" id="connection-refresh">KIỂM TRA ĐÃ LƯU</button><button class="primary">LƯU API</button></div>`)}</form>${card('TRẠNG THÁI & QUYỀN',`<div class="settings-grid"><div>API<br><strong>${esc(state.capabilities.status)}</strong></div><div>ĐỌC TÀI KHOẢN<br><strong>${state.capabilities.read?'CÓ':'CHƯA XÁC MINH'}</strong></div><div>GIAO DỊCH SPOT<br><strong>${state.capabilities.trade?'CÓ':'KHÓA REAL'}</strong></div></div><p class="modal-intro">Read vẫn theo dõi và nhận tin. Quyền Trade không tự bật BOT. Để trống secret để giữ giá trị đã lưu.</p>`)}</div>
 <div data-pane-group="connection" data-pane="symbols" hidden><form id="connection-priority">${card('WATCHLIST / PRIORITY',priorityEditor(s))}<div class="modal-actions"><button class="primary">LƯU DANH SÁCH</button></div></form></div>
 <div data-pane-group="connection" data-pane="telegram" hidden><form data-integration-form>${card('KẾT NỐI TELEGRAM',`<div class="settings-grid"><label>BOT TOKEN<input name="TELEGRAM_BOT_TOKEN" type="password" autocomplete="new-password" placeholder="Không hiển thị token đã lưu"></label><label>CHAT ID<input name="TELEGRAM_CHAT_ID" placeholder="Để trống giữ nguyên"></label></div><div class="modal-actions"><button type="button" id="telegram-test">GỬI TIN THỬ</button><button class="primary">LƯU TELEGRAM</button></div>`)}</form><form id="telegram-form">${card('THÔNG BÁO / COOLDOWN',`<div class="settings-grid">${field('telegram_enabled',s.telegram_enabled,'Telegram ON')}${field('telegram_replay_missed',s.telegram_replay_missed,'Gửi bù tin cũ')}${field('telegram_batch_minutes',s.telegram_batch_minutes,'Gom BUY (phút)')}${Object.entries(s.telegram_cooldowns).map(([k,v])=>field('c.'+k,v,'Giãn '+k+' (phút)')).join('')}${Object.entries(s.telegram_groups).map(([k,v])=>field('g.'+k,v,{signals:'Tín hiệu BUY / mất BUY',positions:'Quản lý thoát / bảo vệ',orders:'Lệnh & khớp',system:'Hệ thống'}[k])).join('')}</div>`)}<div class="modal-actions"><button class="primary">LƯU THÔNG BÁO</button></div></form></div></div>`);
 $$('[data-integration-form]').forEach(form=>form.onsubmit=async e=>{e.preventDefault();try{const data={};for(const [key,value]of new FormData(form))if(value.trim())data[key]=value.trim();await api('/api/integrations','POST',data);toast('Đã lưu kết nối. BOT OFF.');closeModal();poll();}catch(e){toast(e.message,true);}});
 $('#connection-refresh').onclick=async()=>{try{await api('/api/refresh','POST',{});toast('Đang xác minh cấu hình đã lưu.');}catch(e){toast(e.message,true);}};
 $('#telegram-test').onclick=async()=>{try{await api('/api/telegram/test','POST',{});toast('Đã gửi tin thử Telegram.');}catch(e){toast(e.message,true);}};
 bindPriority($('#connection-priority'),s);$('#connection-priority').onsubmit=async e=>{e.preventDefault();try{await api('/api/settings','PUT',collectPriority(e.target));toast('Đã lưu danh sách.');closeModal();poll();}catch(e){toast(e.message,true);}};
 $('#telegram-form').onsubmit=async e=>{e.preventDefault();try{const f=e.target.elements;await api('/api/settings','PUT',{telegram_enabled:f.telegram_enabled.checked,telegram_replay_missed:f.telegram_replay_missed.checked,telegram_batch_minutes:Number(f.telegram_batch_minutes.value),telegram_cooldown_minutes:s.telegram_cooldown_minutes,telegram_cooldowns:Object.fromEntries(Object.keys(s.telegram_cooldowns).map(k=>[k,Number(f['c.'+k].value)])),telegram_groups:Object.fromEntries(Object.keys(s.telegram_groups).map(k=>[k,f['g.'+k].checked]))});toast('Đã lưu cách nhận tin.');closeModal();poll();}catch(e){toast(e.message,true);}};
}
async function loadHistory(){try{const rows=await api('/api/history?mode='+mode+'&kind='+encodeURIComponent($('#history-kind').value)+'&offset='+historyOffset);$('#history-content').innerHTML=table(['Thời điểm UTC+7','Nhóm / cặp','Thông tin'],rows.map(x=>{const p=x.payload;return `<tr><td>${date(x.at)}</td><td>${esc(x.kind)}<small>${esc(x.mode)} ${esc(x.symbol)}</small></td><td><details><summary>${esc(p.text||p.reason||(p.side?`${p.side} ${p.qty||p.quantity} @ ${p.price}`:p.action||'Xem chi tiết'))}</summary><pre class="history-detail">${esc(JSON.stringify(p,null,2))}</pre></details></td></tr>`;}));setText('#history-page',(historyOffset+1)+'–'+(historyOffset+rows.length));$('#history-csv').onclick=()=>downloadCSV(rows.map(x=>({time:date(x.at),kind:x.kind,mode:x.mode,symbol:x.symbol,...x.payload})),'RICE_HISTORY.csv');}catch(e){toast(e.message,true);}}
function downloadCSV(rows,name){if(!rows.length){toast('Không có dữ liệu để xuất.');return;}const keys=[...new Set(rows.flatMap(x=>Object.keys(x)))];const cell=v=>'"'+String(typeof v==='object'?JSON.stringify(v):v??'').replaceAll('"','""')+'"';const blob=new Blob(['\uFEFF'+keys.map(cell).join(',')+'\r\n'+rows.map(x=>keys.map(k=>cell(x[k])).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
function renderVolume(){showModal('Lọc biến động volume',`<p class="modal-intro">So sánh volume bình quân 5 hoặc 10 ngày với khoảng trước đó. Top thanh khoản là xếp theo volume quote Binance, không phải vốn hóa.</p><form id="volume-form"><div class="settings-grid">${field('source','WATCHLIST','Nguồn quét',{WATCHLIST:'Watchlist',LIQUIDITY:'Top thanh khoản Binance'})}${field('count',20,'Số cặp quét')}${field('period','5','Chu kỳ (ngày)',{'5':'5 ngày','10':'10 ngày'})}${field('threshold',20,'Ngưỡng biến động (%)')}${field('direction','BOTH','Hướng',{BOTH:'Cả hai',UP:'Tăng',DOWN:'Giảm'})}</div><div class="modal-actions"><button class="primary">QUÉT VOLUME</button><button type="button" id="volume-export">Xuất CSV</button></div></form><div id="volume-result" class="table-scroll"></div>`);let rows=[];$('#volume-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const d=Object.fromEntries(new FormData(e.target));const r=await api('/api/volume-scan','POST',d);rows=r.rows;$('#volume-result').innerHTML=table(['Cặp','TB trước','TB gần nhất','Biến động'],rows.map(x=>`<tr><td>${esc(x.symbol)}</td><td>${qty(x.before)}</td><td>${qty(x.after)}</td><td class="${color(x.change_pct)}">${signed(x.change_pct)}%</td></tr>`));toast('Đã quét '+r.scanned+' cặp · '+rows.length+' kết quả.');}catch(e){toast(e.message,true);}finally{b.disabled=false;}};$('#volume-export').onclick=()=>downloadCSV(rows,'RICE_VOLUME.csv');}
function renderBacktest(){const now=new Date(),end=new Date(now-86400000).toISOString().slice(0,10),start=new Date(now-31*86400000).toISOString().slice(0,10);showModal('Backtest · VA / Binance Spot',`<p class="modal-intro">Chạy riêng khỏi tài khoản REAL, dùng bản chụp RULE hiện tại. Fee 0,1% và slippage 0,05% là giả định mô phỏng. Không phát lệnh thật hoặc Telegram.</p><form id="backtest-form"><label>Các cặp<input name="symbols" value="${esc(selected)}"></label><div class="settings-grid"><label>Từ ngày UTC<input type="date" name="start" value="${start}" required></label><label>Đến ngày UTC<input type="date" name="end" value="${end}" required></label>${field('capital','1000','Vốn mô phỏng USDT')}${field('simulation','DAILY','Mô phỏng',{DAILY:'DAILY · OHLC bảo thủ',REPLAY:'REPLAY · nến phút'})}</div><div class="warning-box">Backtest giữ MAX LỆNH=1 như MayinTien. Nến phút không tái dựng mọi dao động tick; kết quả không chứng minh khả năng khớp LIVE.</div><div class="modal-actions"><button class="primary">CHẠY BACKTEST</button></div></form><h3 class="group-title">Các lượt chạy</h3><div id="backtest-jobs"></div>`);$('#backtest-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/backtests','POST',Object.fromEntries(new FormData(e.target)));toast('Đã xếp backtest. Có thể đóng hộp này trong lúc chạy.');poll();}catch(e){toast(e.message,true);}};renderJobs();}
function renderJobs(){const el=$('#backtest-jobs');if(!el)return;el.innerHTML=state.jobs.length?state.jobs.map(j=>`<article class="job-card"><h3>${esc(j.symbols.join(', '))} <span class="badge ${j.status==='COMPLETE'?'green':j.status==='FAILED'?'red':'neutral'}">${esc(j.status)}</span></h3><p>${esc(j.start)} → ${esc(j.end)} · ${esc(j.simulation)}</p><progress max="100" value="${Number(j.progress)||0}"></progress><p>${esc(j.message)}</p>${j.result?`<p>PNL ${signed(j.result.pnl)} USDT · ${j.result.fills} lần khớp · NAV ${num(j.result.ending_nav)} USDT</p>${j.result.files.map(n=>`<a href="/api/backtests/${encodeURIComponent(j.id)}/files/${encodeURIComponent(n)}">↓ ${esc(n)}</a>`).join('')}`:''}${['RUNNING','QUEUED'].includes(j.status)?`<button data-cancel-job="${j.id}">Hủy lượt chạy</button>`:''}</article>`).join(''):'<p class="small-muted">Chưa có lượt backtest.</p>';}
function positionModal(id){const p=state.positions.find(x=>x.id===id);if(!p)return;modalName='position';const keys=['initial_sl_pct','normal_policy','normal_arm_pct','normal_giveback_pct','normal_sell_pct','normal_sell_basis','normal_repeat_enabled','normal_repeat_below_arm_enabled','dynamic_policy','dynamic_sell_pct','dynamic_sell_basis','dynamic_repeat_enabled','indicator_exit_policy','indicator_exit_sell_pct','indicator_exit_sell_basis','indicator_exit_repeat_enabled','take_profit_pct','take_profit_sell_pct'];showModal(p.symbol+' · vị thế '+p.id.slice(0,8),`<p class="modal-intro">${p.mode} · đang giữ ${qty(p.quantity)} · giá vốn ${price(p.entry)} · MFE ${signed(p.metrics?.mfe)}% / MAE ${signed(p.metrics?.mae)}%</p><form id="position-form"><div class="settings-grid">${['managed','bot_trade','auto_sell','sl_enabled','tp_enabled','protect_enabled','e_enabled'].map(k=>field(k,p[k]??true,{managed:'Nhận quản lý vị thế',bot_trade:'Cho phép BOT ở vị thế',auto_sell:'Cho phép tự bán',sl_enabled:'SL',tp_enabled:'TP',protect_enabled:'ARM / DYNAMIC',e_enabled:'E chỉ báo'}[k])).join('')}${field('sl_price',p.sl_price||'0','Giá SL riêng (0 = RULE)')}${field('tp_price',p.tp_price||'0','Giá TP riêng (0 = RULE)')}</div><h3 class="group-title">Cấu hình thoát riêng</h3><label class="check-field"><input type="checkbox" name="custom" ${Object.keys(p.overrides||{}).length?'checked':''}>Dùng cấu hình riêng; bỏ chọn để về RULE</label><div class="settings-grid">${keys.map(k=>field('r.'+k,p.overrides?.[k]??state.settings.rules[k],labels[k],choices[k])).join('')}</div><div class="modal-actions"><button type="button" id="position-remeasure">Đo lại MAE / MFE</button><button class="primary">LƯU VỊ THẾ</button></div></form>`);$('#position-form').onsubmit=async e=>{e.preventDefault();try{const f=e.target.elements,d={};for(const k of ['managed','bot_trade','auto_sell','sl_enabled','tp_enabled','protect_enabled','e_enabled'])d[k]=f[k].checked;d.sl_price=f.sl_price.value;d.tp_price=f.tp_price.value;d.overrides={};if(f.custom.checked)for(const k of keys){const v=state.settings.rules[k],x=f['r.'+k];d.overrides[k]=typeof v==='boolean'?x.checked:typeof v==='number'?Number(x.value):x.value;}await api('/api/positions/'+id,'PATCH',d);toast('Đã lưu vị thế.');closeModal();poll();}catch(e){toast(e.message,true);}};$('#position-remeasure').onclick=async()=>{const days=await askInput('Số ngày cần đo lại (1–30), tính từ sau lúc mở vị thế. Sau khi đo tự bán tạm OFF.','3');if(days===null)return;try{await api('/api/positions/'+id+'/remeasure','POST',{days:Number(days)});toast('Đang đo lại MAE/MFE ở nền.');}catch(e){toast(e.message,true);}};}
function adoptModal(asset){modalName='adopt';const b=state.holdings.find(x=>x.asset===asset);showModal('Theo dõi vị thế '+asset,`<p class="modal-intro">Số dư Spot không cung cấp giá vốn. Nhập giá vốn đã xác minh, hoặc để 0 để chỉ theo dõi kỹ thuật. Vị thế mới chưa bật tự quản lý.</p><form id="adopt-form"><div class="settings-grid">${field('quantity',b.free,'Khối lượng cần phân bổ')}${field('entry','0','Giá vốn USDT')}</div><div class="modal-actions"><button class="primary">THÊM VỊ THẾ THEO DÕI</button></div></form>`);$('#adopt-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/positions/adopt','POST',{symbol:asset+'USDT',...Object.fromEntries(new FormData(e.target))});toast('Đã thêm vị thế theo dõi.');closeModal();poll();}catch(e){toast(e.message,true);}};}
function renderUtility(name){
 if(name==='portfolio'){
  const ps=state.positions.filter(p=>p.mode===mode);
  const rows=mode==='PAPER'?ps.map(p=>`<tr><td>${esc(p.symbol)}</td><td>${qty(p.quantity)}</td><td>${price(p.entry)}</td><td>${price(p.price)}</td><td class="${color(p.pnl)}">${signed(p.pnl)}</td><td><button data-position="${esc(p.id)}">THIẾT LẬP</button></td></tr>`):state.holdings.map(b=>`<tr><td>${esc(b.asset)}</td><td>${qty(b.free)}</td><td>${qty(b.locked)}</td><td>${num(b.value)}</td><td>${b.asset==='USDT'?'—':`<button data-adopt="${esc(b.asset)}">NHẬN THEO DÕI</button>`}</td></tr>`);
  showModal('Rice Reborn · DANH MỤC '+mode,`<p class="modal-intro">${mode==='PAPER'?'Sổ mô phỏng tách riêng khỏi tài khoản Binance.':'Số dư Spot theo Binance. Locked là số dư bị khóa; giá vốn phải được người vận hành xác minh.'}</p><div class="table-scroll">${rows.length?table(mode==='PAPER'?['Cặp','Còn giữ','Giá vốn','Giá thị trường','PNL trước phí','Quản lý']:['Tài sản','Free','Locked','Giá trị USDT','Theo dõi'],rows):'<div class="empty-state">Chưa có dữ liệu tài khoản.</div>'}</div>`);
 }
 if(name==='info')showModal('Rice Reborn · INFO',`<div class="toolbar"><button data-modal="priority">WATCHLIST / PRIORITY</button><button data-modal="volume">LỌC VOLUME</button></div><div class="table-scroll">${table(['Cặp','Giá Binance','EMA BUY','RSI14','Volume / TB','Trạng thái'],state.settings.watchlist.map(symbol=>{const d=state.decisions[mode][symbol]||{},m=d.marks||{};return `<tr><td><button data-select="${esc(symbol)}">${esc(symbol)}</button></td><td>${price(state.quotes[symbol]?.price)}</td><td>${price(m.buy_fast)} / ${price(m.buy_slow)}</td><td>${num(m.rsi)}</td><td>${num(m.volume_ratio)}×</td><td>${esc(d.reason||'CHỜ DỮ LIỆU')}</td></tr>`;}))}</div>`);
 if(name==='legend')showModal('Rice Reborn · CHÚ THÍCH BẢNG LỆNH',table(['Màu / nhóm','Ý nghĩa'],[
  '<tr><td class="positive">XANH · VỊ THẾ LÃI</td><td>Đã khớp mua; PNL chưa chốt trước phí.</td></tr>',
  '<tr><td class="negative">ĐỎ · VỊ THẾ LỖ</td><td>Đã khớp mua, giá hiện tại thấp hơn vốn.</td></tr>',
  '<tr><td class="warn">VÀNG · LỆNH CHỜ</td><td>Chưa khớp hết; có thể chọn và yêu cầu hủy phần còn lại.</td></tr>',
  '<tr><td>XÁM · THAM KHẢO</td><td>Cặp theo dõi chưa có vị thế hoặc lệnh hoạt động.</td></tr>',
  '<tr><td>XANH DƯƠNG · NGOÀI APP</td><td>Lệnh đọc từ Binance, chưa liên kết vị thế. Chỉ xem trong app.</td></tr>',
  '<tr><td>CTRL / SHIFT + CLICK</td><td>Chọn nhiều dòng. Ctrl+A chọn toàn bộ vị thế. Escape bỏ chọn. Nhấp đúp vị thế để mở thiết lập.</td></tr>'
 ]));
 if(name==='health')showModal('Rice Reborn · HEALTH',`<p class="modal-intro">${esc($('#health-title').textContent)} · ${esc($('#health-time').textContent)}</p><pre class="history-detail">${esc(JSON.stringify({errors:state.errors,streams:state.streams,capabilities:state.capabilities,account_at:date(state.account_at)},null,2))}</pre>`);
 if(name==='order-help')showModal('Rice Reborn · PHIẾU LỆNH',`<div class="rule-summary">MARKET: gửi theo giá thị trường. LIMIT: đặt giới hạn trên sàn. LIMIT kích hoạt: chờ tại backend rồi gửi LIMIT khi giá chạm.<br><br>KL AUTO tính từ ngân sách và giới hạn Binance. TP/SL để trống theo RULE; nhập 0 để tắt nhánh tương ứng cho lượt mua mới. PROTECT/E ở cột trái áp dụng cho lượt mua tay mới. Các vị thế đang giữ chỉnh ở Override.<br><br>REAL cần API Spot Trade và xác nhận gửi. API Read chỉ theo dõi và thông báo. Phí trong preview là ước tính 0,1%; phí thực nhận được ghi theo khớp.</div>`);
}
function selectInfoTab(name){infoTab=name;$('#preview-content').hidden=name!=='preview';$('#system-log').hidden=name==='preview';$$('[data-info]').forEach(b=>b.classList.toggle('active',b.dataset.info===name));$$('[data-preview]').forEach(b=>b.disabled=name!=='preview');if(name!=='preview')loadSystemLog();}
async function loadSystemLog(){
 lastLogRead=Date.now();const requested=infoTab;if(requested==='preview')return;
 try{const rows=await api('/api/history?mode='+mode);if(infoTab!==requested)return;
  const filtered=rows.filter(x=>requested==='manual'?x.payload.source==='MANUAL'||x.payload.reason==='MANUAL':x.payload.source==='BOT'||['TRACE','NOTICE','OBSERVATION'].includes(x.kind));
  $('#system-log').innerHTML=filtered.length?filtered.slice(0,60).map(x=>`<div>${esc(date(x.at))} · ${esc(x.kind)} · ${esc(x.symbol)}\n${esc(x.payload.text||x.payload.result||x.payload.reason||x.payload.action||'ĐÃ GHI NHẬN')}</div>`).join(''):'BOT khởi động OFF; dữ liệu và đối soát vẫn hoạt động.\nChưa có nhật ký '+(requested==='manual'?'thao tác tay.':'tự động.');
 }catch(e){$('#system-log').textContent=e.message;}
}
$('#system-collapse').onclick=()=>{const collapsed=$('.right-column').classList.toggle('info-collapsed');setText('#system-collapse',collapsed?'⌃':'⌄');};
$('#running-table').addEventListener('click',e=>{
 if(e.target.closest('button'))return;const row=e.target.closest('tr[data-row]');if(!row)return;
 const id=row.dataset.row, ids=$$('#running-table tr[data-row]').map(x=>x.dataset.row);
 if(e.shiftKey&&selectionAnchor&&ids.includes(selectionAnchor)){if(!e.ctrlKey&&!e.metaKey)selectedRows.clear();const a=ids.indexOf(selectionAnchor),b=ids.indexOf(id);ids.slice(Math.min(a,b),Math.max(a,b)+1).forEach(x=>selectedRows.add(x));}
 else if(e.ctrlKey||e.metaKey){if(selectedRows.has(id))selectedRows.delete(id);else selectedRows.add(id);selectionAnchor=id;}
 else{selectedRows.clear();selectedRows.add(id);selectionAnchor=id;}
 syncSelection();
});
$('#running-table').addEventListener('dblclick',e=>{const row=e.target.closest('tr[data-row]');if(row?.dataset.row.startsWith('p:'))positionModal(row.dataset.row.slice(2));});
$('#running-table').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();selectedRows.clear();state.positions.filter(p=>p.mode===mode).forEach(p=>selectedRows.add('p:'+p.id));syncSelection();}if(e.key==='Escape'){selectedRows.clear();syncSelection();}});
$('#cancel-selected').onclick=async()=>{
 const orders=state.orders.filter(o=>o.mode===mode&&selectedRows.has('o:'+o.id)&&activeOrder(o)&&o.source!=='EXTERNAL');
 if(!orders.length||!await askConfirm('Hủy phần chưa khớp của '+orders.length+' lệnh '+mode+' đã chọn?'))return;
 try{for(const order of orders)await api('/api/orders/'+order.id+'/cancel','POST',{});toast('Đã tiếp nhận yêu cầu hủy.');poll();}catch(e){toast(e.message,true);poll();}
};
$('#sell-selected').onclick=async()=>{
 const positions=state.positions.filter(p=>p.mode===mode&&selectedRows.has('p:'+p.id));if(!positions.length)return;
 try{const previews=[];for(const p of positions){const preview=await api('/api/preview','POST',{mode,symbol:p.symbol,side:'SELL',trade_id:p.id});previews.push({p,quantity:preview.quantity,request_id:uid()});}
  const book=mode, done=new Set();modalName='sell-review';
  showModal('Rice Reborn · BÁN ĐÃ CHỌN',`<p class="modal-intro">Kiểm tra số lượng trước khi gửi lệnh MARKET · ${book}. Phần đã dành cho SELL khác và dust đã được loại khỏi khối lượng.</p><form id="sell-review"><div class="table-scroll">${table(['Chọn','Cặp / vị thế','Khối lượng bán'],previews.map(({p,quantity})=>`<tr><td><input type="checkbox" name="use.${p.id}" checked aria-label="Chọn ${esc(p.symbol)}"></td><td>${esc(p.symbol)} · ${esc(p.id.slice(0,8))}</td><td><input type="number" min="0" max="${esc(quantity)}" step="any" name="qty.${p.id}" value="${esc(quantity)}" aria-label="Khối lượng bán ${esc(p.symbol)}"></td></tr>`))}</div><div class="modal-actions"><button type="submit" class="danger">XÁC NHẬN BÁN ${book}</button></div></form>`);
  $('#sell-review').onsubmit=async e=>{e.preventDefault();const f=e.target.elements,chosen=previews.filter(({p})=>f['use.'+p.id].checked&&!done.has(p.id)&&Number(f['qty.'+p.id].value)>0);if(!chosen.length){toast('Chọn ít nhất một lượng bán hợp lệ.');return;}if(book==='REAL'&&!await askConfirm('GỬI LỆNH BÁN THẬT MARKET:\n'+chosen.map(({p})=>p.symbol+' · '+f['qty.'+p.id].value).join('\n')))return;const button=e.target.querySelector('[type=submit]');button.disabled=true;try{for(const {p,request_id}of chosen){await api('/api/orders','POST',{mode:book,symbol:p.symbol,side:'SELL',type:'MARKET',quantity:f['qty.'+p.id].value,trade_id:p.id,request_id});done.add(p.id);}toast('Đã tiếp nhận '+done.size+' lệnh bán '+book+'.');closeModal();poll();}catch(error){toast('Đã nhận '+done.size+' lệnh. '+error.message,true);poll();}finally{button.disabled=false;}};
 }catch(e){toast(e.message,true);}
};
document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
 if(b.dataset.tabGroup){const group=b.dataset.tabGroup,name=b.dataset.tab;$$('[data-tab-group]').filter(x=>x.dataset.tabGroup===group).forEach(x=>{x.classList.toggle('active',x.dataset.tab===name);x.setAttribute('aria-selected',String(x.dataset.tab===name));});$$('[data-pane-group]').filter(x=>x.dataset.paneGroup===group).forEach(x=>x.hidden=x.dataset.pane!==name);}
 if(b.dataset.preview){previewTab=b.dataset.preview;$('#preview-content').dataset.view=previewTab;$$('[data-preview]').forEach(x=>x.classList.toggle('active',x.dataset.preview===previewTab));}
 if(b.dataset.info)selectInfoTab(b.dataset.info);
 if(b.dataset.modal)await openModal(b.dataset.modal);
 if(b.dataset.select)selectSymbol(b.dataset.select);
 if(b.dataset.position)positionModal(b.dataset.position);
 if(b.dataset.sell){selectedRows.clear();selectedRows.add('p:'+b.dataset.sell);syncSelection();await $('#sell-selected').onclick();}
 if(b.dataset.cancel&&await askConfirm('Hủy đúng lệnh này?')){await api('/api/orders/'+b.dataset.cancel+'/cancel','POST',{});toast('Đã tiếp nhận hủy; kiểm tra trạng thái đối soát.');poll();}
 if(b.dataset.replace){const o=state.orders.find(x=>x.id===b.dataset.replace);const quantity=await askInput('Khối lượng lệnh thay (phần còn lại)',o.remaining);if(quantity===null)return;const value=await askInput('Giá LIMIT mới',o.price);if(value===null)return;if(!await askConfirm('Xác nhận hủy lệnh cũ rồi tạo lệnh thay?'))return;await api('/api/orders/'+o.id+'/replace','POST',{quantity,price:value,request_id:uid()});toast('Đã tiếp nhận sửa lệnh.');poll();}
 if(b.dataset.adopt)adoptModal(b.dataset.adopt);
 if(b.dataset.cancelJob){await api('/api/backtests/'+b.dataset.cancelJob+'/cancel','POST',{});toast('Đã yêu cầu hủy backtest.');}
}catch(err){toast(err.message,true);}});
setInterval(()=>setText('#clock',new Date().toLocaleString('vi-VN',{timeZone:'Asia/Bangkok',hour12:false})),1000);
poll();setInterval(poll,2000);
