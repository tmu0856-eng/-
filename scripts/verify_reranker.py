"""使用标注的合成证据比较本地精排前后的排序，不调用付费模型。"""
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ.update(RESEARCHDOCK_DATA=str(ROOT/'data/reranker-smoke'),EMBEDDING_PROVIDER='local',
                  EMBEDDING_MODEL='BAAI/bge-m3',RERANKER_MODEL='BAAI/bge-reranker-v2-m3',RERANKER_CANDIDATES='40')
sys.path.insert(0,str(ROOT))
from backend import db,embeddings,retrieval,reranker

texts={
    'hardware':'The Transformer models were trained on a single machine with eight NVIDIA P100 GPUs.',
    'optimizer':'We used the Adam optimizer with beta1 = 0.9, beta2 = 0.98 and epsilon = 1e-9.',
    'decoder_mask':'The decoder uses masked self-attention. A position cannot attend to subsequent positions, so predictions cannot see future tokens.',
    'encoder':'In encoder self-attention, each position can attend to all positions in the input sequence.',
    'positions':'The architecture has no recurrence. We add positional encodings to the input embeddings to provide the order of tokens.',
    'dropout':'We use residual dropout with a dropout rate of 0.1 for regularization.',
    'mlm':'BERT uses masked language modeling, predicting randomly masked input tokens from both left and right context.',
    'nsp':'BERT next sentence prediction determines whether sentence B is the actual next sentence after sentence A in the original document.',
    'finetune':'For fine-tuning, BERT can add one task-specific output layer to the pre-trained model.',
    'fruit':'Fresh apples, oranges and bananas are sold at the grocery store.'}
cases=[('这项研究采用了哪种运算装备','hardware'),('优化方法的动量衰减系数是多少','optimizer'),
       ('怎样避免生成当前词时偷看后面的内容','decoder_mask'),('模型用什么方式知道词语在序列中的先后','positions'),
       ('正则化时随机丢弃的比例是多少','dropout'),('怎样利用两边的上下文补全被遮住的词','mlm'),
       ('如何判断两个句子是不是原本相邻','nsp'),('编码部分能不能看见整段输入','encoder')]
db.init()
db.execute("DELETE FROM papers WHERE id='rerank-fixture'")
db.execute("INSERT INTO papers(id,title,status) VALUES('rerank-fixture','Synthetic ranking evidence','ready')")
for page,(key,text) in enumerate(texts.items(),1):
    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(key,'rerank-fixture',page,text,'[10,20,200,300]',None))
started=time.monotonic()
embeddings.index_pending()
reranker.score('warm-up',['A short warm-up passage.'])
report={'date':datetime.now(timezone.utc).isoformat(),'scope':'Eight labeled questions on ten synthetic passages; not a contest benchmark.',
        'cold_setup_seconds':round(time.monotonic()-started,2),'cases':[]}
for question,gold in cases:
    before_start=time.monotonic()
    before,first=retrieval.retrieve(question,['rerank-fixture'],40,use_reranker=False)
    before_ms=(time.monotonic()-before_start)*1000
    after_start=time.monotonic()
    after,second=retrieval.retrieve(question,['rerank-fixture'],8)
    after_ms=(time.monotonic()-after_start)*1000
    def rank(values):
        return next((i for i,s in enumerate(values,1) if s['chunk_id']==gold),None)
    report['cases'].append({'question':question,'gold':gold,'before_rank':rank(before),'after_rank':rank(after),
        'before_total_ms':round(before_ms,1),'after_total_ms':round(after_ms,1),'retrieval':second,
        'before':[s['chunk_id'] for s in before],
        'after':[{'chunk_id':s['chunk_id'],'source_id':s['source_id'],'rerank_score':s.get('rerank_score'),'page':s['page']} for s in after],
        'anchors_preserved':all(s['text']==texts[s['chunk_id']] and s['bbox']==[10,20,200,300] for s in after)})
original=os.environ.get('RERANKER_LOCAL_PATH')
try:
    os.environ['RERANKER_LOCAL_PATH']=str(ROOT/'.local/missing-reranker-fixture')
    values,info=retrieval.retrieve('Adam optimizer',['rerank-fixture'],3)
    report['fallback_check']={'passed':bool(values) and bool(info['reranker']['error']) and not info['reranker']['applied'],'retrieval':info}
finally:
    if original is None:os.environ.pop('RERANKER_LOCAL_PATH',None)
    else:os.environ['RERANKER_LOCAL_PATH']=original
n=len(cases)
report['summary']={'questions':n,
    'top1_before':sum(c['before_rank']==1 for c in report['cases']),
    'top1_after':sum(c['after_rank']==1 for c in report['cases']),
    'top5_before':sum(c['before_rank'] is not None and c['before_rank']<=5 for c in report['cases']),
    'top5_after':sum(c['after_rank'] is not None and c['after_rank']<=5 for c in report['cases']),
    'median_warm_rerank_ms':statistics.median(c['retrieval']['reranker']['elapsed_ms'] for c in report['cases'])}
report['model']=reranker.status()
report['passed']=(all(c['anchors_preserved'] and c['retrieval']['reranker']['applied'] for c in report['cases'])
    and report['fallback_check']['passed'] and report['summary']['top1_after']>=report['summary']['top1_before'])
(ROOT/'docs/RERANKER_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':report['passed'],'summary':report['summary'],'runtime':report['model']['runtime']}),flush=True)
if not report['passed']:raise SystemExit(1)
