"""检查检索失败、有界重试与真实持久化缓存回退。"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import db, research
from backend.app import app

FEED = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title>
<summary>A real-format fixture about attention. https://github.com/example/project</summary>
<published>2017-06-12T00:00:00Z</published><author><name>Fixture Author</name></author></entry></feed>'''


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setattr(research,'_wait_for_arxiv',lambda:None)
    research.CACHE.clear()
    with TestClient(app) as value:
        yield value
    research.CACHE.clear()


def transport(monkeypatch,handler):
    original=httpx.Client
    monkeypatch.setattr(research.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))


def seed_cache(minutes=10):
    stamp=(datetime.now(timezone.utc)-timedelta(minutes=minutes)).isoformat()
    db.execute('INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?,?)',('attention',12,stamp,db.encode(research._parse_arxiv(FEED))))
    return stamp


def test_success_and_persistent_cache_avoid_repeated_network_calls(client,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,content=FEED)
    transport(monkeypatch,handler)
    first=client.get('/api/discover',params={'query':'attention'})
    assert first.status_code==200
    assert first.json()[0]['retrieval']['cache_state']=='fresh'
    research.CACHE.clear()  # A new server process can recover from SQLite as well.
    second=client.get('/api/discover',params={'query':'attention'})
    assert len(calls)==1
    assert second.json()[0]['retrieval']['cache_state']=='cached'
    assert second.json()[0]['id']=='1706.03762v7'


@pytest.mark.parametrize('failure',['timeout','http503','invalid_xml'])
def test_retry_once_then_return_verified_feed(client,monkeypatch,failure):
    calls=[]
    def handler(request):
        calls.append(request.url.host)
        if len(calls)==1:
            if failure=='timeout':raise httpx.ReadTimeout('fixture timeout',request=request)
            if failure=='http503':return httpx.Response(503,text='temporarily unavailable')
            return httpx.Response(200,text='<html>not an Atom feed</html>')
        return httpx.Response(200,content=FEED)
    transport(monkeypatch,handler)
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==200
    assert len(response.json())==1
    assert calls==['export.arxiv.org','arxiv.org']


def test_both_hosts_fail_with_actionable_arxiv_error(client,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(503,text='upstream failed')
    transport(monkeypatch,handler)
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==502
    assert len(calls)==2
    assert response.json()['code']=='arxiv_unavailable'
    assert 'arXiv' in response.json()['detail']
    assert 'PDF' in response.json()['detail']
    assert '未生成虚构结果' not in response.json()['detail']


def test_rate_limit_is_not_retried_immediately(client,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':'120'})
    transport(monkeypatch,handler)
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==503
    assert response.json()['code']=='arxiv_rate_limited'
    assert len(calls)==1


def test_outage_can_serve_time_labelled_recent_cache(client,monkeypatch):
    stamp=seed_cache()
    transport(monkeypatch,lambda r:httpx.Response(503))
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==200
    meta=response.json()[0]['retrieval']
    assert meta['cache_state']=='stale'
    assert meta['fetched_at']==stamp


def test_expired_cache_is_not_presented_as_current(client,monkeypatch):
    seed_cache(minutes=25*60)
    transport(monkeypatch,lambda r:httpx.Response(503))
    assert client.get('/api/discover',params={'query':'attention'}).status_code==502


def test_atom_query_error_does_not_look_like_empty_success(client,monkeypatch):
    error=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#incorrect_id_format</id><title>Error</title></entry></feed>'
    transport(monkeypatch,lambda r:httpx.Response(200,content=error))
    response=client.get('/api/discover',params={'query':'incorrect query'})
    assert response.status_code==422
    assert response.json()['code']=='arxiv_invalid_query'


def test_empty_atom_feed_is_a_valid_empty_result(client,monkeypatch):
    transport(monkeypatch,lambda r:httpx.Response(200,content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>'))
    response=client.get('/api/discover',params={'query':'nonexistent fixture'})
    assert response.status_code==200 and response.json()==[]


@pytest.mark.parametrize('query',['1706.03762v7','https://arxiv.org/abs/1706.03762v7'])
def test_identifier_search_uses_id_list(client,monkeypatch,query):
    def handler(request):
        assert request.url.params['id_list']=='1706.03762v7'
        assert 'search_query' not in request.url.params
        return httpx.Response(200,content=FEED)
    transport(monkeypatch,handler)
    assert client.get('/api/discover',params={'query':query}).json()[0]['id']=='1706.03762v7'


def test_legacy_arxiv_identifier_keeps_category(client,monkeypatch):
    feed=FEED.replace(b'1706.03762v7',b'hep-th/9901001')
    transport(monkeypatch,lambda r:httpx.Response(200,content=feed))
    result=client.get('/api/discover',params={'query':'hep-th/9901001'})
    assert result.json()[0]['id']=='hep-th/9901001'


def test_redirects_never_visit_an_untrusted_host(client,monkeypatch):
    hosts=[]
    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(302,headers={'Location':'https://untrusted.example/api/query'})
    transport(monkeypatch,handler)
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code in (502,504)
    assert hosts==['export.arxiv.org','arxiv.org']


def test_official_redirect_preserves_query(client,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(503)
        if len(calls)==2:return httpx.Response(301,headers={'Location':'https://export.arxiv.org/api/query'})
        assert request.url.params['search_query']=='all:attention'
        return httpx.Response(200,content=FEED)
    transport(monkeypatch,handler)
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==200 and len(calls)==3


def test_feedback_reranks_persistent_cache_without_remote_fetch(client,monkeypatch):
    seed_cache(minutes=1)
    def handler(request):raise AssertionError('Fresh cache must not fetch remotely')
    transport(monkeypatch,handler)
    assert client.post('/api/feedback',json={'arxiv_id':'1706.03762v7','value':1}).status_code==200
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.json()[0]['feedback']==1


def test_concurrent_identical_searches_share_one_request(client,monkeypatch):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,content=FEED)
    transport(monkeypatch,handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:research.arxiv_search('attention'),range(2)))
    assert len(calls)==1 and all(len(r)==1 for r in results)


def test_stopword_only_metadata_does_not_crash_ranking(client,monkeypatch):
    db.execute("INSERT INTO papers(id,title,abstract,status) VALUES('fixture','the and','or the','ready')")
    feed=FEED.replace(b'Attention Is All You Need',b'the and').replace(b'A real-format fixture about attention. https://github.com/example/project',b'or the')
    transport(monkeypatch,lambda r:httpx.Response(200,content=feed))
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==200
    assert response.json()[0]['score']==0


def test_invalid_cached_payload_is_refetched(client,monkeypatch):
    stamp=datetime.now(timezone.utc).isoformat()
    db.execute('INSERT INTO discovery_cache VALUES(?,?,?,?)',('attention',12,stamp,'{"not":"a paper list"}'))
    transport(monkeypatch,lambda r:httpx.Response(200,content=FEED))
    response=client.get('/api/discover',params={'query':'attention'})
    assert response.status_code==200
    assert response.json()[0]['retrieval']['cache_state']=='fresh'
