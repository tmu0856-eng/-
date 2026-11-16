"""检查未来方向抽取与证据约束，不调用付费模型。"""
import pytest

from backend import db, llm, research

STATEMENT = 'Future work includes improving the robustness of Adam under noisy gradients and sparse data.'
OTHER_SENTENCES = [
    'Future work explores extending this design to federated settings across many devices.',
    'Limitation: the current study leaves open question of scaling beyond eight GPUs.',
    'Future research should revisit the memory overhead of the proposed optimizer.',
    'Training details are omitted here for brevity and discussed in the appendix.',
]
VERBATIM = 'improving the robustness of Adam under noisy gradients and sparse data'


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(llm, 'configured', lambda: False)
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_MODEL'):
        monkeypatch.delenv(key, raising=False)
    db.init()
    db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES('alpha','Alpha paper','ready',1,'2026-01-01')")
    db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES('beta','Beta paper','ready',1,'2026-01-02')")
    db.execute("UPDATE papers SET metadata=? WHERE id='alpha'", ('{"publication":{"date":"2024-01-01","source":"fixture"}}',))
    db.execute("UPDATE papers SET metadata=? WHERE id='beta'", ('{"publication":{"date":"2025-01-01","source":"fixture"}}',))
    db.execute('INSERT INTO pages(paper_id,page,text) VALUES(?,?,?)', ('alpha' , 1, ' '.join([STATEMENT] + OTHER_SENTENCES)))
    db.execute('INSERT INTO pages(paper_id,page,text) VALUES(?,?,?)', ('beta', 1, 'Our robust variant solves Adam under noisy gradients.'))
    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', ('alpha-1', 'alpha', 1, STATEMENT, '[]', None))
    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
               ('beta-1', 'beta', 1, 'Our robust variant works by ' + VERBATIM + ' in practice.', '[]', None))
    yield


def test_extracts_statements_with_candidates_capped_per_paper(library):
    report = research.future_work_report(['alpha'])
    items = report['items']
    assert 1 <= len(items) <= 3
    assert all(i['paper_id'] == 'alpha' and 20 <= len(i['statement']) <= 500 for i in items)
    assert items[0]['statement'] == STATEMENT
    assert items[0]['candidates'][0]['paper_id'] == 'beta'
    assert items[0]['candidates'][0]['score'] > .15
    assert VERBATIM in items[0]['candidates'][0]['text']
    assert items[0]['verdict'] == '待核实'


def test_sentence_without_marker_is_not_extracted(library):
    report = research.future_work_report(['alpha'])
    assert all('Training details' not in i['statement'] for i in report['items'])


def test_no_candidates_reported_as_not_found(library):
    db.execute("DELETE FROM chunks WHERE paper_id='beta'")
    report = research.future_work_report(['alpha'])
    assert report['items'][0]['verdict'] == '未发现相关后续文献'
    assert research.judge_future_work(STATEMENT, [])['verdict'] == '未发现相关后续文献'


def test_valid_verbatim_quote_resolves(library, monkeypatch):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: {'verdict': '已解决', 'ref': 'E1', 'quote': VERBATIM})
    value = research.judge_future_work(STATEMENT, [{'is_later':True, 'paper_id': 'beta', 'title': 'Beta', 'page': 1, 'text': 'x ' + VERBATIM}])
    assert value['verdict'] == '已解决' and value['ref'] == 'E1' and value['quote'] == VERBATIM


@pytest.mark.parametrize('value', [
    {'verdict': '已解决', 'ref': 'E1', 'quote': 'fabricated sentence never written anywhere'},
    {'verdict': '已解决', 'ref': 'E9', 'quote': VERBATIM},
    {'verdict': '部分解决', 'ref': '', 'quote': VERBATIM},
    {'verdict': '已解决', 'ref': 'E1', 'quote': ''},
])
def test_unverifiable_resolution_is_downgraded(library, monkeypatch, value):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: value)
    result = research.judge_future_work(STATEMENT, [{'is_later':True, 'paper_id': 'beta', 'title': 'Beta', 'page': 1, 'text': 'x ' + VERBATIM}])
    assert result['verdict'] == '待核实' and result['reason'] and result['quote'] == ''


def test_model_outage_keeps_unverified(library, monkeypatch):
    def fail(*a, **kw):
        raise llm.ModelError('judge down')
    monkeypatch.setattr(llm, 'complete', fail)
    result = research.judge_future_work(STATEMENT, [{'is_later':True, 'paper_id': 'beta', 'title': 'Beta', 'page': 1, 'text': VERBATIM}])
    assert result['verdict'] == '待核实' and '模型不可用' in result['reason']


def test_invalid_verdict_format_keeps_unverified(library, monkeypatch):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: {'verdict': '已处理', 'ref': 'E1', 'quote': VERBATIM})
    result = research.judge_future_work(STATEMENT, [{'is_later':True, 'paper_id': 'beta', 'title': 'Beta', 'page': 1, 'text': VERBATIM}])
    assert result['verdict'] == '待核实' and '格式异常' in result['reason']


