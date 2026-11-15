import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import agent, db, llm, retrieval, reranker
from backend.app import app


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    monkeypatch.setattr(reranker, '_RUNTIME', {'state':'idle','device':'','error':'','last_candidates':0,'last_ms':None})
    monkeypatch.setattr(reranker, '_MODEL', None)
    monkeypatch.setattr(reranker, '_MODEL_KEY', None)
    monkeypatch.setattr(reranker, '_TOKENIZER', None)


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('RERANKER_MODEL', reranker.MODEL_ID)
    monkeypatch.setenv('RERANKER_CANDIDATES', '40')
    with TestClient(app) as client:
        for pid in ('p1','p2'):
            db.execute('INSERT INTO papers(id,title,status) VALUES(?,?,?)', (pid, pid, 'ready'))
            for index in range(25):
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                           (f'{pid}-{index}', pid, index+1, f'topic paper {pid} evidence number {index}', '[1,2,30,40]', None))
        yield client


def test_second_stage_can_promote_beyond_old_cutoff(library, monkeypatch):
    baseline, _ = retrieval.retrieve('topic', ['p1'], 25, use_reranker=False)
    target = baseline[-1]
    seen = []
    def score(query, passages):
        seen.extend(passages)
        return [9 if p == target['text'] else -1 for p in passages]
    monkeypatch.setattr(reranker, 'score', score)
    result, info = retrieval.retrieve('topic', ['p1'], 2)
    assert len(seen) == 25
    assert result[0]['chunk_id'] == target['chunk_id']
    assert result[0]['bbox'] == target['bbox'] and result[0]['page'] == target['page']
    assert [s['source_id'] for s in result] == ['S1','S2']
    assert info['reranker']['applied'] and info['reranker']['candidates'] == 25
    assert all(s['paper_id'] == 'p1' for s in result)


def test_reranking_preserves_cross_paper_coverage(library, monkeypatch):
    monkeypatch.setattr(reranker, 'score', lambda q, texts: [100 if 'p1' in t else -100 for t in texts])
    result, info = retrieval.retrieve('topic', ['p1','p2'], 2)
    assert info['reranker']['candidates'] == 40
    assert {s['paper_id'] for s in result} == {'p1','p2'}


def test_agent_uses_same_stage_with_stable_source_ids(library, monkeypatch):
    monkeypatch.setattr(reranker, 'score', lambda q,texts:list(range(len(texts))))
    session=agent.Session(['p1'],False)
    value,args,message,state=session.execute('search_evidence',json.dumps({'query':'topic','paper_id':'p1','limit':4}))
    assert state=='done' and '精排保留' in message
    assert value['retrieval']['reranker']['applied']
    source=value['sources'][0]
    page=session.execute('read_page',json.dumps({'paper_id':'p1','page':source['page']}))[0]
    assert next(s for s in page['sources'] if s['chunk_id']==source['chunk_id'])['source_id']==source['source_id']


def test_author_header_survives_a_low_rerank_score(library, monkeypatch):
    blocks=[{'bbox':[50,50,250,90],'text':'First Author\nExample University'}]
    db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)', ('p1',1,'author',json.dumps(blocks),600,800))
    monkeypatch.setattr(reranker, 'score', lambda q, texts: [-100 if 'First Author' in t else 10 for t in texts])
    result, _ = retrieval.retrieve('topic 作者', ['p1'], 2)
    assert result[0]['chunk_id'] == 'frontmatter:p1:1'
    assert 'Example University' in result[0]['text']


@pytest.mark.parametrize('values', [[float('nan')], [float('inf')], []])
def test_bad_scores_fall_back_without_losing_sources(library, monkeypatch, values):
    baseline,_ = retrieval.retrieve('topic',['p1'],2,use_reranker=False)
    monkeypatch.setattr(reranker, 'score', lambda q, texts: values)
    result,info = retrieval.retrieve('topic',['p1'],2)
    assert [s['chunk_id'] for s in result] == [s['chunk_id'] for s in baseline]
    assert info['reranker']['error'] and not info['reranker']['applied']


def test_model_failure_keeps_hybrid_order(library, monkeypatch):
    def fail(*args):
        raise llm.ModelError('fixture model unavailable')
    monkeypatch.setattr(reranker, 'score', fail)
    result,info = retrieval.retrieve('topic',['p1'],3)
    assert len(result) == 3 and '保留混合召回' in info['reranker']['error']
    assert not any('rerank_score' in s for s in result)


