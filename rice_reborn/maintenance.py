"""Offline launcher operations. Never construct Service, send orders or Telegram."""
import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
import zipfile

from dotenv import dotenv_values
from .config import DEFAULT_RULES, DEFAULT_SETTINGS, RULE_VERSION, Environment, validate_settings
from .lease import RuntimeLease
from .models import dec
from .store import Store


def paths():
    home=Path(os.getenv('RICE_HOME',Path.cwd())).resolve()
    env_file=Path(os.getenv('RICE_ENV_FILE',home/'.env')).resolve()
    values={**(dotenv_values(env_file) if env_file.exists() else {}),
        **{k:v for k,v in os.environ.items() if k.startswith(('RICE_','BINANCE_','TELEGRAM_'))}}
    return home,env_file,values


@contextmanager
def readonly(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try: yield db
    finally: db.close()


def check_environment(lock=None):
    issues=[]
    if sys.version_info<(3,11): issues.append('Cần Python >= 3.11.')
    for name in ('flask','requests','dotenv','waitress','websocket','openpyxl','sqlite3','ssl'):
        try: importlib.import_module(name)
        except Exception as exc: issues.append(f'IMPORT {name}: {type(exc).__name__}')
    if lock:
        for line in Path(lock).read_text('utf-8').splitlines():
            line=line.strip()
            if not line or line.startswith('#'): continue
            name,expected=line.split('==',1)
            try: actual=metadata.version(name)
            except metadata.PackageNotFoundError: actual='MISSING'
            if actual!=expected: issues.append(f'{name}: {actual}; cần {expected}')
    home,env_file,values=paths()
    print('Python:',sys.version.split()[0],sys.executable)
    print('Runtime:',home/'data'/'v1')
    print('API:', 'CONFIGURED' if values.get('BINANCE_API_KEY') and values.get('BINANCE_API_SECRET') else 'NOT_CONFIGURED')
    print('Password:', 'CONFIGURED' if values.get('RICE_PASSWORD_HASH') else 'NOT_CONFIGURED')
    print('Access:',values.get('RICE_ACCESS_MODE') or 'direct','·',(values.get('RICE_HOST') or '127.0.0.1')+':'+str(values.get('RICE_PORT') or 8080))
    print('App allowlist:', 'SET' if values.get('RICE_ALLOWED_IPS') else 'EMPTY (kiểm tra firewall bên ngoài)')
    if os.name!='nt' and env_file.exists() and stat.S_IMODE(env_file.stat().st_mode)&0o077:
        issues.append('File .env có quyền group/other; cần chmod 600.')
    for issue in issues: print('[FAIL]',issue)
    print('[CHECK]',len(issues),'lỗi. Không sửa API, dữ liệu hoặc khởi động backend.')
    return 1 if issues else 0


def state_check():
    home,_,_=paths()
    directory=home/'data'/'v1'
    issues=[]
    files=sorted(directory.glob('account-*.sqlite3'))
    if not files: print('[STATE] Chưa có database LIVE; không tạo database giả.'); return 0
    for path in files:
        if path.is_symlink(): issues.append(path.name+': không đọc symlink.'); continue
        with readonly(path) as db:
            result=db.execute('PRAGMA quick_check').fetchone()[0]
            if result!='ok': issues.append(path.name+': SQLite quick_check FAILED')
            documents={}
            for row in db.execute('SELECT kind,id,payload FROM documents'):
                if row['kind'] in ('order','position','settings'): documents[(row['kind'],row['id'])]=json.loads(row['payload'])
            positions=[p for (kind,_),p in documents.items() if kind=='position']
            orders=[o for (kind,_),o in documents.items() if kind=='order']
            for p in positions:
                if dec(p['quantity'])<0 or dec(p['original'])<dec(p['quantity']): issues.append(path.name+': quantity vị thế không hợp lệ')
            for order in orders:
                if dec(order['filled'])+dec(order['remaining'])!=dec(order['quantity']):
                    issues.append(path.name+': lượng lệnh không cân')
                if order['side']=='SELL' and ('position',order['trade_id']) not in documents:
                    issues.append(path.name+': SELL thiếu vị thế liên kết')
            unknown=sum(o['status'] in ('UNKNOWN','ACCOUNTING_PENDING') for o in orders)
            fills=db.execute('SELECT COUNT(*) FROM events WHERE kind="FILL"').fetchone()[0]
            print('[STATE]',path.name,'· positions',len(positions),'· orders',len(orders),'· fills',fills,'· cần đối soát',unknown)
    for issue in issues: print('[FAIL]',issue)
    print('[STATE] Chỉ đọc; không reset lock, nhận fill hoặc phục hồi runtime.')
    return 1 if issues else 0


def apply_va():
    env=Environment()
    lease=RuntimeLease(env.data/'runtime.lock')
    try:
        key=env.get('BINANCE_API_KEY')
        profile=hashlib.sha256(key.encode()).hexdigest()[:20] if key else 'public'
        store=Store(env.data/('account-'+profile+'.sqlite3'))
        settings=store.get('settings',default=deepcopy(DEFAULT_SETTINGS))
        settings['rules']=deepcopy(DEFAULT_RULES)
        store.put('settings','main',validate_settings(settings))
        store.event('preset:'+uuid.uuid4().hex,'SETTINGS',{'action':'PRESET_VA','version':RULE_VERSION})
        print('[VA]',RULE_VERSION,'· đã nạp ngưỡng cho profile hiện tại. Giữ vốn, watchlist, Priority, API và sổ tài chính.')
    finally: lease.close()


def backup(directory):
    home,env_file,_=paths()
    data=home/'data'/'v1'
    if not data.exists(): raise ValueError('Chưa có runtime LIVE để backup.')
    if data.is_symlink() or data.parent.is_symlink(): raise ValueError('Không backup runtime symlink.')
    lease=RuntimeLease(data/'runtime.lock')
    try:
        if os.name!='nt' and os.geteuid()==0:
            owner=data.stat()
            os.chown(data/'runtime.lock',owner.st_uid,owner.st_gid)
        directory=Path(directory).resolve()
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name!='nt' and stat.S_IMODE(directory.stat().st_mode)&0o077:
            raise ValueError('Thư mục backup phải có quyền 700 vì chứa credentials.')
        target=directory/('rice-backup-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime())+'-'+uuid.uuid4().hex[:8]+'.zip')
        fd,partial=tempfile.mkstemp(prefix='.rice-backup-',dir=directory); os.close(fd)
        try:
            with tempfile.TemporaryDirectory(prefix='rice-snapshot-') as temporary, zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as archive:
                manifest={'version':RULE_VERSION,'created_at':time.time(),'files':{}}
                sources=[(env_file,'config/.env'),(data/'session.key','data/v1/session.key')]
                for path in sorted(data.glob('account-*.sqlite3')):
                    if path.is_symlink(): raise ValueError('Database symlink không được backup.')
                    # Runtime lease makes committed DB/WAL stable. Open a private copy
                    # so a root backup never creates root-owned WAL/SHM in app data.
                    copied=Path(temporary)/path.name
                    shutil.copyfile(path,copied)
                    wal=Path(str(path)+'-wal')
                    if wal.is_symlink(): raise ValueError('WAL symlink không được backup.')
                    if wal.exists(): shutil.copyfile(wal,str(copied)+'-wal')
                    destination=Path(temporary)/('snapshot-'+path.name)
                    with readonly(copied) as source,closing(sqlite3.connect(destination)) as out: source.backup(out)
                    sources.append((destination,'data/v1/'+path.name))
                for path,name in sources:
                    if not path.exists(): continue
                    if path.is_symlink(): raise ValueError('Không backup symlink.')
                    content=path.read_bytes()
                    archive.writestr(name,content)
                    manifest['files'][name]=hashlib.sha256(content).hexdigest()
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            with zipfile.ZipFile(partial) as archive:
                if archive.testzip() is not None: raise ValueError('Backup checksum FAILED.')
                for name,digest in manifest['files'].items():
                    if hashlib.sha256(archive.read(name)).hexdigest()!=digest: raise ValueError('Backup hash FAILED.')
            os.chmod(partial,0o600); os.replace(partial,target)
            print('[BACKUP]',target)
            print('[BACKUP] Có credentials/session. Quyền 600; không đưa lên Git hoặc gửi công khai. Logs/backtest không nằm trong bản sao này.')
            return target
        finally:
            if os.path.exists(partial): os.unlink(partial)
    finally: lease.close()


def configure_access(mode,allowed_ips='',port=None):
    env=Environment(); lease=RuntimeLease(env.data/'runtime.lock')
    try:
        port=env.port if port is None else port
        env.save_access({'RICE_ACCESS_MODE':mode,'RICE_HOST':'0.0.0.0' if mode=='direct' else '127.0.0.1',
            'RICE_PORT':str(port),'RICE_COOKIE_SECURE':'1' if mode=='https' else '0','RICE_ALLOWED_IPS':allowed_ips})
        print('[ACCESS]',mode,'· port',port,'· đã lưu. Không sửa firewall, SSH hoặc chứng chỉ.')
        if mode=='https': print('[ACCESS] Cần reverse proxy HTTPS được cấu hình riêng; backend chỉ nghe loopback.')
        elif mode=='direct': print('[ACCESS] HTTP chưa mã hóa. Whitelist nguồn truy cập không thay thế TLS/SSH tunnel.')
    finally: lease.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','state-check','preset-va','backup','access-show','access-set'))
    parser.add_argument('--lock'); parser.add_argument('--directory')
    parser.add_argument('--mode',choices=('direct','ssh','https')); parser.add_argument('--allow-ips',default='')
    parser.add_argument('--port',type=int)
    args=parser.parse_args()
    if args.action=='check': raise SystemExit(check_environment(args.lock))
    if args.action=='state-check': raise SystemExit(state_check())
    if args.action=='preset-va': apply_va()
    if args.action=='backup':
        if not args.directory: parser.error('backup cần --directory')
        backup(args.directory)
    if args.action=='access-set':
        if not args.mode: parser.error('access-set cần --mode')
        configure_access(args.mode,args.allow_ips,args.port)
    if args.action=='access-show':
        _,_,values=paths()
        mode=values.get('RICE_ACCESS_MODE') or 'direct'
        print('Truy cập:',{'direct':'IP trực tiếp','ssh':'SSH tunnel','https':'HTTPS'}.get(mode,mode))
        print('Web trên VPS:',values.get('RICE_HOST','127.0.0.1')+':'+str(values.get('RICE_PORT') or 8080))
        if mode=='direct':
            print('UI: http://IP-PUBLIC-VPS:'+str(values.get('RICE_PORT') or 8080))
            print('Whitelist IP public nhà ở Cloud Firewall. IP đổi: cập nhật rule cloud; để app allowlist trống nếu chỉ quản lý tại cloud.')
            print('HTTP chưa mã hóa đường truyền. Script không tự sửa firewall.')
        elif mode=='ssh':
            connection=os.environ.get('RICE_SSH_CONNECTION','').split()
            host=connection[2] if len(connection)==4 else 'IP-VPS'
            ssh_port=connection[3] if len(connection)==4 else '22'
            if ':' in host: host='['+host+']'
            login=os.environ.get('RICE_SSH_LOGIN') or 'user'
            print('Trên máy WINDOWS, mở terminal và chạy:')
            print('ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -p '+ssh_port+
                  ' -L 127.0.0.1:18080:127.0.0.1:'+str(values.get('RICE_PORT') or 8080)+' '+login+'@'+host)
            print('Sau đó mở trình duyệt trên máy nhà: http://127.0.0.1:18080')
            print('Giữ cửa sổ tunnel mở khi dùng web. Đóng tunnel thì Rice trên VPS vẫn chạy.')
        else: print('HTTPS proxy/chứng chỉ cấu hình riêng; backend chỉ nghe loopback.')


if __name__=='__main__': main()
