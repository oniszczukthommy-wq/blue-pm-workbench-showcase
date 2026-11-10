from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class Problem(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workbench.sqlite3"
        with self.tx() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workspace_trash (
                    type TEXT NOT NULL, item_id TEXT NOT NULL, project_id TEXT,
                    title TEXT NOT NULL, deleted_at TEXT NOT NULL,
                    PRIMARY KEY(type,item_id)
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, idea TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
                    metadata TEXT NOT NULL, prd_id TEXT, created_at TEXT NOT NULL,
                    UNIQUE(project_id,kind,version)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    result TEXT, error TEXT, usage TEXT NOT NULL DEFAULT '{}',
                    retry_of TEXT, cache_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(project_id)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                    type TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    document_id TEXT NOT NULL REFERENCES documents(id), title TEXT NOT NULL,
                    description TEXT NOT NULL, acceptance TEXT NOT NULL, priority TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'todo', position INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                PRAGMA user_version=1;
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        for key in ("metadata", "payload", "result", "usage"):
            if key in value and value[key] is not None:
                value[key] = json.loads(value[key])
        return value

    def require_project(self, db, project_id):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None or self.trashed(db, "project", project_id):
            raise Problem(404, "PROJECT_NOT_FOUND", "找不到这个产品")
        return dict(row)

    def idle(self, db, project_id):
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                      (project_id,)).fetchone():
            raise Problem(409, "PROJECT_BUSY", "这个产品有任务正在执行，请结束后再操作")

    def latest(self, db, project_id, kind):
        doc = self.decode(db.execute(
            "SELECT * FROM documents WHERE project_id=? AND kind=? ORDER BY version DESC LIMIT 1",
            (project_id, kind)).fetchone())
        return None if doc and self.trashed(db, "document", doc["id"]) else doc

    @staticmethod
    def trashed(db, kind, item_id):
        return bool(db.execute("SELECT 1 FROM workspace_trash WHERE type=? AND item_id=?", (kind, item_id)).fetchone())

    def add_document(self, db, project_id, kind, content, metadata=None, prd_id=None):
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE project_id=? AND kind=?",
                             (project_id, kind)).fetchone()[0]
        document = dict(id=uid(), project_id=project_id, kind=kind,
                        version=version,
                        content=content, metadata=metadata or {}, prd_id=prd_id, created_at=now())
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)", (
            document["id"], project_id, kind, document["version"], content,
            json.dumps(document["metadata"], ensure_ascii=False), prd_id, document["created_at"]
        ))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id))
        return document