def test_disabled_stage_never_calls_model(library, monkeypatch):
    monkeypatch.delenv('RERANKER_MODEL')
    monkeypatch.setattr(reranker, 'score', lambda *args: pytest.fail('Disabled stage was invoked'))
    result,info = retrieval.retrieve('topic',['p1'],2)
    assert len(result) == 2 and not info['reranker']['applied']


def test_bad_candidate_budget_falls_back(library, monkeypatch):
    monkeypatch.setenv('RERANKER_CANDIDATES','10000')
    monkeypatch.setattr(reranker, 'score', lambda *args: pytest.fail('Invalid budget was invoked'))
    result,info = retrieval.retrieve('topic',['p1'],2)
    assert len(result) == 2 and 'RERANKER_CANDIDATES' in info['reranker']['error']


def test_only_one_candidate_does_not_load_model(library, monkeypatch):
    db.execute("DELETE FROM chunks WHERE id!='p1-0'")
    monkeypatch.setattr(reranker, 'score', lambda *args: pytest.fail('Only one source needs no ranking'))
    result,info = retrieval.retrieve('topic',['p1'],2)
    assert len(result) == 1 and not info['reranker']['applied']


def test_scores_preserve_anchors_and_stable_ties(monkeypatch):
    candidates=[{'chunk_id':str(i),'text':str(i),'paper_id':'p','page':i+1,'bbox':[1,2,3,4]} for i in range(3)]
    monkeypatch.setattr(reranker,'score',lambda q,texts:[-2,5,5])
    result=reranker.rerank('q',candidates)
    assert [s['chunk_id'] for s in result] == ['1','2','0']
    assert result[0]['page']==2 and result[0]['bbox']==[1,2,3,4]
    assert 'rerank_score' not in candidates[0]


def test_missing_model_visible_in_status(tmp_path, monkeypatch):
    monkeypatch.setenv('RERANKER_MODEL',reranker.MODEL_ID)
    monkeypatch.setenv('RERANKER_LOCAL_PATH',str(tmp_path))
    assert '文件未就绪' in reranker.status()['error']


def test_local_batching_caches_model_and_uses_safe_loading(tmp_path, monkeypatch):
    monkeypatch.setenv('RERANKER_MODEL',reranker.MODEL_ID)
    monkeypatch.setenv('RERANKER_LOCAL_PATH',str(tmp_path))
    monkeypatch.setenv('RERANKER_BATCH_SIZE','2')
    (tmp_path/'researchdock-model.json').write_text(json.dumps({'model':reranker.MODEL_ID,'revision':'a'*40}))
    (tmp_path/'model.safetensors').touch()
    calls=[]
    class Tensor:
        def __init__(self,values):self.values=np.asarray(values)
        def to(self,device):return self
        def reshape(self,*shape):return Tensor(self.values.reshape(*shape))
        def float(self):return self
        def cpu(self):return self
        def numpy(self):return self.values
    class Model:
        config=SimpleNamespace(num_labels=1)
        def to(self,device):return self
        def eval(self):return self
        def __call__(self,input_ids,**kwargs):return SimpleNamespace(logits=input_ids)
    def load_model(*args,**kwargs):calls.append(kwargs);return Model()
    def tokenize(pairs,**kwargs):return {'input_ids':Tensor([[float(p)] for q,p in pairs])}
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,empty_cache=lambda:None),float16='float16',float32='float32',inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules,'transformers',SimpleNamespace(AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=load_model),AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a,**kw:tokenize)))
    assert reranker.score('query',['1','2','3']) == [1,2,3]
    assert reranker.score('query',['4']) == [4]
    assert len(calls)==1 and calls[0]['trust_remote_code'] is False and calls[0]['local_files_only'] is True
    assert calls[0]['use_safetensors'] is True and calls[0]['torch_dtype']=='float16'


def test_excessive_pairs_rejected_before_loading(monkeypatch):
    monkeypatch.setenv('RERANKER_MODEL',reranker.MODEL_ID)
    with pytest.raises(llm.ModelError,match='80'):
        reranker.score('query',['text']*81)
