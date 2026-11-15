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


def test_epsilon_followup_keeps_adam_topic_and_rejects_old_values(client):
    turns=[ChatTurn(role='user',content='请给出训练 GPU 数量，以及 Adam 的 beta2。'),
           ChatTurn(role='assistant',content='虚拟历史答案：epsilon=9999',paper_ids=['p1'])]
    query, scope, info=conversation.prepare('其中 epsilon 是多少？',[],turns)
    assert 'Adam 优化器' in query and '9999' not in query
    assert scope==['p1'] and info['resolved_object']=='Adam 优化器'


def test_parameter_topic_survives_multiple_short_followups(client):
    turns=[ChatTurn(role='user',content='Adam 参数是什么？'),
           ChatTurn(role='assistant',content='不可信历史结果',paper_ids=['p1']),
           ChatTurn(role='user',content='其中 beta2 是多少？'),
           ChatTurn(role='assistant',content='不可信数值',paper_ids=['p1'])]
    query,_,_=conversation.prepare('那 epsilon 呢？',[],turns)
    assert 'Adam 优化器' in query and '不可信' not in query


def test_ambiguous_parameter_requests_clarification_without_model(client,monkeypatch):
    turns=[ChatTurn(role='user',content='比较 Adam 和标签平滑的参数。'),
           ChatTurn(role='assistant',content='历史内容',paper_ids=['p1'])]
    monkeypatch.setattr(retrieval,'answer_stream',lambda *a:pytest.fail('澄清前不应检索或调用模型'))
    body={'question':'其中 epsilon 是多少？','history':[t.model_dump() for t in turns]}
    value=client.post('/api/ask',json=body).json()
    assert value['mode']=='clarification' and not value['sources']
    assert 'Adam' in value['answer'] and '标签平滑' in value['answer']
    stream=client.post('/api/ask/stream',json=body).text
    assert 'clarification' in stream


def test_explicit_new_parameter_object_overrides_old_topic(client):
    turns=[ChatTurn(role='user',content='Adam 的参数？'),
           ChatTurn(role='assistant',content='旧值',paper_ids=['p1'])]
    query,_,_=conversation.prepare('其中标签平滑的 epsilon 是多少？',[],turns)
    assert query=='其中标签平滑的 epsilon 是多少？'


def test_parameter_does_not_cross_new_topic(client):
    turns=[ChatTurn(role='user',content='Adam 参数是什么？'),
           ChatTurn(role='assistant',content='旧值',paper_ids=['p1']),
           ChatTurn(role='user',content='BERT 的架构是什么？'),
           ChatTurn(role='assistant',content='新话题',paper_ids=['p1'])]
    query,_,info=conversation.prepare('其中 epsilon 呢？',[],turns)
    assert 'Adam' not in query and 'resolved_object' not in info


def test_adamw_is_not_silently_changed_to_adam(client):
    turns=[ChatTurn(role='user',content='AdamW 的参数是什么？'),
           ChatTurn(role='assistant',content='旧值',paper_ids=['p1'])]
    query,_,info=conversation.prepare('其中 epsilon 呢？',[],turns)
    assert info['resolved_object']=='AdamW 优化器' and 'AdamW' in query


def test_short_clarification_reply_keeps_pending_question_and_paper_scope(client):
    turns=[ChatTurn(role='user',content='比较 Adam 和标签平滑。'),
           ChatTurn(role='assistant',content='原文比较',paper_ids=['p1']),
           ChatTurn(role='user',content='其中 epsilon 是多少？'),
           ChatTurn(role='assistant',content='请说明哪个对象')]
    query,scope,info=conversation.prepare('Adam',[],turns)
    assert 'epsilon' in query and 'Adam' in query and scope==['p1']
    assert not info.get('needs_clarification')
