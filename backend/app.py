from __future__ import annotations

from backend import file_lock
import io
import json
import os
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from backend.engine import ROOT, model_status, real_runner
from backend.jobs import Jobs
from backend.models import DraftConfirm, ImportInput, InterviewInput, PRDInput, ProjectInput, TaskUpdate, TeardownInput
from backend.store import Problem, Store, now, uid
from backend.assistant import Assistant, initialize as initialize_assistant, note_value, routes as assistant_routes

MAX_BODY = 450000
UI_NOT_BUILT = "工作台界面尚未构建，请先完成前端构建后重试。"


class _WorkbenchAssets(StaticFiles):
    async def check_config(self):
        directory = Path(self.directory)
        # 连 assets 目录本身也不能通过符号链接指向构建目录外。
        if not directory.resolve().is_relative_to(directory.parent):
            raise HTTPException(status_code=404)
        if not directory.is_dir():
            raise Problem(503, "UI_NOT_BUILT", UI_NOT_BUILT)
        await super().check_config()


def create_app(data_root: Path | None = None, runner_factory=real_runner, ui_root: Path | None = None, research_factory=None):
    root = Path(data_root or os.environ.get("WORKBENCH_DATA_DIR", ROOT / "data"))
    store = Store(root)
    initialize_assistant(store)
    from backend.workspace import initialize as initialize_workspace, routes as workspace_routes
    initialize_workspace(store)
    ui = Path(ui_root if ui_root is not None else ROOT / "workbench-ui" / "dist").resolve()

    @asynccontextmanager
    async def lifespan(app):
        with (store.root / "server.lock").open("a") as lock:
            try:
                file_lock.acquire(lock)
            except BlockingIOError:
                raise RuntimeError("这个数据目录已有一个工作台服务在使用") from None
            jobs = Jobs(store, runner_factory, research_factory)
            app.state.jobs = jobs
            jobs.recover()
            app.state.assistant = Assistant(store, jobs)
            app.state.assistant.recover()
            try:
                yield
            finally:
                jobs.close()
                app.state.assistant.close()
                file_lock.release(lock)

    app = FastAPI(title="产品经理工作台 API", version="0.1.0", lifespan=lifespan,
                  description="单人本机版。先建产品，再访谈或导入 PRD；确认需求后生成方案和开发任务。")
    app.state.store = store
    assistant_routes(app)
    workspace_routes(app)
    from backend.original_integrations import routes as original_integration_routes
    original_integration_routes(app)

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Pydantic 的默认错误包含用户输入；只返回字段位置与通用提示。
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        return JSONResponse({"error": {"code": "INVALID_INPUT", "message": "请检查字段：" + "、".join(fields)}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": "HTTP_ERROR", "message": "请求地址或方法不正确"}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "操作未完成，请稍后重试"}}, status_code=500)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        def error(code, message, status):
            return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
        host = request.headers.get("host", "")
        # Host 与来源同时校验，避免其他网站或 DNS rebinding 读取本机资料。
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", host):
            return error("LOCAL_ONLY", "只允许本机访问", 403)
        origin = request.headers.get("origin")
        if origin and origin not in ("http://" + host, "https://" + host):
            return error("ORIGIN_REJECTED", "来源不受信任", 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return error("ORIGIN_REJECTED", "来源不受信任", 403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return error("BODY_TOO_LARGE", "请求内容超过大小限制", 413)
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", include_in_schema=False)
    def home():
        index = (ui / "index.html").resolve()
        if not index.is_relative_to(ui) or not index.is_file():
            raise Problem(503, "UI_NOT_BUILT", UI_NOT_BUILT)
        return FileResponse(index, media_type="text/html")

    app.mount("/assets", _WorkbenchAssets(directory=ui / "assets", check_dir=False, follow_symlink=False),
              name="workbench-assets")

    @app.get("/api/health", tags=["运行状态"])
    def health() -> dict:
        with store.tx() as db:
            active_chats = db.execute("SELECT COUNT(*) FROM assistant_messages WHERE role='assistant' "
                                      "AND status IN ('queued','running')").fetchone()[0]
        return {"status": "ok", "mode": "local-single-user", "database": "sqlite", "schema_version": 1,
                "active_chats": active_chats}

    @app.get("/api/skills", tags=["技能库"])
    def skills() -> dict:
        from backend.skills import catalog
        return {"items": catalog()}

    @app.get("/api/skills/{skill_id}", tags=["技能库"])
    def skill_detail(skill_id: str) -> dict:
        from backend.skills import _entry, catalog, load_skill
        _entry(skill_id)
        documents = load_skill(skill_id)
        return {"skill": next(item for item in catalog() if item["id"] == skill_id),
                "content": documents["SKILL.md"],
                "references": [{"path": path, "content": content} for path, content in documents.items() if path != "SKILL.md"]}

    @app.get("/api/model", tags=["运行状态"])
    def model() -> dict:
        status = model_status()
        with store.tx() as db:
            successful = [store.decode(row) for row in db.execute(
                "SELECT usage,updated_at,kind FROM jobs WHERE status='done' ORDER BY updated_at DESC")]
        latest = next((job for job in successful if job["usage"].get("mode") == "real"
                       and job["usage"].get("model") == status["model"]
                       and str(job["usage"].get("runtime", "")).startswith(status["runtime"])), None)
        return {**status, "validation": "已有该模型的成功生成记录" if latest else status["validation"],
                "last_real_success": {"at": latest["updated_at"], "kind": latest["kind"]} if latest else None}

    def summary(db, row):
        project = dict(row)
        latest = {kind: store.latest(db, row["id"], kind) for kind in ("prd", "draft", "plan", "tasks", "teardown")}
        prd = latest["prd"]
        project["documents"] = {kind: ({k: doc[k] for k in ("id", "version", "prd_id", "created_at")} if doc else None)
                                for kind, doc in latest.items()}
        project["stage"] = "需求梳理"
        if prd:
            project["stage"] = "技术方案"
            for kind in ("plan", "tasks"):
                doc = latest[kind]
                if doc:
                    project["documents"][kind]["stale"] = doc["prd_id"] != prd["id"]
                    if kind == "tasks" and latest["plan"]:
                        project["documents"][kind]["stale"] |= doc["metadata"].get("plan_id") != latest["plan"]["id"]
            if latest["plan"] and latest["plan"]["prd_id"] == prd["id"]:
                project["stage"] = "任务拆解"
            if latest["tasks"] and not project["documents"]["tasks"]["stale"]:
                project["stage"] = "开发跟进"
        project["active_job"] = store.decode(db.execute(
            "SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running')", (row["id"],)).fetchone())
        return project

    @app.post("/api/projects", status_code=201, tags=["产品"])
    def create_project(body: ProjectInput) -> dict:
        project_id, stamp = uid(), now()
        with store.tx() as db:
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, body.name, body.idea, stamp, stamp))
            return summary(db, store.require_project(db, project_id))

    @app.get("/api/projects", tags=["产品"])
    def projects() -> dict:
        with store.tx() as db:
            return {"items": [summary(db, row) for row in db.execute("SELECT * FROM projects WHERE id NOT IN (SELECT item_id FROM workspace_trash WHERE type='project') ORDER BY updated_at DESC")]}

    @app.get("/api/projects/{project_id}", tags=["产品"])
    def project(project_id: str) -> dict:
        with store.tx() as db:
            return summary(db, store.require_project(db, project_id))

    @app.get("/api/projects/{project_id}/messages", tags=["需求访谈"])
    def messages(project_id: str) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            return {"items": [dict(r) for r in db.execute("SELECT * FROM messages WHERE project_id=? ORDER BY seq", (project_id,))]}

    @app.post("/api/projects/{project_id}/interview", status_code=202, tags=["需求访谈"])
    def interview(project_id: str, body: InterviewInput) -> dict:
        return app.state.jobs.enqueue(project_id, "interview", body.model_dump())

    @app.post("/api/projects/{project_id}/teardown", status_code=202, tags=["产品拆解"])
    def teardown(project_id: str, body: TeardownInput) -> dict:
        return app.state.jobs.enqueue(project_id, "teardown", body.model_dump())

    def save_prd(project_id, body, source, draft_id=None):
        with store.tx() as db:
            store.require_project(db, project_id)
            store.idle(db, project_id)
            current = store.latest(db, project_id, "prd")
            if (current["version"] if current else 0) != body.expected_version:
                raise Problem(409, "VERSION_CONFLICT", "PRD 已被更新，请读取最新版本后再保存")
            content = body.content if not draft_id else None
            if draft_id:
                draft = store.decode(db.execute("SELECT * FROM documents WHERE id=? AND project_id=? AND kind='draft'",
                                               (draft_id, project_id)).fetchone())
                if not draft or store.trashed(db, "document", draft_id):
                    raise Problem(404, "DRAFT_NOT_FOUND", "找不到这个需求草稿")
                if draft["prd_id"] != (current["id"] if current else None):
                    raise Problem(409, "STALE_DRAFT", "这份草稿基于旧版需求，请按当前 PRD 重新访谈")
                content = draft["content"]
            return store.add_document(db, project_id, "prd", content,
                                      {"source": source, "change_note": body.change_note, "draft_id": draft_id})

    @app.post("/api/projects/{project_id}/prd", status_code=201, tags=["PRD 与版本"])
    def write_prd(project_id: str, body: PRDInput) -> dict:
        return save_prd(project_id, body, "manual")

    @app.post("/api/projects/{project_id}/prd/import", status_code=201, tags=["PRD 与版本"])
    def import_prd(project_id: str, body: ImportInput) -> dict:
        """接收 UTF-8 Markdown 内容及原文件名。文件名仅作验证，不用作磁盘路径。"""
        return save_prd(project_id, body, "markdown-import")

    @app.post("/api/projects/{project_id}/prd/confirm", status_code=201, tags=["PRD 与版本"])
    def confirm_prd(project_id: str, body: DraftConfirm) -> dict:
        return save_prd(project_id, body, "confirmed-draft", body.draft_id)

    @app.get("/api/projects/{project_id}/documents", tags=["PRD 与版本"])
    def documents(project_id: str, kind: Literal["prd", "draft", "plan", "tasks", "teardown"] | None = None) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            sql = "SELECT * FROM documents WHERE id NOT IN (SELECT item_id FROM workspace_trash WHERE type='document') AND project_id=?" + (" AND kind=?" if kind else "") + " ORDER BY created_at DESC"
            return {"items": [{**store.decode(row), "current": bool((current := store.latest(db, project_id, row["kind"])) and current["id"] == row["id"])} for row in db.execute(sql, (project_id, kind) if kind else (project_id,))]}

    @app.get("/api/projects/{project_id}/documents/{document_id}", tags=["PRD 与版本"])
    def document(project_id: str, document_id: str) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            doc = store.decode(db.execute("SELECT * FROM documents WHERE id=? AND project_id=?", (document_id, project_id)).fetchone())
            if doc and store.trashed(db, "document", document_id):
                doc = None
            if not doc:
                note = db.execute("SELECT * FROM assistant_notes WHERE id=? AND project_id=?", (document_id, project_id)).fetchone()
                if note and not store.trashed(db, "note", document_id):
                    return note_value(note)
            if not doc:
                raise Problem(404, "DOCUMENT_NOT_FOUND", "找不到这个版本的文档")
            return doc

    @app.get("/api/projects/{project_id}/documents/{document_id}/report.html", tags=["产品拆解"])
    def report_html(project_id: str, document_id: str):
        from backend.report_html import render_teardown
        doc = document(project_id, document_id)
        if doc["kind"] != "teardown":
            raise Problem(404, "REPORT_NOT_FOUND", "这份文档不是产品拆解报告")
        return Response(render_teardown(doc), media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="teardown-V{doc["version"]}.html"',
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})

    @app.post("/api/projects/{project_id}/plan", status_code=202, tags=["方案与任务"])
    def plan(project_id: str) -> dict:
        return app.state.jobs.enqueue(project_id, "plan")

    @app.post("/api/projects/{project_id}/tasks/generate", status_code=202, tags=["方案与任务"])
    def generate_tasks(project_id: str) -> dict:
        return app.state.jobs.enqueue(project_id, "tasks")

    @app.get("/api/projects/{project_id}/tasks", tags=["方案与任务"])
    def tasks(project_id: str, include_history: bool = False) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            doc = store.latest(db, project_id, "tasks")
            query = "SELECT * FROM tasks WHERE project_id=?" + ("" if include_history else " AND document_id=?") + " ORDER BY created_at,position"
            prd = store.latest(db, project_id, "prd")
            plan = store.latest(db, project_id, "plan")
            return {"stale": bool(doc and prd and (doc["prd_id"] != prd["id"] or
                    (plan and doc["metadata"].get("plan_id") != plan["id"]))),
                    "items": [dict(row) for row in db.execute(query, (project_id,) if include_history else (project_id, doc["id"] if doc else ""))]}

    @app.patch("/api/projects/{project_id}/tasks/{task_id}", tags=["方案与任务"])
    def update_task(project_id: str, task_id: str, body: TaskUpdate) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            cursor = db.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=? AND project_id=?", (body.status, now(), task_id, project_id))
            if not cursor.rowcount:
                raise Problem(404, "TASK_NOT_FOUND", "找不到这个开发任务")
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    @app.get("/api/projects/{project_id}/jobs", tags=["生成记录"])
    def project_jobs(project_id: str) -> dict:
        with store.tx() as db:
            store.require_project(db, project_id)
            rows = [store.decode(r) for r in db.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            return {"items": rows, "usage": {field: sum(r["usage"].get(field, 0) for r in rows)
                                              for field in ("calls", "input_tokens", "output_tokens")}}

    @app.get("/api/jobs/{job_id}", tags=["生成记录"])
    def job(job_id: str) -> dict:
        return app.state.jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/research", tags=["产品拆解"])
    def job_research(job_id: str) -> dict:
        current = app.state.jobs.get(job_id)
        if current["kind"] != "teardown":
            raise Problem(404, "RESEARCH_NOT_FOUND", "这个任务没有公开搜证记录")
        return app.state.jobs.research_snapshot(current)

    @app.get("/api/jobs/{job_id}/events", tags=["生成记录"])
    def events(job_id: str, after: int = Query(0, ge=0)) -> dict:
        current = app.state.jobs.get(job_id)
        with store.tx() as db:
            return {"status": current["status"], "items": [dict(r) for r in db.execute(
                "SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 200", (job_id, after))]}

    @app.post("/api/jobs/{job_id}/retry", status_code=202, tags=["生成记录"])
    def retry(job_id: str) -> dict:
        old = app.state.jobs.get(job_id)
        return app.state.jobs.enqueue(old["project_id"], old["kind"], retry=job_id)

    @app.get("/api/projects/{project_id}/export", tags=["文档导出"])
    def export(project_id: str):
        """导出所有文档版本与开发任务，不包含密钥、数据库或引擎缓存。"""
        with store.tx() as db:
            project = store.require_project(db, project_id)
            docs = [store.decode(r) for r in db.execute("SELECT * FROM documents WHERE project_id=? AND id NOT IN (SELECT item_id FROM workspace_trash WHERE type='document') ORDER BY kind,version", (project_id,))]
            task_rows = [dict(r) for r in db.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY created_at,position", (project_id,))]
            notes = [note_value(row) for row in db.execute("SELECT * FROM assistant_notes WHERE project_id=? AND id NOT IN (SELECT item_id FROM workspace_trash WHERE type='note') ORDER BY created_at", (project_id,))]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2))
            for doc in docs:
                archive.writestr(f"{doc['kind']}/V{doc['version']}.md", doc["content"])
                archive.writestr(f"{doc['kind']}/V{doc['version']}.json", json.dumps(doc, ensure_ascii=False, indent=2))
                if doc["kind"] == "teardown":
                    from backend.report_html import render_teardown
                    archive.writestr(f"teardown/V{doc['version']}.html", render_teardown(doc))
            archive.writestr("task-status.json", json.dumps(task_rows, ensure_ascii=False, indent=2))
            for note in notes:
                archive.writestr(f"notes/{note['id']}.md", note["content"])
                archive.writestr(f"notes/{note['id']}.json", json.dumps(note, ensure_ascii=False, indent=2))
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="workbench-{project_id}.zip"'})

    return app
