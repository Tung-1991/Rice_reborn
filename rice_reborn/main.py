"""Windows development / Linux service entry point."""
import argparse,getpass,logging,signal,sys
from logging.handlers import TimedRotatingFileHandler
from werkzeug.security import generate_password_hash
from .config import Environment

def main():
    p=argparse.ArgumentParser(description='Rice Reborn · Binance Spot · VA')
    p.add_argument('--demo',action='store_true',help='Dữ liệu mô phỏng, không đọc key/gọi mạng/gửi Telegram')
    p.add_argument('--set-password',action='store_true',help='Tạo/đổi mật khẩu quản trị')
    p.add_argument('--host'); p.add_argument('--port',type=int); p.add_argument('--no-scheduler',action='store_true'); p.add_argument('--once',action='store_true')
    args=p.parse_args(); env=Environment(demo=args.demo)
    if args.set_password:
        from .lease import RuntimeLease
        password_lease=RuntimeLease(env.data/'runtime.lock')
        try:
            password=getpass.getpass('Mật khẩu quản trị (ít nhất 12 ký tự): ')
            if len(password)<12: raise SystemExit('Mật khẩu phải từ 12 ký tự.')
            if password!=getpass.getpass('Nhập lại: '): raise SystemExit('Mật khẩu không khớp.')
            env.save_secrets({'RICE_PASSWORD_HASH':generate_password_hash(password)}); print('Đã lưu hash mật khẩu; các session cũ bị thu hồi sau khi backend khởi động lại.')
        finally: password_lease.close()
        return
    from .lease import RuntimeLease
    import atexit
    lease=RuntimeLease(env.data/'runtime.lock'); atexit.register(lease.close)
    logs=env.data/'logs'; logs.mkdir(exist_ok=True)
    handler=TimedRotatingFileHandler(logs/'rice.log',when='midnight',backupCount=30,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s',handlers=[handler,logging.StreamHandler()])
    from .service import Service
    from .web import create_web_app
    service=Service(env); app=create_web_app(service)
    if args.once:
        if not env.demo: service.refresh()
        service.step(); print(__import__('json').dumps(service.snapshot(),ensure_ascii=False)); service.close(); return
    host=args.host or env.host; port=args.port or env.port
    if args.demo and host not in ('127.0.0.1','localhost','::1'): raise SystemExit('Demo chỉ bind localhost.')
    if not args.demo and not env.get('RICE_PASSWORD_HASH'):
        print('Chưa có mật khẩu. Chạy: python -m rice_reborn.main --set-password')
    if not args.no_scheduler: service.start()
    def shutdown(*_):
        app.rice_service['service'].close(); raise SystemExit(0)
    signal.signal(signal.SIGINT,shutdown); signal.signal(signal.SIGTERM,shutdown)
    from waitress import serve
    print(f'Rice Reborn {"DEMO" if args.demo else "LIVE"}: http://{host}:{port}',flush=True)
    serve(app,host=host,port=port,threads=4,channel_timeout=30)

if __name__=='__main__': main()
