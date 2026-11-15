import pytest

from backend import db,retrieval


def test_table_context_keeps_headers_and_full_page_provenance(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path);db.init()
    db.execute("INSERT INTO papers(id,status) VALUES('p','ready')")
    db.execute('INSERT INTO pages(paper_id,page,text,width,height) VALUES(?,?,?,?,?)',
               ('p',2,'Table 1\nModel Parameters Context\nA 100M 512\nB 1B 1024',600,800))
    source={'paper_id':'p','page':2,'chunk_id':'fragment','text':'A 100M 512','bbox':[1,2,3,4]}
    result=retrieval._page_context('表1中的参数量和上下文',[source,{**source,'chunk_id':'other'}])
    assert len(result)==1 and 'Model Parameters Context' in result[0]['text']
    assert result[0]['bbox']==[0,0,600,800]
    assert result[0]['chunk_id']=='page-context:p:2' and result[0]['origin_chunk_id']=='fragment'
    assert source['text']=='A 100M 512' and source['bbox']==[1,2,3,4]


def test_unrelated_question_preserves_precise_fragment():
    source={'paper_id':'p','page':2,'chunk_id':'fragment','text':'a fact','bbox':[1,2,3,4]}
    assert retrieval._page_context('What GPU was used?',[source])==[source]


def test_cross_paper_budget_keeps_multiple_evidence_per_paper():
    candidates=[{'paper_id':p,'chunk_id':f'{p}:{i}'} for p in ('dominant','other','third') for i in range(10)]
    result=retrieval._preserve_coverage(candidates,['dominant','other','third'],9,False)
    assert [sum(c['paper_id']==p for c in result) for p in ('dominant','other','third')]==[3,3,3]


def test_context_terms_expand_without_hardcoded_answers():
    value=retrieval.expanded('最大模型参数量和上下文长度')
    assert 'sequence length' in value and 'parameter count' in value
    assert '512' not in value and '1024' not in value


def test_requested_context_facet_survives_reranking_of_parameter_tables():
    candidates=[{'paper_id':'p','chunk_id':str(i),'text':'Model parameter table'} for i in range(5)]
    context={'paper_id':'p','chunk_id':'ctx','text':'We trained on contiguous sequences of 512 tokens.'}
    result=retrieval._preserve_coverage(candidates+[context],['p'],3,False,'比较参数量和上下文长度')
    assert result[0]['chunk_id']=='ctx'


def test_unrequested_context_does_not_override_rank():
    candidates=[{'paper_id':'p','chunk_id':'ranked','text':'Model parameters 1B'},
                {'paper_id':'p','chunk_id':'ctx','text':'Context length is 2048.'}]
    assert retrieval._preserve_coverage(candidates,['p'],1,False,'参数量')[0]['chunk_id']=='ranked'
