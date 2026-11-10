"""Local search, recoverable removal and immutable document change review.

Simin Lab's MIT proposal-before-write and recoverable-trash design is adapted to
our SQLite/versioned document model. No filesystem write tools are enabled.
"""
from __future__ import annotations

import difflib
import json
from typing import Literal

from fastapi import Query, Request
from pydantic import Field

from backend.models import Input
from backend.store import Problem, now


class TrashInput(Input):
    type: Literal['project', 'document', 'note', 'conversation']
    item_id: str = Field(min_length=1, max_length=80)


class DecisionInput(Input):
    allow: bool


def visible(store, db, kind, identity, pid=None):
    return not store.trashed(db, kind, identity) and not (pid and store.trashed(db, 'project', pid))


def proposal_value(row):
    item = dict(row)
    item['diff'] = list(difflib.unified_diff(item['before'].splitlines(), item['after'].splitlines(),
                                           fromfile='修改前', tofile='修改后', lineterm=''))
    return item


def initialize(store):
    with store.tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS document_proposals (
            id TEXT PRIMARY KEY, message_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
            project_id TEXT NOT NULL, document_id TEXT NOT NULL, kind TEXT NOT NULL,
            before TEXT NOT NULL, after TEXT NOT NULL, reason TEXT NOT NULL,
            base_prd_id TEXT, status TEXT NOT NULL, result_id TEXT, created_at TEXT NOT NULL
        )''')


def remove(store, body):
    with store.tx() as db:
        tables = {'project': 'projects', 'document': 'documents', 'note': 'assistant_notes', 'conversation': 'assistant_conversations'}
        row = db.execute(f'SELECT * FROM {tables[body.type]} WHERE id=?', (body.item_id,)).fetchone()
        if not row:
            raise Problem(404, 'ITEM_NOT_FOUND', '找不到这个项目')
        if store.trashed(db, body.type, body.item_id):
            return {'status': 'trashed'}
        pid = row['id'] if body.type == 'project' else row['project_id']
        if pid:
            store.require_project(db, pid)
            store.idle(db, pid)
        active = db.execute("SELECT 1 FROM assistant_messages m JOIN assistant_conversations c ON c.id=m.conversation_id "
                            "WHERE m.status IN ('queued','running') AND (c.id=? OR c.project_id=?)",
                            (body.item_id if body.type == 'conversation' else '', pid or '')).fetchone()
        if body.type == 'conversation':
            active = active or db.execute(
                "SELECT 1 FROM assistant_actions a JOIN assistant_messages m ON m.id=a.message_id "
                "LEFT JOIN jobs j ON j.id=json_extract(a.result,'$.job_id') "
                "WHERE m.conversation_id=? AND (a.status='executing' OR j.status IN ('queued','running')) LIMIT 1",
                (body.item_id,)).fetchone()
        if active:
            raise Problem(409, 'CHAT_BUSY', '相关产品仍在回复，请等待完成后再移入回收站')
        if body.type == 'document':
            # References and historical versions remain recoverable; don't break generated dependencies.
            dependency = db.execute("SELECT 1 FROM documents WHERE id<>? AND (prd_id=? OR json_extract(metadata,'$.plan_id')=? "
                                    "OR json_extract(metadata,'$.draft_id')=?) UNION ALL "
                                    "SELECT 1 FROM tasks WHERE document_id=? LIMIT 1", (row['id'],)*5).fetchone()
            if dependency:
                raise Problem(409, 'DOCUMENT_IN_USE', '这份文档关联着其他版本或任务，请保留它，或将整个产品移入回收站')
        title = row['name'] if body.type == 'project' else row['title'] if body.type == 'conversation' else row['content'].splitlines()[0].lstrip('# ')[:100]
        db.execute('INSERT INTO workspace_trash VALUES(?,?,?,?,?)', (body.type, body.item_id, pid, title, now()))
        return {'status': 'trashed'}


def search(store, phrase, project_id=None):
    with store.tx() as db:
        if project_id:
            store.require_project(db, project_id)
        needle = phrase.casefold().strip()
        if not needle:
            return {'items': [], 'has_more': False}
        hits = []
        def add(item_type, identity, pid, title, content, **extra):
            if project_id and pid != project_id or not visible(store, db, item_type, identity, pid):
                return
            text = title + '\n' + content
            offset = text.casefold().find(needle)
            if offset < 0:
                return
            start = max(0, offset - 60)
            hits.append(dict(type=item_type, id=identity, project_id=pid, title=title,
                             snippet=('…' if start else '') + text[start:offset+len(needle)+120], **extra))
        for p in db.execute('SELECT * FROM projects ORDER BY updated_at DESC'):
            add('project', p['id'], p['id'], p['name'], p['idea'])
        for d in db.execute('SELECT * FROM documents ORDER BY created_at DESC'):
            add('document', d['id'], d['project_id'], d['content'].splitlines()[0].lstrip('# ')[:100], d['content'], kind=d['kind'], version=d['version'])
        for n in db.execute('SELECT * FROM assistant_notes ORDER BY created_at DESC'):
            add('note', n['id'], n['project_id'], n['content'].splitlines()[0].lstrip('# ')[:100], n['content'])
        for m in db.execute("SELECT m.*,c.title,c.project_id FROM assistant_messages m JOIN assistant_conversations c ON c.id=m.conversation_id WHERE m.status='done' ORDER BY m.rowid DESC"):
            if not store.trashed(db, 'conversation', m['conversation_id']):
                add('conversation', m['conversation_id'], m['project_id'], m['title'], m['content'], message_id=m['id'])
        for t in db.execute('SELECT * FROM tasks ORDER BY updated_at DESC'):
            if not store.trashed(db, 'document', t['document_id']):
                add('task', t['id'], t['project_id'], t['title'], t['description']+'\n'+t['acceptance'])
        # One hit per matching message is useful for navigating the original paragraph.
        return {'items': hits[:60], 'has_more': len(hits) > 60}


def routes(app):
    @app.get('/api/search', tags=['全文搜索'])
    def full_search(request: Request, q: str = Query('', max_length=120), project_id: str | None = None):
        return search(request.app.state.store, q, project_id)

    @app.get('/api/trash', tags=['回收站'])
    def trash(request: Request):
        with request.app.state.store.tx() as db:
            return {'items': [dict(row) for row in db.execute('SELECT * FROM workspace_trash ORDER BY deleted_at DESC')]}

    @app.post('/api/trash', tags=['回收站'])
    def trash_item(body: TrashInput, request: Request):
        return remove(request.app.state.store, body)

    @app.post('/api/trash/restore', tags=['回收站'])
    def restore(body: TrashInput, request: Request):
        store = request.app.state.store
        with store.tx() as db:
            row = db.execute('SELECT * FROM workspace_trash WHERE type=? AND item_id=?', (body.type, body.item_id)).fetchone()
            if row and row['project_id'] and body.type != 'project':
                store.require_project(db, row['project_id'])
            db.execute('DELETE FROM workspace_trash WHERE type=? AND item_id=?', (body.type, body.item_id))
        return {'status': 'restored'}

    @app.get('/api/notes/{note_id}', tags=['文档库'])
    def get_note(note_id: str, request: Request):
        from backend.assistant import note_value
        store = request.app.state.store
        with store.tx() as db:
            row = db.execute('SELECT * FROM assistant_notes WHERE id=?', (note_id,)).fetchone()
            if not row or not visible(store, db, 'note', note_id, row['project_id']):
                raise Problem(404, 'NOTE_NOT_FOUND', '找不到这条讨论结论，请检查回收站')
            return note_value(row)

    @app.get('/api/proposals/{proposal_id}', tags=['修改审阅'])
    def get_proposal(proposal_id: str, request: Request):
        store = request.app.state.store
        with store.tx() as db:
            row = db.execute('SELECT * FROM document_proposals WHERE id=?', (proposal_id,)).fetchone()
            if not row or not visible(store, db, 'conversation', row['conversation_id'], row['project_id']):
                raise Problem(404, 'PROPOSAL_NOT_FOUND', '找不到这份修改建议')
            return proposal_value(row)

    @app.post('/api/proposals/{proposal_id}/decide', tags=['修改审阅'])
    def decide(proposal_id: str, body: DecisionInput, request: Request):
        store = request.app.state.store
        with store.tx() as db:
            row = db.execute('SELECT * FROM document_proposals WHERE id=?', (proposal_id,)).fetchone()
            if not row or not visible(store, db, 'conversation', row['conversation_id'], row['project_id']):
                raise Problem(404, 'PROPOSAL_NOT_FOUND', '找不到这份修改建议')
            if row['status'] != 'pending':
                if (body.allow and row['status']=='saved') or (not body.allow and row['status']=='rejected'):
                    return proposal_value(row)
                raise Problem(409, 'PROPOSAL_DECIDED', '这份建议已处理')
            result_id = None
            if body.allow:
                store.require_project(db, row['project_id'])
                store.idle(db, row['project_id'])
                latest = store.latest(db, row['project_id'], row['kind'])
                prd = store.latest(db, row['project_id'], 'prd')
                if not latest or latest['id'] != row['document_id'] or (prd['id'] if prd else None) != row['base_prd_id']:
                    raise Problem(409, 'VERSION_CONFLICT', '原文或 PRD 已变化，请重新讨论修改，不会覆盖新版本')
                doc = store.add_document(db, row['project_id'], row['kind'], row['after'],
                                         {**latest['metadata'], **({'draft_id': None} if row['kind']=='prd' else {}), 'source': 'reviewed-assistant-change', 'proposal_id': row['id'], 'change_note': row['reason']},
                                         latest['prd_id'])
                result_id = doc['id']
            db.execute('UPDATE document_proposals SET status=?,result_id=? WHERE id=?',
                       ('saved' if body.allow else 'rejected', result_id, proposal_id))
            return proposal_value(db.execute('SELECT * FROM document_proposals WHERE id=?', (proposal_id,)).fetchone())
