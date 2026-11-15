import pytest
from fastapi.testclient import TestClient

from backend import conversation, db, retrieval
from backend.app import ChatTurn, app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path/'data')
    with TestClient(app) as client:
        for pid in ('p1','p2'):
            db.execute('INSERT INTO papers(id,title,status) VALUES(?,?,?)',(pid,pid,'ready'))
        yield client


def history():
    return [ChatTurn(role='user',content='Attention Is All You Need 的第六位作者是谁？'),
            ChatTurn(role='assistant',content='Untrusted old answer: beta2=9999',paper_ids=['p1'])]


def test_followup_uses_prior_sources_without_reusing_answer(client):
    query,scope,info=conversation.prepare('其中 Adam 参数是多少？',[],history())
    assert scope==['p1'] and info['used']
    assert '9999' not in query and '作者' not in query


def test_explicit_scope_switch_wins(client):
    query,scope,info=conversation.prepare('为什么？',['p2'],history())
    assert query=='为什么？' and scope==['p2'] and not info['used']


def test_new_topic_does_not_inherit_scope(client):
    _,scope,info=conversation.prepare('BERT 的架构是什么？',[],history())
    assert not scope and not info['used']


def test_generic_followup_carries_only_user_topic(client):
    query,scope,info=conversation.prepare('详细解释一下',[],history())
    assert '第六位作者' in query and '9999' not in query and scope==['p1']


def test_deleted_prior_source_requires_reselection(client):
    db.execute("DELETE FROM papers WHERE id='p1'")
    with pytest.raises(ValueError,match='重新选择'):
        conversation.prepare('其中 Adam 参数是多少？',[],history())


def test_direct_and_stream_share_resolved_scope(client, monkeypatch):
    seen=[]
    def answer(query,ids):
        seen.append((query,ids))
        yield {'event':'result','data':{'answer':'fixture','sources':[],'trace':[],'mode':'generated'}}
    monkeypatch.setattr(retrieval,'answer_stream',answer)
    body={'question':'其中 Adam 参数是多少？','history':[t.model_dump() for t in history()]}
    result=client.post('/api/ask',json=body).json()
    assert result['context']['paper_ids']==['p1']
    stream=client.post('/api/ask/stream',json=body).text
    assert 'resolve_context' in stream and 'event: result' in stream
    assert all(ids==['p1'] for _,ids in seen)


def test_history_limits_are_enforced(client):
    body={'question':'其中 Adam 参数是多少？','history':[{'role':'user','content':'x'}]*13}
    assert client.post('/api/ask',json=body).status_code==422
