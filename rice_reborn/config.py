"""Validated settings. Never return credentials in API snapshots."""
from copy import deepcopy
import ipaddress, json, math, os, re, tempfile
from pathlib import Path
from dotenv import dotenv_values
from .models import dec

PRESET = json.loads((Path(__file__).parent/'presets/va_crypto.json').read_text(encoding='utf-8'))
RULE_VERSION = PRESET['version']
DEFAULT_RULES = PRESET['rules']
DEFAULT_SETTINGS = {'watchlist':['BTCUSDT','ETHUSDT','LINKUSDT'], 'priority':{},'capital_usdt':'0','paper_capital':'1000',
 'market_override':True,'market_exposure_pct':100,'signal_mode':'REALTIME','indicator_interval':'TICK',
 'telegram_enabled':True,'telegram_replay_missed':False,'telegram_batch_minutes':0,'telegram_cooldown_minutes':30,
 'telegram_groups':{'signals':True,'positions':True,'orders':True,'system':True},
 'telegram_cooldowns':{'BUY':60,'BUY_LOST':60,'E':30,'ARM':0,'DYNAMIC':0,'SL':0,'TP':0,'SYSTEM':30},
 'trace_enabled':True,'trace_minutes':2,'manual_sell_pause_minutes':15,'rules':DEFAULT_RULES}

def symbol(value):
    value=str(value).upper().strip().replace('/','').replace('-','')
    if not value.endswith('USDT'): value+='USDT'
    if not re.fullmatch(r'[A-Z0-9]{2,24}USDT',value): raise ValueError('Cặp không hợp lệ; dùng BTCUSDT hoặc BTC.')
    return value

def finite(value,minimum=0,maximum=1e12):
    n=float(value)
    if not math.isfinite(n) or not minimum<=n<=maximum: raise ValueError(f'Giá trị phải từ {minimum:g} đến {maximum:g}.')
    return n

def validate_settings(values):
    if set(values)-set(DEFAULT_SETTINGS): raise ValueError('Thiết lập không hỗ trợ.')
    out={**deepcopy(DEFAULT_SETTINGS),**deepcopy(values)}
    if isinstance(out['watchlist'],str): out['watchlist']=re.split(r'[,;\s]+',out['watchlist'].strip())
    out['watchlist']=list(dict.fromkeys(symbol(x) for x in out['watchlist'] if x))
    if not 1<=len(out['watchlist'])<=100: raise ValueError('Theo dõi từ 1 đến 100 cặp.')
    for k in ('capital_usdt','paper_capital'):
        value=dec(out[k])
        if not 0<=value<=dec('1e12'): raise ValueError('Ngân sách ngoài giới hạn.')
        out[k]=str(value)
    out['market_exposure_pct']=finite(out['market_exposure_pct'],0,100)
    if out['signal_mode'] not in ('REALTIME','CLOSED'): raise ValueError('Mode nến không hợp lệ.')
    if out['indicator_interval'] not in ('TICK','1M','2M','5M'): raise ValueError('Nhịp chỉ báo không hợp lệ.')
    for k in ('trace_minutes','manual_sell_pause_minutes','telegram_batch_minutes','telegram_cooldown_minutes'):
        out[k]=int(finite(out[k],1 if k=='trace_minutes' else 0,1440))
    for k in ('market_override','telegram_enabled','telegram_replay_missed','trace_enabled'):
        if type(out[k]) is not bool: raise ValueError(f'{k} phải boolean.')
    if set(out['telegram_groups'])!={'signals','positions','orders','system'} or any(type(v) is not bool for v in out['telegram_groups'].values()): raise ValueError('Nhóm Telegram không hợp lệ.')
    cooldowns=out['telegram_cooldowns']
    if set(cooldowns)!=set(DEFAULT_SETTINGS['telegram_cooldowns']): raise ValueError('Loại giãn tin không hợp lệ.')
    out['telegram_cooldowns']={k:int(finite(v,0,1440)) for k,v in cooldowns.items()}
    if set(out['rules'])-set(DEFAULT_RULES): raise ValueError('Tham số RULE không hỗ trợ.')
    r={**deepcopy(DEFAULT_RULES),**out['rules']}
    for k,d in DEFAULT_RULES.items():
        if isinstance(d,bool):
            if type(r[k]) is not bool: raise ValueError(f'{k} phải boolean.')
        elif isinstance(d,(int,float)):
            n=finite(r[k],-100 if k.endswith('_sl_pct') else 0,10000)
            if isinstance(d,int) and n!=int(n): raise ValueError(f'{k} phải số nguyên.')
            r[k]=int(n) if isinstance(d,int) else n
    for k in ('buy_ema_fast','buy_ema_slow','sell_ema_fast','sell_ema_slow','rsi_period','ma_period','pivot_left','pivot_right','confirm_sessions','max_positions','whipsaw_n','whipsaw_x','buy_ema_cross_sessions','loss_lock_count'):
        if not 1<=r[k]<=500: raise ValueError(f'{k}: giới hạn 1–500.')
    for k in ('normal_sell_pct','dynamic_sell_pct','indicator_exit_sell_pct','take_profit_sell_pct','normal_retention_pct'): finite(r[k],0.01,100)
    for k in ('initial_sl_pct','reentry_sl_pct'): finite(r[k],-99,-0.01)
    for k in ('normal_policy','dynamic_policy','indicator_exit_policy'):
        if r[k] not in ('AUTO','ALERT'): raise ValueError('Policy phải AUTO hoặc ALERT.')
    for k in ('normal_sell_basis','dynamic_sell_basis','indicator_exit_sell_basis'):
        if r[k] not in ('ORIGINAL','REMAINING'): raise ValueError('Cơ sở bán không hợp lệ.')
    if r['buy_ema_cross_mode'] not in ('VA','SESSIONS'): raise ValueError('Mode cắt phải VA hoặc SESSIONS.')
    if r['loss_lock_mode'] not in ('TIMED','MANUAL'): raise ValueError('Kiểu khóa lỗ không hợp lệ.')
    if r['buy_confirmation_enabled'] and (r['buy_confirmation_minutes']<1 or not (r['buy_confirmation_require_ema'] or r['buy_confirmation_require_rsi'])): raise ValueError('Xác nhận BUY cần thời gian dương và ít nhất một điều kiện.')
    for k in ('buy_window_start','buy_window_end'):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(r[k])): raise ValueError('Giờ phải HH:MM.')
    r['exposure']={k:finite(r['exposure'].get(k,v),0,1) for k,v in DEFAULT_RULES['exposure'].items()}
    out['rules']=r; priority={}
    for name,row in out['priority'].items():
        name=symbol(name)
        if name not in out['watchlist']: raise ValueError('Priority phải trong watchlist.')
        if row.get('mode','ALERT') not in ('AUTO','ALERT'): raise ValueError('Priority phải AUTO hoặc ALERT.')
        cap=dec(row.get('cap',0))
        if not 0<=cap<=dec('1e12'): raise ValueError('Hạn mức ngoài giới hạn.')
        priority[name]={'mode':row.get('mode','ALERT'),'cap':str(cap),'use_pct':finite(row.get('use_pct',100),0,100),'max_orders':int(finite(row.get('max_orders',1),1,20))}
    if sum(v['mode']=='AUTO' and float(v['cap'])>0 for v in priority.values())>r['max_positions']: raise ValueError('Priority AUTO vượt MAX vị thế.')
    out['priority']=priority
    return out

