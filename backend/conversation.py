"""根据上一轮引用文献确定追问范围，历史答案不作为原文证据。"""
import re

from . import db

FOLLOWUP = re.compile(r'^(?:其中|这篇|这两篇|这些|该论文|该模型|它|它们|上述|前面|刚才|上面|接着|继续|那么|那|为什么|还有|其他|具体|详细|再解释)|\b(?:this paper|these papers|what about|why is that|explain more)\b', re.I)
GENERIC = re.compile(r'^(?:为什么|还有呢|其他呢|具体呢|详细|再解释|继续|那么呢|那呢)|\b(?:why is that|explain more)\b', re.I)


def prepare(question, paper_ids, history):
    scope = list(paper_ids)
    result = {'used': False, 'paper_ids': scope, 'message': ''}
    if not history or not FOLLOWUP.search(question.strip()):
        return question, scope, result
    previous_answer = next((t for t in reversed(history) if t.role == 'assistant' and t.paper_ids), None)
    previous_question = next((t.content for t in reversed(history) if t.role == 'user'), '')
    if not previous_answer:
        return question, scope, result
    prior_ids = list(dict.fromkeys(previous_answer.paper_ids))
    if scope and not set(scope).intersection(prior_ids):
        # 用户明确切换文献范围时，以新范围为准。
        return question, scope, result
    if not scope:
        ready = {p['id'] for p in db.rows("SELECT id FROM papers WHERE status IN ('ready','partial')")}
        scope = [pid for pid in prior_ids if pid in ready]
        if not scope:
            raise ValueError('上一轮引用的论文已删除或尚未解析，请重新选择论文范围。')
    rewritten = question
    if GENERIC.search(question.strip()) and previous_question:
        rewritten = question + '\n\n用于明确本次追问主题的上一问题（不是原文证据）：' + previous_question[:600]
    result.update(used=True, paper_ids=scope,
                  message=f'承接上一轮引用的 {len(scope)} 篇论文；历史回答不作为事实证据，重新检索原文。')
    return rewritten, scope, result
