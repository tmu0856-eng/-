"""使用当前解析器重新处理已保存的 PDF，更新页文本和分块。重复运行结果一致，不生成报告，进度可从任务表查看。"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import db, documents


def main():
    papers = db.rows('SELECT id,title FROM papers')
    for i, p in enumerate(papers):
        pdf = db.DATA / 'files' / (p['id'] + '.pdf')
        if not pdf.exists():
            print('skip (no file):', p['title'][:50])
            continue
        with db.connect() as con:
            con.execute('DELETE FROM chunks WHERE paper_id=?', (p['id'],))
            con.execute('DELETE FROM pages WHERE paper_id=?', (p['id'],))
        jid = uuid.uuid4().hex
        db.execute('INSERT INTO jobs(id,paper_id,status,total,message,created) VALUES(?,?,?,?,?,?)',
                   (jid, p['id'], 'queued', 0, '重新解析', documents.now()))
        try:
            documents.parse(p['id'], jid)
            print(f'[{i + 1}/{len(papers)}] ok:', p['title'][:50])
        except Exception as exc:
            print(f'[{i + 1}/{len(papers)}] FAILED:', p['title'][:50], type(exc).__name__, str(exc)[:120])


if __name__ == '__main__':
    main()
