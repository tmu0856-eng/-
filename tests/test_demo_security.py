from collections import defaultdict,deque

import pytest
from fastapi.testclient import TestClient

from backend import db,security
from backend.app import app


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setattr(security,'_BUCKETS',defaultdict(deque))
    monkeypatch.setenv('DEMO_USERNAME','reviewer')
    monkeypatch.setenv('DEMO_PASSWORD','fixture-password-only')
    monkeypatch.setenv('DEMO_COOKIE_SECURE','0')
    with TestClient(app) as client:yield client


def sign_in(client):
    return client.post('/api/auth/login',json={'username':'reviewer','password':'fixture-password-only'})


def test_data_and_mutations_require_login(client):
    assert client.get('/api/health').json()=={'status':'ok'}
    assert client.get('/').status_code==200
    assert client.get('/api/papers').status_code==401
    assert client.post('/api/ask',json={'question':'a question'}).status_code==401


def test_cookie_login_and_logout(client):
    value=sign_in(client)
    assert value.status_code==200 and 'HttpOnly' in value.headers['set-cookie'] and 'SameSite=strict' in value.headers['set-cookie']
    assert client.get('/api/papers').status_code==200
    assert client.get('/api/auth/status').json()['authenticated']
    client.post('/api/auth/logout')
    assert client.get('/api/papers').status_code==401


def test_invalid_password_does_not_issue_session(client):
    assert client.post('/api/auth/login',json={'username':'reviewer','password':'wrong'}).status_code==401
    assert not client.cookies.get(security.COOKIE)


def test_tampered_or_expired_token_is_rejected(client,monkeypatch):
    sign_in(client)
    token=client.cookies.get(security.COOKIE)
    client.cookies.clear();client.cookies.set(security.COOKIE,token+'x')
    assert client.get('/api/papers').status_code==401
    client.cookies.clear();client.cookies.set(security.COOKIE,token)
    monkeypatch.setattr(security.time,'time',lambda:10**12)
    assert client.get('/api/papers').status_code==401


def test_login_attempt_budget(client):
    for _ in range(5):client.post('/api/auth/login',json={'username':'x','password':'wrong'})
    assert sign_in(client).status_code==429


def test_request_budget_does_not_block_reading(client,monkeypatch):
    sign_in(client);monkeypatch.setenv('DEMO_REQUESTS_PER_MINUTE','1')
    assert client.post('/api/auth/logout').status_code==200
    sign_in(client)
    assert client.post('/api/embeddings/reindex').status_code==429
    assert client.get('/api/papers').status_code==200


def test_local_mode_keeps_existing_flow(client,monkeypatch):
    monkeypatch.delenv('DEMO_PASSWORD')
    assert client.get('/api/papers').status_code==200


def test_independent_login_route_and_workspace_redirect(client):
    response=client.get('/',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/login'
    page=client.get('/login')
    assert page.status_code==200 and page.headers['cache-control']=='no-store'
    assert 'id="login-form"' in page.text and 'id="navigation"' not in page.text
    assert 'fixture-password-only' not in page.text
    assert client.get('/login.css').status_code==200
    assert client.get('/login.js').status_code==200
    sign_in(client)
    response=client.get('/',follow_redirects=False)
    assert response.status_code==200 and 'id="navigation"' in response.text
    client.post('/api/auth/logout')
    assert client.get('/',follow_redirects=False).status_code==303


def test_workspace_stays_directly_available_in_local_mode(client,monkeypatch):
    monkeypatch.delenv('DEMO_PASSWORD')
    response=client.get('/',follow_redirects=False)
    assert response.status_code==200 and 'id="navigation"' in response.text


def test_cross_origin_login_is_rejected(client):
    value=client.post('/api/auth/login',headers={'Origin':'https://unrelated.example'},json={'username':'reviewer','password':'fixture-password-only'})
    assert value.status_code==403


def test_public_mode_fails_closed_without_password(tmp_path,monkeypatch):
    monkeypatch.delenv('DEMO_PASSWORD',raising=False)
    monkeypatch.setenv('DEMO_REQUIRE_LOGIN','1')
    with pytest.raises(RuntimeError,match='DEMO_PASSWORD'):
        with TestClient(app):pass


def test_demo_full_translation_budget_is_visible(client,monkeypatch):
    from backend import llm
    sign_in(client)
    monkeypatch.setattr(llm,'configured',lambda:True)
    db.execute("INSERT INTO papers(id,title,status,page_count) VALUES('book','Book','ready',541)")
    response=client.post('/api/papers/book/translate-all')
    assert response.status_code==403 and '20' in response.json()['detail']
