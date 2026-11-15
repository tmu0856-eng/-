"""使用隔离的合成证据检查真实本地 BGE-M3，不调用付费接口。"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
os.environ['RESEARCHDOCK_DATA'] = str(ROOT / 'data/bge-m3-smoke')
os.environ['EMBEDDING_PROVIDER'] = 'local'
os.environ['EMBEDDING_MODEL'] = 'BAAI/bge-m3'
sys.path.insert(0, str(ROOT))
from backend import db, embeddings, retrieval

db.init()
db.execute("DELETE FROM papers WHERE id='bge-smoke'")
db.execute("INSERT INTO papers(id,title,status) VALUES('bge-smoke','Synthetic embedding fixtures','ready')")
texts = [('hardware', 'The model was trained on a machine with eight NVIDIA P100 GPUs.'),
         ('optimizer', 'We used the Adam optimizer with beta1 = 0.9, beta2 = 0.98 and epsilon = 1e-9.'),
         ('distractor', 'Fresh apples, oranges and bananas are sold at the grocery store.')]
for index, (key, text) in enumerate(texts, 1):
    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (key, 'bge-smoke', index, text, '[0,0,100,100]', None))
start = time.monotonic()
embeddings.index_pending()
report = {'date': datetime.now(timezone.utc).isoformat(), 'scope': 'Real local BGE-M3; synthetic evidence, not a contest accuracy benchmark.',
          'space': embeddings.space_id(), 'runtime': dict(embeddings._RUNTIME), 'checks': []}
for question, target in [('这项研究采用了哪种计算设备', 'hardware'), ('优化方法的动量衰减系数是多少', 'optimizer')]:
    sources, info = retrieval.retrieve(question, ['bge-smoke'], 3)
    query_vector = np.asarray(embeddings.encode([question])[0])
    raw = []
    for row in db.rows('SELECT chunk_id,vector FROM chunk_vectors'):
        raw.append({'chunk_id':row['chunk_id'], 'cosine':float(np.frombuffer(row['vector'], dtype='<f4') @ query_vector)})
    raw.sort(key=lambda row: -row['cosine'])
    overlap = sorted(set(retrieval.tokens(retrieval.expanded(question))) & set(retrieval.tokens(' '.join(text for _, text in texts))))
    report['checks'].append({'question': question, 'expected_top_chunk': target,
                             'passed': bool(sources) and sources[0]['chunk_id'] == target and 'BAAI/bge-m3' in info['mode'],
                             'retrieval': info, 'sources': sources, 'dense_ranking':raw, 'lexical_overlap':overlap})
vectors = [np.frombuffer(row['vector'], dtype='<f4') for row in db.rows('SELECT vector FROM chunk_vectors')]
report['vector_check'] = {'count': len(vectors), 'dimensions': [len(v) for v in vectors], 'norms': [float(np.linalg.norm(v)) for v in vectors]}
report['seconds'] = round(time.monotonic()-start, 2)
report['passed'] = all(c['passed'] for c in report['checks']) and len(vectors) == 3 and all(len(v) == 1024 for v in vectors)
(ROOT / 'docs/BGE_M3_CHECK.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'passed': report['passed'], 'seconds': report['seconds'], 'runtime': report['runtime']}), flush=True)
if not report['passed']:
    sys.exit(1)
