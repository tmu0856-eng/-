"""完整题名限定事实问题；显式范围与跨论文引用问题保持原意。"""
import pytest
from fastapi.testclient import TestClient
from backend import db,retrieval
from backend.app import app


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    with TestClient(app):
        for pid,title in [('a','Attention Is All You Need'),('a2','Attention Is All You Need'),('b','Other Research Paper')]:
            db.execute('INSERT INTO papers(id,title,status) VALUES(?,?,?)',(pid,title,'ready'))
        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',('c','a',7,'Training used 8 NVIDIA P100 GPUs.','[1,2,3,4]',None))
        for i in range(30):
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(str(i),'b',10,'References: Attention Is All You Need.','[1,2,3,4]',None))
        yield


def test_full_title_resolves_versions_and_keeps_hardware_evidence(library):
    values,info=retrieval.retrieve('Attention Is All You Need 训练用了多少块 P100 GPU？')
    assert values[0]['chunk_id']=='c'
    assert set(info['inferred_paper_ids'])=={'a','a2'}


def test_explicit_scope_is_not_replaced(library):
    _,scope,inferred=retrieval._named_scope('Attention Is All You Need 使用什么 GPU？',['b'])
    assert scope==['b'] and not inferred


@pytest.mark.parametrize('query',['哪些论文引用 Attention Is All You Need？','比较 Attention Is All You Need 和 BERT 的训练设置'])
def test_cross_paper_intent_does_not_narrow_scope(library,query):
    assert retrieval._named_scope(query,[])==(query,[],False)
