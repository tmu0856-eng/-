"""SQLite 持久化存储，每次操作独立管理连接。"""
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('RESEARCHDOCK_DATA', str(ROOT / 'data'))).resolve()


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'researchdock.db', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init():
    for name in ('files', 'charts'):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS papers (
          id TEXT PRIMARY KEY, filename TEXT, title TEXT, authors TEXT,
          abstract TEXT, sha TEXT, version INTEGER, arxiv_id TEXT,
          page_count INTEGER, status TEXT, warning TEXT, created TEXT,
          metadata TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS pages (
          paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          page INTEGER, text TEXT, blocks TEXT, width REAL, height REAL,
          PRIMARY KEY(paper_id,page));
        CREATE TABLE IF NOT EXISTS chunks (
          id TEXT PRIMARY KEY, paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          page INTEGER, text TEXT, bbox TEXT, embedding TEXT);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, paper_id TEXT, status TEXT, done INTEGER DEFAULT 0,
          total INTEGER DEFAULT 0, message TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS chunk_vectors (
          chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
          space TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS vectors_space ON chunk_vectors(space);
        CREATE TABLE IF NOT EXISTS document_vectors (
          paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
          space TEXT NOT NULL, sample_sha TEXT NOT NULL, vector BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS reference_imports (
          id TEXT PRIMARY KEY, paper_id TEXT, status TEXT, done INTEGER, total INTEGER,
          results TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS feedback (arxiv_id TEXT PRIMARY KEY, value INTEGER);
        CREATE TABLE IF NOT EXISTS watches (
          id TEXT PRIMARY KEY, query TEXT, hours REAL, last_run TEXT,
          last_error TEXT, results TEXT DEFAULT '[]', enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS translations (
          paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          page INTEGER, model TEXT, content TEXT, PRIMARY KEY(paper_id,page,model));
        CREATE TABLE IF NOT EXISTS visual_pages (
          paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
          page INTEGER, model TEXT, content TEXT, PRIMARY KEY(paper_id,page,model));
        CREATE TABLE IF NOT EXISTS discovery_cache (
          query TEXT, result_count INTEGER, fetched_at TEXT, payload TEXT,
          PRIMARY KEY(query,result_count));
        ''')
        # 服务重启后，不把已停止的解析任务继续标为运行中。
        con.execute("UPDATE jobs SET status='failed', message='服务重启，任务已中断，请重新导入' WHERE status IN ('queued','running')")
        con.execute("UPDATE papers SET status='failed', warning='解析中断，请删除后重新导入' WHERE status='processing'")
        con.execute("UPDATE reference_imports SET status='failed',error='服务重启，任务已中断，可重新选择未导入条目' WHERE status IN ('queued','running')")


def rows(sql, args=()):
    with connect() as con:
        return [dict(row) for row in con.execute(sql, args).fetchall()]


def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None


def execute(sql, args=()):
    with connect() as con:
        con.execute(sql, args)


def encode(value):
    return json.dumps(value, ensure_ascii=False)
