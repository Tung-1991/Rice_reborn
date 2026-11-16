"""Financial state commits atomically; network calls never run inside transactions."""
from contextlib import contextmanager
import json,sqlite3,threading,time
from .models import wire
class Store:
    def __init__(self,path):
        self.path=str(path); self.lock=threading.RLock(); self.local=threading.local()
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS documents(kind TEXT,id TEXT,payload TEXT,updated REAL,PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,kind TEXT,mode TEXT,symbol TEXT,at REAL,payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS event_search ON events(kind,at)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,at REAL)')
    @contextmanager
    def transaction(self):
        with self.lock:
            active=getattr(self.local,'db',None)
            if active is not None: yield active; return
            db=sqlite3.connect(self.path,timeout=15,isolation_level=None); db.row_factory=sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE'); self.local.db=db
            try: yield db; db.execute('COMMIT')
            except BaseException:
                if db.in_transaction: db.execute('ROLLBACK')
                raise
            finally: self.local.db=None; db.close()
    def get(self,kind,key='main',default=None):
        with self.transaction() as db:
            r=db.execute('SELECT payload FROM documents WHERE kind=? AND id=?',(kind,key)).fetchone()
            return json.loads(r[0]) if r else default
    def put(self,kind,key,value):
        payload=json.dumps(wire(value),ensure_ascii=False,allow_nan=False)
        with self.transaction() as db:
            previous=db.execute('SELECT payload FROM documents WHERE kind=? AND id=?',(kind,str(key))).fetchone()
            if previous and previous[0]==payload: return
            db.execute('INSERT INTO documents VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',(kind,str(key),payload,time.time()))
    def all(self,kind):
        with self.transaction() as db: return [json.loads(r[0]) for r in db.execute('SELECT payload FROM documents WHERE kind=? ORDER BY updated',(kind,))]
    def delete(self,kind,key):
        with self.transaction() as db: db.execute('DELETE FROM documents WHERE kind=? AND id=?',(kind,str(key)))
    def receipt(self,key):
        with self.transaction() as db: return db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(key,time.time())).rowcount==1
    def event(self,key,kind,payload,mode='',symbol='',at=None):
        with self.transaction() as db: return db.execute('INSERT OR IGNORE INTO events(id,kind,mode,symbol,at,payload) VALUES(?,?,?,?,?,?)',(key,kind,mode,symbol,at or time.time(),json.dumps(wire(payload),ensure_ascii=False,allow_nan=False))).rowcount==1
    def history(self,kind='',mode='',limit=100,offset=0):
        with self.transaction() as db:
            rows=db.execute('SELECT * FROM events WHERE (?="" OR kind=?) AND (?="" OR mode=?) ORDER BY seq DESC LIMIT ? OFFSET ?',(kind,kind,mode,mode,min(500,int(limit)),max(0,int(offset))))
            return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]
    def prune(self):
        with self.transaction() as db:
            db.execute('DELETE FROM events WHERE kind="TRACE" AND at<?',(time.time()-7*86400,))
            db.execute('DELETE FROM documents WHERE kind="outbox" AND updated<? AND json_extract(payload,"$.status")="SENT"',(time.time()-30*86400,))
