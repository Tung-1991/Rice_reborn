"""Authenticated web control plane. Capability checks also live below HTTP."""
from collections import defaultdict,deque
from functools import wraps
from pathlib import Path
import csv,hashlib,hmac,io,ipaddress,json,re,secrets,time
from flask import Flask,abort,jsonify,redirect,render_template,request,session,url_for,send_file,Response
from werkzeug.security import check_password_hash
from .config import RULE_VERSION,DEFAULT_RULES,symbol
from .models import dec,wire
from .execution import ACTIVE

def create_web_app(service):
    app=Flask(__name__); holder={'service':service}; env=service.env
    key_path=env.data/'session.key'
    if not key_path.exists():
        key_path.write_text(secrets.token_hex(32)); key_path.chmod(0o600)
    app.secret_key=key_path.read_text().strip()
    app.config.update(MAX_CONTENT_LENGTH=262144,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=env.get('RICE_COOKIE_SECURE','0')=='1',PERMANENT_SESSION_LIFETIME=3600*8)
    networks=[ipaddress.ip_network(x.strip(),strict=False) for x in env.get('RICE_ALLOWED_IPS').split(',') if x.strip()]
    attempts=defaultdict(lambda:deque(maxlen=12))
    def svc(): return holder['service']
    def local():
        try: return ipaddress.ip_address(request.remote_addr or '').is_loopback
        except ValueError: return False
    def auth_generation():
        return hmac.new(app.secret_key.encode(),env.get('RICE_PASSWORD_HASH').encode(),hashlib.sha256).hexdigest()
    def authenticated():
        if env.demo and local(): return True
        return bool(session.get('authenticated') and hmac.compare_digest(session.get('auth_generation',''),auth_generation()))
    @app.before_request
    def protect():
        session.setdefault('csrf',secrets.token_hex(32))
        if env.demo and not local(): abort(403,'Demo chỉ mở tại localhost.')
        if networks and not local():
            try: address=ipaddress.ip_address(request.remote_addr or '')
            except ValueError: abort(403)
            if not any(address in network for network in networks): abort(403)
        if request.path not in ('/login','/healthz') and not request.path.startswith('/static/') and not authenticated():
            if request.path.startswith('/api/'): abort(401,'Cần đăng nhập.')
            return redirect('/login')
        if request.method in ('POST','PUT','PATCH','DELETE'):
            token=request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not token or not hmac.compare_digest(token,session['csrf']): abort(403,'CSRF token không hợp lệ; tải lại trang.')
    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
          'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if not request.path.startswith('/static/'): response.headers['Cache-Control']='no-store'
        return response
    @app.errorhandler(ValueError)
    def bad_request(exc): return jsonify(error=str(exc)),400
    @app.errorhandler(PermissionError)
    def forbidden(exc): return jsonify(error=str(exc)),403
    @app.errorhandler(RuntimeError)
    def runtime_error(exc): return jsonify(error=str(exc)),502
    from werkzeug.exceptions import HTTPException
    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith('/api/'): return jsonify(error=exc.description),exc.code
        return exc
    @app.route('/login',methods=['GET','POST'])
    def login():
        error=''
        if request.method=='POST':
            address=request.remote_addr; now=time.time(); recent=attempts[address]
            if len(recent)>=10 and now-recent[-10]<300: error='Quá nhiều lần thử; chờ 5 phút.'
            elif env.get('RICE_PASSWORD_HASH') and check_password_hash(env.get('RICE_PASSWORD_HASH'),request.form.get('password','')):
                session.clear(); session.update(authenticated=True,auth_generation=auth_generation(),csrf=secrets.token_hex(32)); session.permanent=True; recent.clear(); return redirect('/')
            else:
                recent.append(now); error='Mật khẩu không đúng.' if env.get('RICE_PASSWORD_HASH') else 'Chưa tạo mật khẩu. Chạy python -m rice_reborn.main --set-password tại máy chủ.'
            if len(attempts)>1000: attempts.clear()
        return render_template('login.html',csrf=session['csrf'],error=error)
    @app.post('/logout')
    def logout(): session.clear(); return jsonify(ok=True)
    @app.get('/')
    def index(): return render_template('index.html',csrf=session['csrf'],version=RULE_VERSION,demo=env.demo)
    @app.get('/healthz')
    def health(): return jsonify(ok=True,version=RULE_VERSION)
    @app.get('/api/status')
    def status(): return jsonify(svc().snapshot())
    @app.route('/api/settings',methods=['GET','PUT'])
    def settings():
        if request.method=='GET': return jsonify(svc().settings)
        return jsonify(svc().settings_save(request.get_json()))
    @app.get('/api/preset')
    def preset(): return jsonify(rules=DEFAULT_RULES,version=RULE_VERSION)
    @app.post('/api/refresh')
    def refresh(): svc().refresh_async(); return jsonify(ok=True)
    @app.post('/api/bot')
    def bot():
        d=request.get_json()
        if type(d.get('enabled')) is not bool: raise ValueError('enabled phải boolean.')
        svc().set_bot(d['mode'],d['enabled']); return jsonify(bot=svc().execution.bot)
    @app.post('/api/orders')
    def order():
        d=request.get_json(); d['symbol']=symbol(d['symbol'])
        if not d.get('request_id'): raise ValueError('Thiếu request_id chống đặt trùng.')
        return jsonify(wire(svc().execution.create(d)))
    @app.post('/api/preview')
    def preview():
        d=request.get_json(); return jsonify(svc().preview(d['mode'],symbol(d['symbol']),d.get('side','BUY'),d.get('trade_id',''),d.get('type','MARKET'),d.get('price')))
    @app.post('/api/orders/<oid>/cancel')
    def cancel(oid): return jsonify(wire(svc().execution.cancel(oid)))
    @app.post('/api/orders/<oid>/replace')
    def replace(oid): return jsonify(wire(svc().execution.replace(oid,request.get_json())))
    @app.patch('/api/positions/<pid>')
    def position(pid): return jsonify(svc().update_position(pid,request.get_json()))
    @app.post('/api/positions/<pid>/remeasure')
    def remeasure(pid): svc().remeasure(pid,int(request.get_json()['days'])); return jsonify(ok=True)
    @app.post('/api/positions/adopt')
    def adopt(): return jsonify(svc().adopt(request.get_json()))
    @app.post('/api/unlock')
    def unlock():
        d=request.get_json()
        if d['mode'] not in ('REAL','PAPER'): raise ValueError('Sổ không hợp lệ.')
        svc().unlock(d['mode'],symbol(d['symbol']),d['kind']); return jsonify(ok=True)
    @app.post('/api/ema-override')
    def ema_override():
        d=request.get_json()
        if d['mode'] not in ('REAL','PAPER') or type(d.get('enabled')) is not bool: raise ValueError('Sổ/công tắc không hợp lệ.')
        svc().override[d['mode']+':'+symbol(d['symbol'])]=d['enabled']; return jsonify(ok=True)
    @app.get('/api/history')
    def history(): return jsonify(svc().store.history(request.args.get('kind',''),request.args.get('mode',''),int(request.args.get('limit',100)),int(request.args.get('offset',0))))
    @app.post('/api/integrations')
    def integrations():
        old=svc(); d=request.get_json(); updates={}
        allowed={'BINANCE_API_KEY','BINANCE_API_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'}
        if set(d)-allowed: raise ValueError('Trường kết nối không hỗ trợ.')
        for k,v in d.items():
            if v is None: updates[k]=''
            elif isinstance(v,str) and v.strip(): updates[k]=v.strip()
        key=updates.get('BINANCE_API_KEY',env.get('BINANCE_API_KEY')); secret=updates.get('BINANCE_API_SECRET',env.get('BINANCE_API_SECRET'))
        if bool(key)!=bool(secret): raise ValueError('Binance cần cả key và secret.')
        if env.demo: raise ValueError('Demo không lưu API thật. Mở bản LIVE để cấu hình.')
        with old.lock:
            if any(o['status'] in ACTIVE and (o.get('broker_id') or o['status']=='UNKNOWN') for o in old.execution.orders('REAL')):
                raise ValueError('Đang có lệnh REAL cần đối soát; chưa đổi kết nối.')
            old.set_bot('REAL',False); old.set_bot('PAPER',False); env.save_secrets(updates); old.close()
            from .service import Service
            new=Service(env,recover_jobs=False); new.jobs=old.jobs
            # Watchlist/rule can follow a credential replacement; financial state cannot.
            if new.store.get('settings') is None: new.settings_save(old.settings)
            holder['service']=new; new.start()
        return jsonify(ok=True,message='Đã lưu kết nối. BOT OFF; secret không trả lại trình duyệt.')
    @app.post('/api/telegram/test')
    def telegram_test(): svc().telegram.send('Rice Reborn · kiểm tra Telegram thành công.'); return jsonify(ok=True)
    @app.post('/api/sync-history')
    def sync_history():
        name=symbol(request.get_json()['symbol']); gateway=svc().gateway
        if not gateway.configured: raise ValueError('Chưa có API đọc tài khoản.')
        count=0; last=None; rows=[]
        for _ in range(100):
            page=gateway.trades(name,last if last is not None else 0)
            for f in page:
                payload={**f,'side':'BUY' if f['isBuyer'] else 'SELL','source':'BINANCE_HISTORY'}
                if svc().store.event('exchange:'+name+':'+str(f['id']),'EXTERNAL_FILL',payload,'REAL',name,float(f['time'])/1000): count+=1
            rows.extend(page)
            if len(page)<1000: break
            last=page[-1]['id']+1
        return jsonify(imported=count,records=len(rows),message='Đã đọc lịch sử Binance; không tự dựng/nhận quản lý vị thế từ dữ liệu mơ hồ.')
    @app.post('/api/volume-scan')
    def volume_scan():
        d=request.get_json(); source=d.get('source','WATCHLIST'); n=int(d.get('count',20)); period=int(d.get('period',5)); threshold=float(d.get('threshold',20)); direction=d.get('direction','BOTH')
        if not 1<=n<=100 or period not in (5,10) or threshold<=0 or direction not in ('UP','DOWN','BOTH'): raise ValueError('Tham số lọc volume không hợp lệ.')
        service=svc(); names=service.settings['watchlist'][:n]
        if source=='LIQUIDITY':
            tickers=service.gateway.ticker(); names=[x['symbol'] for x in sorted(tickers,key=lambda x:float(x['quoteVolume']),reverse=True) if x['symbol'] in service.execution.instruments][:n]
        result=[]
        for name in names:
            bars=service.bars.get(name) or ([] if env.demo else service.gateway.klines(name,'1d',2*period+2))
            bars=[x for x in bars if x.get('closed')][-2*period:]
            if len(bars)<2*period: continue
            before=sum(x['volume'] for x in bars[:period])/period; after=sum(x['volume'] for x in bars[period:])/period
            pct=(after/before-1)*100 if before>0 else None
            if pct is not None and abs(pct)>=threshold and (direction=='BOTH' or (direction=='UP' and pct>0) or (direction=='DOWN' and pct<0)): result.append({'symbol':name,'before':before,'after':after,'change_pct':pct})
        return jsonify(rows=sorted(result,key=lambda x:abs(x['change_pct']),reverse=True),scanned=len(names),source=source)
    @app.route('/api/backtests',methods=['GET','POST'])
    def backtests():
        if request.method=='GET': return jsonify(svc().job_status())
        if env.demo: raise ValueError('Chạy backtest dữ liệu Binance trên bản LIVE; demo không gọi mạng.')
        from .backtest import start_job
        return jsonify(start_job(svc(),request.get_json()))
    @app.post('/api/backtests/<jobid>/cancel')
    def cancel_backtest(jobid):
        if not re.fullmatch('[a-f0-9]{32}',jobid): abort(404)
        from .backtest import cancel_job
        cancel_job(svc(),jobid); return jsonify(ok=True)
    @app.get('/api/backtests/<jobid>/files/<name>')
    def backtest_file(jobid,name):
        if not re.fullmatch('[a-f0-9]{32}',jobid) or Path(name).name!=name or not name.endswith('.xlsx'): abort(404)
        path=env.data/'backtests'/jobid/name
        if not path.is_file(): abort(404)
        return send_file(path,as_attachment=True)
    app.rice_service=holder
    return app