def test_report_judge_only_runs_when_model_configured(library, monkeypatch):
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: pytest.fail('model unconfigured, judge must be skipped'))
    report = research.future_work_report(['alpha'], judge=True)
    assert report['items'][0]['verdict'] == '待核实' and '逐字核对' in report['note']


def test_report_judge_applies_valid_verdict(library, monkeypatch):
    monkeypatch.setattr(llm, 'configured', lambda: True)
    monkeypatch.setattr(llm, 'complete', lambda *a, **kw: {'verdict': '已解决', 'ref': 'E1', 'quote': VERBATIM})
    report = research.future_work_report(['alpha'], judge=True)
    item = report['items'][0]
    assert item['verdict'] == '已解决' and item['quote'] == VERBATIM


def test_survey_writing_carries_judged_future_work(library, monkeypatch):
    monkeypatch.setattr(llm, 'configured', lambda: True)
    payloads = []

    def fake_complete(system, user, *args, **kw):
        payloads.append(user)
        if args and args[0]:
            return {'verdict': '已解决', 'ref': 'E1', 'quote': VERBATIM}
        return '综述内容 [P:alpha]'
    monkeypatch.setattr(llm, 'complete', fake_complete)
    result = research.writing('survey', 'Adam 优化器', ['alpha'])
    future = result['future_work']
    assert future['items'][0]['verdict'] == '已解决' and '逐字核对' in future['note']
    assert any('future_work' in p for p in payloads)
    assert result['content'].startswith('综述内容 [P:alpha]')
    assert result['references'][0]['title']=='Alpha paper'


def test_earlier_paper_is_not_a_later_solution(library):
    db.execute("UPDATE papers SET metadata=? WHERE id='beta'", ('{"publication":{"date":"2023-01-01","source":"fixture"}}',))
    report=research.future_work_report(['alpha'])
    assert not report['items'][0]['candidates']


def test_unknown_date_cannot_support_resolved_verdict(library,monkeypatch):
    monkeypatch.setattr(llm,'complete',lambda *a,**kw:{'verdict':'已解决','ref':'E1','quote':VERBATIM})
    candidate={'paper_id':'beta','title':'Beta','page':1,'text':VERBATIM,'is_later':False}
    value=research.judge_future_work(STATEMENT,[candidate])
    assert value['verdict']=='待核实' and '发表时间' in value['reason']


def test_same_year_without_dates_is_not_proven_later(library):
    for pid in ('alpha','beta'):
        db.execute('UPDATE papers SET metadata=? WHERE id=?', ('{"publication":{"date":"2025","source":"fixture"}}',pid))
    candidates=research.future_work_report(['alpha'])['items'][0]['candidates']
    assert candidates and not candidates[0]['is_later']


def test_import_timestamp_never_becomes_publication_date(library):
    db.execute("UPDATE papers SET metadata='{}',arxiv_id='' WHERE id='alpha'")
    row=db.one("SELECT * FROM papers WHERE id='alpha'")
    assert research._publication_bounds(row)==('','',{})


def test_model_bibliography_is_rebuilt_from_real_metadata(library,monkeypatch):
    monkeypatch.setattr(llm,'configured',lambda:True)
    monkeypatch.setattr(llm,'complete',lambda *a,**kw:'Abstract\nAn outline.\n## References\nInvented Person. 2099. Imaginary citation.')
    value=research.writing('outline','A research idea',['alpha'])
    assert 'Invented Person' not in value['content'] and 'Alpha paper' in value['content']
    assert value['references'][0]['year']=='2024'


def test_latest_search_filters_older_metadata_and_never_judges_abstract(library,monkeypatch):
    calls=[]
    def search(query,count,**options):
        assert options['sort']=='newest'
        calls.append(query)
        return [{'id':'2501.00001','title':'Later Work','published':'2025-01-02T00:00:00Z','summary':'We study this topic.','url':'https://arxiv.org/abs/2501.00001'},
                {'id':'2301.00001','title':'Earlier Work','published':'2023-01-02T00:00:00Z','summary':'Earlier topic.','url':'https://arxiv.org/abs/2301.00001'}]
    monkeypatch.setattr(research,'arxiv_search',search)
    report=research.future_work_report(['alpha'],discover_latest=True)
    assert calls and 'submittedDate' in calls[0]
    item=report['items'][0]
    assert item['verdict']=='待核实'
    assert len(item['latest_search']['candidates'])==1
    assert item['latest_search']['candidates'][0]['abstract_only']


def test_latest_search_outage_is_explicit(library,monkeypatch):
    def fail(*args,**kwargs):raise research.DiscoveryError('fixture network unavailable')
    monkeypatch.setattr(research,'arxiv_search',fail)
    value=research.discover_later_work(STATEMENT,'alpha')
    assert value['error'] and not value['candidates']