class Environment:
    def __init__(self,home=None,demo=False):
        self.demo=demo; self.home=Path(home or os.getenv('RICE_HOME',Path.cwd())).resolve()
        self.env_file=Path(os.getenv('RICE_ENV_FILE',self.home/'.env')).resolve()
        self.values={} if demo else {**dotenv_values(self.env_file),**{k:v for k,v in os.environ.items() if k.startswith(('BINANCE_','TELEGRAM_','RICE_'))}}
        self.data=self.home/'data'/('demo' if demo else 'v1'); self.data.mkdir(parents=True,exist_ok=True)
        self.host=self.get('RICE_HOST','127.0.0.1'); self.port=int(self.get('RICE_PORT','8080'))
    def get(self,key,default=''): return self.values.get(key) or default
    def save_secrets(self,updates):
        if set(updates)-{'BINANCE_API_KEY','BINANCE_API_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','RICE_PASSWORD_HASH'}: raise ValueError('Secret không hỗ trợ.')
        self._save_values(updates)
    def save_access(self,updates):
        if set(updates)-{'RICE_HOST','RICE_PORT','RICE_ALLOWED_IPS','RICE_COOKIE_SECURE','RICE_ACCESS_MODE'}: raise ValueError('Thiết lập truy cập không hỗ trợ.')
        if 'RICE_HOST' in updates and updates['RICE_HOST']!='localhost': ipaddress.ip_address(updates['RICE_HOST'])
        if 'RICE_PORT' in updates and not 1<=int(updates['RICE_PORT'])<=65535: raise ValueError('Cổng không hợp lệ.')
        for value in updates.get('RICE_ALLOWED_IPS','').split(','):
            if value.strip(): ipaddress.ip_network(value.strip(),strict=False)
        if 'RICE_COOKIE_SECURE' in updates and updates['RICE_COOKIE_SECURE'] not in ('0','1'): raise ValueError('Secure cookie phải là 0 hoặc 1.')
        if 'RICE_ACCESS_MODE' in updates and updates['RICE_ACCESS_MODE'] not in ('direct','ssh','https'): raise ValueError('Mode truy cập không hợp lệ.')
        self._save_values(updates)
    def _save_values(self,updates):
        current=dict(dotenv_values(self.env_file)) if self.env_file.exists() else {}; current.update(updates)
        self.env_file.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(dir=self.env_file.parent,prefix='.rice-env-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                for k,v in current.items():
                    if v is not None: f.write(k+'='+json.dumps(str(v),ensure_ascii=False)+'\n')
                f.flush(); os.fsync(f.fileno())
            os.chmod(tmp,0o600); os.replace(tmp,self.env_file); self.values.update(updates)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
