"""Durable Telegram delivery. Delivery receipts never mutate trading state."""
import hashlib,threading,time
import requests

class Telegram:
    def __init__(self,store,environment):
        self.store=store; self.env=environment; self.settings={}; self.error=''; self.lock=threading.Lock()
    @property
    def configured(self): return not self.env.demo and bool(self.env.get('TELEGRAM_BOT_TOKEN') and self.env.get('TELEGRAM_CHAT_ID'))
    def configure(self,settings):
        old=self.settings; self.settings=settings
        enabled=settings['telegram_enabled']; previous=self.store.get('telegram','enabled')
        if previous!=enabled or (old and old.get('telegram_replay_missed') and not settings['telegram_replay_missed']):
            if not settings['telegram_replay_missed']:
                for notice in self.store.all('outbox'):
                    if notice['status']=='PENDING': notice['status']='DROPPED'; self.store.put('outbox',notice['id'],notice)
            self.store.put('telegram','enabled',enabled)
        if not settings['telegram_replay_missed']:
            for n in self.store.all('outbox'):
                if n['status']=='PENDING' and not settings['telegram_groups'].get(n['group']): n['status']='DROPPED'; self.store.put('outbox',n['id'],n)
    def enqueue(self,key,group,name,text):
        if not self.configured: return
        if not self.settings.get('telegram_groups',{}).get(group,False): return
        if not self.settings['telegram_enabled'] and not self.settings['telegram_replay_missed']: return
        receiver=hashlib.sha256((self.env.get('TELEGRAM_CHAT_ID')+self.env.get('TELEGRAM_BOT_TOKEN')).encode()).hexdigest()[:16]
        oid=receiver+':'+key
        with self.store.transaction():
            if self.store.get('outbox',oid): return
            book=text.split(' ',1)[0] if text.startswith(('REAL ','PAPER ')) else ''
            category='SYSTEM' if group=='system' else 'BUY_LOST' if key.startswith('lost:') else text.split(' ')[1] if book else group
            if key.startswith('armed:'): category='ARM_START'
            if key.startswith('dynamic-start:'): category='DYNAMIC_START'
            self.store.put('outbox',oid,{'id':oid,'key':key,'receiver':receiver,'group':group,'symbol':name,'mode':book,'category':category,'text':name+' · '+text,'status':'PENDING','at':time.time(),'attempts':0})
    def send(self,text):
        if not self.configured: raise ValueError('Chưa cấu hình Telegram bot token/chat ID.')
        try:
            response=requests.post('https://api.telegram.org/bot'+self.env.get('TELEGRAM_BOT_TOKEN')+'/sendMessage',
                json={'chat_id':self.env.get('TELEGRAM_CHAT_ID'),'text':text[:3900],'disable_web_page_preview':True},timeout=(4,12))
            data=response.json()
        except (requests.RequestException,ValueError): raise RuntimeError('Telegram: lỗi mạng/JSON.') from None
        if not response.ok or not data.get('ok'): raise RuntimeError('Telegram từ chối tin; kiểm tra token/chat/quyền bot.')
        return data.get('result',{}).get('message_id')
    def flush(self):
        if not self.settings.get('telegram_enabled') or not self.configured or not self.lock.acquire(False): return
        try:
            receiver=hashlib.sha256((self.env.get('TELEGRAM_CHAT_ID')+self.env.get('TELEGRAM_BOT_TOKEN')).encode()).hexdigest()[:16]
            pending=[n for n in self.store.all('outbox') if n['status']=='PENDING' and n['receiver']==receiver and self.settings['telegram_groups'].get(n['group'])]
            deliver=[]; now=time.time()
            for n in pending:
                if n['group']=='signals' and now-n['at']>86400: n['status']='EXPIRED'; self.store.put('outbox',n['id'],n); continue
                if now-n.get('last_try',0)<min(300,2**min(8,n['attempts'])): continue
                cooldown=0 if n['group']=='orders' else self.settings.get('telegram_cooldowns',{}).get(n.get('category'),self.settings['telegram_cooldown_minutes'])*60
                if n.get('category') in ('ARM_START','DYNAMIC_START'): cooldown=0
                bucket=n['group']+':'+n.get('mode','')+':'+n['symbol']+':'+n.get('category',''); last=self.store.get('telegram',bucket,0)
                if now-last<cooldown: continue
                if n['group']=='signals' and self.settings['telegram_batch_minutes'] and now-n['at']<self.settings['telegram_batch_minutes']*60: continue
                deliver.append(n)
                if len(deliver)>=8: break
            if not deliver: return
            from datetime import datetime,timezone,timedelta
            text='RICE REBORN · BINANCE SPOT\n'+'\n\n'.join(n['text']+'\nQuan sát '+datetime.fromtimestamp(n['at'],timezone(timedelta(hours=7))).strftime('%d/%m %H:%M:%S')+(' · GỬI TRỄ' if now-n['at']>60 else '') for n in deliver)
            try:
                message_id=self.send(text)
                with self.store.transaction():
                    for n in deliver:
                        n.update(status='SENT',message_id=message_id,sent_at=now); self.store.put('outbox',n['id'],n)
                        self.store.put('telegram',n['group']+':'+n.get('mode','')+':'+n['symbol']+':'+n.get('category',''),now)
                self.error=''
            except RuntimeError as exc:
                self.error=str(exc)
                for n in deliver:
                    n.update(last_try=now,attempts=n['attempts']+1); self.store.put('outbox',n['id'],n)
        finally: self.lock.release()
