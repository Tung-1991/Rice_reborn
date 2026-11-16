import socket
import pytest
from rice_reborn.config import Environment
from rice_reborn.service import Service

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a,**kw): raise AssertionError('Network forbidden in offline tests')
    monkeypatch.setattr(socket.socket,'connect',blocked)

@pytest.fixture
def service(tmp_path): return Service(Environment(tmp_path,demo=True))

@pytest.fixture
def client(service):
    from rice_reborn.web import create_web_app
    app=create_web_app(service); app.config['TESTING']=True
    c=app.test_client(); c.get('/')
    with c.session_transaction() as s: c.token=s['csrf']
    return c
