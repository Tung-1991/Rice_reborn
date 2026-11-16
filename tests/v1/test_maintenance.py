from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import zipfile

import pytest
from dotenv import dotenv_values
from rice_reborn.config import DEFAULT_SETTINGS,DEFAULT_RULES,Environment
from rice_reborn.lease import RuntimeLease
from rice_reborn.maintenance import apply_va,backup,configure_access,state_check,check_environment
from rice_reborn.store import Store


@pytest.fixture
def home(tmp_path,monkeypatch):
    monkeypatch.setenv('RICE_HOME',str(tmp_path))
    monkeypatch.setenv('RICE_ENV_FILE',str(tmp_path/'.env'))
    for name in ('BINANCE_API_KEY','BINANCE_API_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'):
        monkeypatch.delenv(name,raising=False)
    return tmp_path


def test_empty_checks_do_not_create_runtime(home):
    assert state_check()==0
    assert check_environment()==0
    assert not (home/'data').exists()


def test_va_preserves_money_and_financial_state(home):
    env=Environment(); store=Store(env.data/'account-public.sqlite3')
    settings=deepcopy(DEFAULT_SETTINGS)
    settings.update(capital_usdt='123.456',watchlist=['SOLUSDT'])
    settings['rules']['take_profit_pct']=12
    store.put('settings','main',settings)
    store.put('position','held',{'quantity':'1','original':'1','entry':'25'})
    apply_va()
    result=store.get('settings')
    assert result['rules']==DEFAULT_RULES
    assert result['capital_usdt']=='123.456' and result['watchlist']==['SOLUSDT']
    assert store.get('position','held')['quantity']=='1'


def test_maintenance_refuses_running_backend(home):
    env=Environment(); lease=RuntimeLease(env.data/'runtime.lock')
    try:
        with pytest.raises(RuntimeError,match='backend'): apply_va()
        with pytest.raises(RuntimeError,match='backend'): configure_access('ssh')
        with pytest.raises(RuntimeError,match='backend'): backup(home/'backups')
    finally: lease.close()
    assert not (home/'backups').exists()


def test_backup_keeps_consistent_database_and_protected_credentials(home,capsys):
    env=Environment(); env.save_secrets({'BINANCE_API_KEY':'fake-key','BINANCE_API_SECRET':'fake-secret'})
    store=Store(env.data/'account-test.sqlite3')
    store.put('paper','cash','123.45')
    (env.data/'session.key').write_text('fake-session')
    archive=backup(home/'backups')
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'))
        for name,digest in manifest['files'].items(): assert hashlib.sha256(z.read(name)).hexdigest()==digest
        assert b'fake-secret' in z.read('config/.env')
        restored=home/'restored.sqlite3'; restored.write_bytes(z.read('data/v1/account-test.sqlite3'))
        with sqlite3.connect(restored) as db:
            assert json.loads(db.execute('SELECT payload FROM documents WHERE kind="paper"').fetchone()[0])=='123.45'
    assert 'fake-secret' not in capsys.readouterr().out
    if os.name!='nt': assert archive.stat().st_mode&0o777==0o600

def test_backup_includes_committed_wal_without_touching_live_database(home):
    env=Environment(); path=env.data/'account-wal.sqlite3'
    connection=sqlite3.connect(path)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE sample(value TEXT)')
        connection.execute('INSERT INTO sample VALUES(?)',('committed WAL',)); connection.commit()
        before=path.read_bytes(); wal=Path(str(path)+'-wal'); before_wal=wal.read_bytes()
        archive=backup(home/'backups')
        assert path.read_bytes()==before and wal.read_bytes()==before_wal
        with zipfile.ZipFile(archive) as z:
            restored=home/'restored-wal.sqlite3'; restored.write_bytes(z.read('data/v1/account-wal.sqlite3'))
        with sqlite3.connect(restored) as db: assert db.execute('SELECT value FROM sample').fetchone()[0]=='committed WAL'
    finally: connection.close()

@pytest.mark.skipif(os.name=='nt',reason='Linux symlink ownership boundary')
def test_backup_rejects_runtime_symlink_before_creating_lock(home):
    (home/'data').mkdir(); outside=home/'outside'; outside.mkdir()
    (home/'data/v1').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'): backup(home/'backups')
    assert not (outside/'runtime.lock').exists()


def test_state_check_reports_corruption_without_repair(home):
    env=Environment(); store=Store(env.data/'account-public.sqlite3')
    order={'side':'BUY','quantity':'1','filled':'.7','remaining':'.8','status':'NEW'}
    store.put('order','broken',order)
    assert state_check()==1
    assert store.get('order','broken')==order


def test_access_modes_preserve_secrets_and_custom_port(home):
    env=Environment(); env.save_secrets({'BINANCE_API_SECRET':'fake-secret'})
    env.save_access({'RICE_PORT':'9090'})
    configure_access('ssh')
    current=dotenv_values(home/'.env')
    assert current['RICE_HOST']=='127.0.0.1' and current['RICE_PORT']=='9090'
    assert current['RICE_COOKIE_SECURE']=='0'
    configure_access('https')
    current=dotenv_values(home/'.env')
    assert current['RICE_HOST']=='127.0.0.1' and current['RICE_COOKIE_SECURE']=='1'
    assert current['BINANCE_API_SECRET']=='fake-secret'
    configure_access('direct','198.51.100.10/32')
    assert dotenv_values(home/'.env')['RICE_HOST']=='0.0.0.0'


def test_invalid_access_allowlist_does_not_replace_config(home):
    env=Environment(); env.save_secrets({'TELEGRAM_BOT_TOKEN':'fake-token'})
    before=(home/'.env').read_bytes()
    with pytest.raises(ValueError): configure_access('direct','not-an-ip')
    assert (home/'.env').read_bytes()==before

def test_direct_access_instructions_use_cloud_firewall(home,monkeypatch,capsys):
    import sys
    from rice_reborn.maintenance import main
    env=Environment(); env.save_access({'RICE_ACCESS_MODE':'direct','RICE_HOST':'0.0.0.0','RICE_PORT':'8080','RICE_ALLOWED_IPS':''})
    monkeypatch.setattr(sys,'argv',['maintenance','access-show'])
    main(); output=capsys.readouterr().out
    assert 'http://IP-PUBLIC-VPS:8080' in output and 'Cloud Firewall' in output
    assert 'SSH tunnel:' not in output and '18080' not in output


@pytest.mark.parametrize('server,target',[
    ('203.0.113.10','kaiser@203.0.113.10'),
    ('2001:db8::10','kaiser@[2001:db8::10]'),
])
def test_ssh_access_instructions_use_operator_and_connection(home,monkeypatch,capsys,server,target):
    import sys
    from rice_reborn.maintenance import main
    env=Environment(); env.save_access({'RICE_ACCESS_MODE':'ssh','RICE_HOST':'127.0.0.1','RICE_PORT':'9090'})
    monkeypatch.setenv('RICE_SSH_CONNECTION',f'198.51.100.7 12345 {server} 2222')
    monkeypatch.setenv('RICE_SSH_LOGIN','kaiser')
    monkeypatch.setattr(sys,'argv',['maintenance','access-show'])
    main(); output=capsys.readouterr().out
    assert '-p 2222' in output and '127.0.0.1:18080:127.0.0.1:9090 '+target in output
    assert 'WINDOWS' in output and 'http://127.0.0.1:18080' in output
    assert 'Secure cookie:' not in output and 'IP-PUBLIC-VPS' not in output
