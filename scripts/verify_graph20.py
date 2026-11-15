"""通过实际导入流程下载 20 篇相互引用的 arXiv 论文并核验图谱。结果写入 docs/GRAPH20_CHECK.json，不调用生成模型。"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import db, documents, research

PAPER_IDS = [
    '1706.03762',  # Attention Is All You Need
    '1810.04805',  # BERT
    '1907.11692',  # RoBERTa
    '2005.14165',  # Language Models are Few-Shot Learners
    '1910.10683',  # Exploring the Limits of Transfer Learning (T5)
    '1412.6980',   # Adam
    '1512.03385',  # Deep Residual Learning
    '1607.06450',  # Layer Normalization
    '1802.05365',  # Deep contextualized word representations
    '1801.06146',  # Universal Language Model Fine-tuning
    '1901.02860',  # Transformer-XL
    '1906.08237',  # XLNet
    '1909.11942',  # ALBERT
    '2003.10555',  # ELECTRA
    '2002.08505',  # REALM
    '2103.00020',  # Learning Transferable Visual Models (CLIP)
    '1707.06347',  # Proximal Policy Optimization
    '1409.0575',   # Convolutional Neural Networks for Sentence Classification
    '1606.05250',  # SQuAD
    '1804.07461',  # GLUE
]
REPORT = Path(__file__).resolve().parent.parent / 'docs' / 'GRAPH20_CHECK.json'


def wait_jobs(timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not db.one("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")['n']:
            return True
        time.sleep(5)
    return False


def main():
    have = db.rows("SELECT id,arxiv_id,title,status FROM papers")
    by_arxiv = {}
    for row in have:
        if row['arxiv_id']:
            by_arxiv[row['arxiv_id'].split('v')[0]] = row
    imported, reused, failed = [], [], []
    for pid in PAPER_IDS:
        if pid in by_arxiv:
            reused.append(by_arxiv[pid]['id'])
            continue
        try:
            raw = research.download_arxiv(pid)
            info = documents.preview(raw)
            result = documents.enqueue(raw, pid + '.pdf', info)
            imported.append({'arxiv_id': pid, 'paper_id': result['paper_id'], 'title': info['title']})
            time.sleep(3)
        except Exception as exc:
            failed.append({'arxiv_id': pid, 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
    jobs_done = wait_jobs()
    graph = research.citation_graph()
    wanted = {p['arxiv_id'].split('v')[0]: p['id'] for p in graph['nodes'] if p['arxiv_id']}
    wanted_ids = set(wanted.values()) | set(reused)
    edges = [e for e in graph['edges']]
    core_edges = [e for e in edges if e['source'] in wanted_ids and e['target'] in wanted_ids]
    per_paper = []
    detail = {r['id']: r for r in db.rows('SELECT id,status,page_count FROM papers')}
    for node in graph['nodes']:
        if node['id'] not in wanted_ids:
            continue
        meta = detail.get(node['id'], {})
        per_paper.append({'paper_id': node['id'], 'arxiv_id': node['arxiv_id'], 'title': node['title'],
                          'status': meta.get('status'), 'pages': meta.get('page_count'),
                          'references_parsed': len(node['references']),
                          'in_degree': node['in_degree'], 'out_degree': node['out_degree'],
                          'rank': node['rank']})
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'goal': '20 篇真实 arXiv 文献导入后，引用图谱应基于原文 References 生成可核对连边。',
        'method': 'arXiv 官方 PDF 下载 → 与上传一致的解析入库 → reference_entries 抽取 → 规范化标题在参考文献文本中匹配成边；每条边保留原文页码与证据文本。',
        'selected_ids': PAPER_IDS,
        'downloads': {'imported': imported, 'reused_existing': reused, 'failed': failed},
        'parse_jobs_finished': jobs_done,
        'library_nodes_total': len(graph['nodes']),
        'core_papers': per_paper,
        'stats': {
            'core_papers_count': len(per_paper),
            'edges_total_in_library': len(edges),
            'edges_among_core_20': len(core_edges),
            'core_papers_with_incoming_edge': len({e['target'] for e in core_edges}),
            'core_papers_with_outgoing_edge': len({e['source'] for e in core_edges}),
        },
        'edges_among_core_20': core_edges,
        'note': graph['note'],
        'honesty': [
            '连边仅当引用方 References 中出现被引用论文的规范化标题；漏配不等于没有引用关系。',
            '标题抽取依赖 PDF 首页排版，个别论文标题可能不完整，从而漏边。',
            '图中其余节点为库中原有文献，不属于本次 20 篇验收范围。',
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['stats'], ensure_ascii=False))
    print('downloads failed:', failed)
    print('report:', REPORT)


if __name__ == '__main__':
    main()
