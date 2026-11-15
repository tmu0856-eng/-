"""离线检查编号制与作者年份制参考条目提取。"""
import pytest

from backend import db, research


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    db.init()
    yield


def insert(pid, page, text):
    db.execute("INSERT INTO papers(id,title,status,page_count,created) VALUES(?,?,'ready',1,'2026-01-01')", (pid, pid))
    db.execute('INSERT INTO pages(paper_id,page,text) VALUES(?,?,?)', (pid, page, text))


def test_numbered_entries_split_and_keep_continuation(library):
    insert('num', 1, 'Body text before references.\n\nReferences\n[1] Ashish Vaswani et al. Attention is all you need.\nIn NIPS 2017.\n[2] Jacob Devlin. BERT.\n')
    entries = research.reference_entries('num')
    assert [e['label'] for e in entries] == ['1', '2']
    assert 'Attention is all you need' in entries[0]['text'] and 'In NIPS 2017' in entries[0]['text']


def test_author_year_paragraphs_split_into_entries(library):
    text = ('Introduction body.\n\nReferences\n'
            'Alan Akbik, Duncan Blythe, and Roland Vollgraf. 2018. Contextual string embeddings. '
            'In COLING, pages 1638-1649.\n\n'
            'Rami Al-Rfou, Dokook Choe, Noah Constant, and Mandy Guo. 2018. Character-level language modeling.\n\n'
            'Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. 2015. Neural machine translation by jointly learning.\n')
    insert('year', 1, text)
    entries = research.reference_entries('year')
    assert len(entries) == 3
    assert all(e['label'] == '' for e in entries)
    assert 'Contextual string embeddings' in entries[0]['text']
    assert 'Neural machine translation' in entries[2]['text']


def test_reference_heading_must_start_line(library):
    insert('skip', 1, 'We compare against related References in prior work [9].\nMore body text continues here without any bibliography marker.\n')
    assert research.reference_entries('skip') == []


def test_appendix_section_stops_collection(library):
    insert('app', 1, 'References\n[1] First cited work about optimizers.\n[2] Second cited work about attention.\n\n'
                     'Appendix\nA.1 Extra derivation that is not a reference entry at all.\n')
    entries = research.reference_entries('app')
    assert len(entries) == 2 and 'First cited work' in entries[0]['text']
    assert all('Extra derivation' not in e['text'] for e in entries)


def test_asterisk_bibliography_heading_matches(library):
    insert('bib', 1, 'Body text.\n\n*Bibliography\n\nChatfield, K., Simonyan, K., and Zisserman, A. (2014). '
                     'Return of the devil in the details.\n\nKrizhevsky, A., Sutskever, I., and Hinton, G. (2012). '
                     'ImageNet classification with deep convolutional networks.\n')
    entries = research.reference_entries('bib')
    assert len(entries) == 2 and 'devil in the details' in entries[0]['text']


def test_wrapped_doi_line_does_not_switch_to_numbered_style(library):
    insert('doi', 1, 'References\n\nSeo, M., Kembhavi, A., Farhadi, A., and Hajishirzi, H. 2017. Bidirectional '
                     'attention flow.\n10.18653/v1/D17-1254. URL https://doi.org/10.18653/v1/D17-1254.\n\n'
                     'Wang, S., and Jiang, J. 2016. Machine comprehension using match-lstm.\n')
    entries = research.reference_entries('doi')
    assert len(entries) == 2
    assert 'Bidirectional' in entries[0]['text'] and 'match-lstm' in entries[1]['text']


def test_ijcv_entries_split_at_author_starts_without_blank_lines(library):
    insert('ijcv', 1, 'Text before.\n\n*Bibliography\n\nChatfield, K., Simonyan, K., Vedaldi, A., and Zisserman, '
                     'A. 2014. Return of the devil in the details. CoRR, abs/1405.3531.\n'
                     'Chen, Q., Song, Z., and Wang, Y. 2015. Deep face recognition. CoRR, abs/1503.03832.\n')
    entries = research.reference_entries('ijcv')
    assert len(entries) == 2
    assert 'devil in the details' in entries[0]['text'] and 'face recognition' in entries[1]['text']
