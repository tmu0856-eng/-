"""根据上一轮引用文献确定追问范围，历史答案不作为原文证据。"""
import re

from . import db

FOLLOWUP = re.compile(r'^(?:其中|这篇|这两篇|这些|该论文|该模型|它|它们|上述|前面|刚才|上面|接着|继续|那么|那|为什么|还有|其他|具体|详细|再解释|epsilon\b|eps\b|[εϵβ])|\b(?:this paper|these papers|what about|why is that|explain more)\b', re.I)
GENERIC = re.compile(r'^(?:为什么|还有呢|其他呢|具体呢|详细|再解释|继续|那么呢|那呢)|\b(?:why is that|explain more)\b', re.I)
PARAMETER = re.compile(r'epsilon|eps\b|[εϵ]|beta[_\s]?[12]|β[₁₂12]|学习率|预热|动量|参数', re.I)
OBJECTS = {
    'Adam 优化器': re.compile(r'\bAdam\b', re.I),
    'AdamW 优化器': re.compile(r'\bAdamW\b', re.I),
    'SGD 优化器': re.compile(r'\bSGD\b', re.I),
    'RMSprop 优化器': re.compile(r'\bRMSprop\b', re.I),
    '标签平滑': re.compile(r'label\s*smoothing|标签平滑', re.I),
    'LayerNorm': re.compile(r'layer\s*norm(?:alization)?|层归一化', re.I),
}


def _parameter_objects(question, history):
    """只从用户问题提取参数所属对象，不从历史答案继承数值或结论。"""
    if not PARAMETER.search(question):
        return []
    if any(pattern.search(question) for pattern in OBJECTS.values()):
        return []
    for turn in reversed(history):
        if turn.role != 'user':
            continue
        objects = [name for name, pattern in OBJECTS.items() if pattern.search(turn.content)]
        if objects:
            if re.search(r'优化器|optimizer', question, re.I):
                objects = [name for name in objects if '优化器' in name]
            return objects
        if not FOLLOWUP.search(turn.content.strip()):
            break
    return []


def prepare(question, paper_ids, history):
    scope = list(paper_ids)
    result = {'used': False, 'paper_ids': scope, 'message': ''}
    previous_question = next((t.content for t in reversed(history) if t.role == 'user'), '')
    # 澄清后只回复“Adam”等短对象名时，承接待明确的问题并保留文献范围。
    if (history and len(question)<60 and any(p.search(question) for p in OBJECTS.values())
            and not PARAMETER.search(question) and PARAMETER.search(previous_question)
            and FOLLOWUP.search(previous_question.strip())):
        question=previous_question+'\n\n本次用户明确参数对象：'+question
    if not history or not FOLLOWUP.search(question.strip()):
        return question, scope, result
    previous_answer = next((t for t in reversed(history) if t.role == 'assistant' and t.paper_ids), None)
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
    objects = _parameter_objects(question, history)
    if len(objects) > 1:
        result.update(used=True, paper_ids=scope, needs_clarification=True,
                      clarification='这次追问的参数属于哪个对象：' + '、'.join(objects) + '？请写明对象后再查询。',
                      message='上一问题涉及多个参数对象，需要先确认，尚未调用模型。')
        return question, scope, result
    if (not objects and re.search(r'epsilon|eps\b|[εϵ]',question,re.I)
            and not any(p.search(question) for p in OBJECTS.values())):
        result.update(used=True,paper_ids=scope,needs_clarification=True,
                      clarification='你指的是哪个对象的 epsilon，例如 Adam 优化器、标签平滑或 LayerNorm？请写明对象后再查询。',
                      message='目前无法确定参数所属对象，尚未调用模型。')
        return question,scope,result
    if objects:
        rewritten = question + '\n\n本次参数所属对象：' + objects[0] + '。此信息仅用于明确追问主题，参数值必须重新查原文。'
    elif GENERIC.search(question.strip()) and previous_question:
        rewritten = question + '\n\n用于明确本次追问主题的上一问题（不是原文证据）：' + previous_question[:600]
    result.update(used=True, paper_ids=scope,
                  message=f'承接上一轮引用的 {len(scope)} 篇论文；历史回答不作为事实证据，重新检索原文。')
    if objects:
        result['resolved_object'] = objects[0]
        result['message'] += ' 参数对象已明确为' + objects[0] + '。'
    return rewritten, scope, result
