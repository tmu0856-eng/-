"""向空且已停止的演示库复制已验收公开资料。使用 SQLite 备份接口，不复制活动 WAL 文件，不读取主库或覆盖已有评审数据。"""
import shutil
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/system-test-20261115-final'
TARGET=ROOT/'data/demo-public'


def main():
    import socket
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(('127.0.0.1',8767))==0:
            raise SystemExit('Stop the demo service on 8767 before seeding; nothing copied.')
    if not (SOURCE/'researchdock.db').exists():
        raise SystemExit('Validated material library does not exist; import public PDFs through the UI instead.')
    TARGET.mkdir(parents=True,exist_ok=True)
    if (TARGET/'researchdock.db').exists():
        with sqlite3.connect(TARGET/'researchdock.db') as c:
            if c.execute('SELECT COUNT(*) FROM papers').fetchone()[0]:
                raise SystemExit('Reviewer library is not empty; nothing overwritten.')
    with sqlite3.connect(SOURCE/'researchdock.db') as source:
        papers=source.execute('SELECT id,status FROM papers').fetchall()
        if len(papers)!=39 or any(p[1] not in ('ready','partial') for p in papers):
            raise SystemExit('Source is not the completed 39-paper material library.')
        if source.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
            raise SystemExit('Source still has active parsing jobs; wait before seeding.')
        files=[SOURCE/'files'/(pid+'.pdf') for pid,_ in papers]
        if any(not file.is_file() for file in files):
            raise SystemExit('A source PDF is missing; nothing copied.')
        (TARGET/'files').mkdir(exist_ok=True)
        for file in files:
            shutil.copy2(file,TARGET/'files'/file.name)
        if (SOURCE/'charts').exists():
            shutil.copytree(SOURCE/'charts',TARGET/'charts',dirs_exist_ok=True)
        with sqlite3.connect(TARGET/'researchdock.db') as destination:
            source.backup(destination)
    print('Seeded 39 validated material PDFs into data/demo-public. Main library unchanged.')


if __name__=='__main__':
    main()
