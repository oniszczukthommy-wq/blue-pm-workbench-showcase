import io
import json
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import create_app
from backend.jobs import Jobs
from backend.models import TaskResult
from backend.store import Problem, Store, now, uid
from blueprint.mock import MockRunner
from blueprint.schema import validate


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1:8787")


class OfflineRunner(MockRunner):
    """仅注入测试，不向运行中的服务开放模拟开关。"""
    def complete(self, stage, system, prompt, schema):
        if stage == "interview":
            return {"reply": "请确认核心流程", "questions": ["只做个人版吗？推荐是。"],
                    "ready": False, "draft": "# 需求草稿\n\n个人产品需求工作台；预算待确认。"}
        if stage == "tasks":
            return {"tasks": [{"title": "实现需求保存", "description": "保存 PRD 并读取历史版本",
                               "acceptance": "重启服务后能够读到原文", "priority": "P0"}]}
        return super().complete(stage, system, prompt, schema)


@pytest.fixture
def api(tmp_path):
    with client_for(create_app(tmp_path / "data", OfflineRunner)) as client:
        yield client


def project(client, name="测试产品"):
    r = client.post("/api/projects", json={"name": name, "idea": "给个人产品经理用的需求管理工作台，保存文档并跟踪进度"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def save_prd(client, pid, version=0, content="# PRD\n为产品经理管理个人需求，保存数据并跟踪进度。"):
    r = client.post(f"/api/projects/{pid}/prd", json={"content": content, "expected_version": version})
    assert r.status_code == 201, r.text
    return r.json()


def wait(client, job):
    assert job.status_code == 202, job.text
    job_id = job.json()["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] not in ("queued", "running"):
            return result
        time.sleep(.01)
    pytest.fail("job did not finish")


def plan(client, pid):
    job = wait(client, client.post(f"/api/projects/{pid}/plan"))
    assert job["status"] == "done", job
    return job


def test_no_key_can_create_import_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.engine.config", lambda: {"key": "", "model": "deepseek-flash", "base_url": "https://api.deepseek.com/anthropic"})
    with client_for(create_app(tmp_path)) as client:
        pid = project(client)
        r = client.post(f"/api/projects/{pid}/prd/import", json={
            "filename": "需求.md", "content": "# PRD\n真实的本地文档", "expected_version": 0})
        assert r.status_code == 201
        r = client.post(f"/api/projects/{pid}/plan")
        assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []
        assert client.get(f"/api/projects/{pid}/export").status_code == 200


def test_interview_requires_explicit_confirmation(api):
    pid = project(api)
    result = wait(api, api.post(f"/api/projects/{pid}/interview", json={"message": "按个人版做"}))
    assert result["status"] == "done", result
    assert api.get(f"/api/projects/{pid}/documents?kind=prd").json()["items"] == []
    assert api.post(f"/api/projects/{pid}/plan").status_code == 409
    draft_id = result["result"]["document_id"]
    r = api.post(f"/api/projects/{pid}/prd/confirm", json={"draft_id": draft_id, "expected_version": 0})
    assert r.status_code == 201 and r.json()["metadata"]["source"] == "confirmed-draft"
    assert len(api.get(f"/api/projects/{pid}/messages").json()["items"]) == 2


def test_full_flow_and_version_invalidation(api):
    pid = project(api)
    first = save_prd(api, pid)
    job = plan(api, pid)
    document = api.get(f"/api/projects/{pid}/documents/{job['result']['document_id']}").json()
    assert document["prd_id"] == first["id"]
    assert len(document["metadata"]["components"]) == 17
    result = wait(api, api.post(f"/api/projects/{pid}/tasks/generate"))
    assert result["status"] == "done", result
    assert api.get(f"/api/projects/{pid}").json()["stage"] == "开发跟进"
    task = api.get(f"/api/projects/{pid}/tasks").json()["items"][0]
    assert api.patch(f"/api/projects/{pid}/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    second = save_prd(api, pid, 1, "# PRD V2\n增加本地备份功能")
    assert second["version"] == 2
    assert api.get(f"/api/projects/{pid}/documents/{first['id']}").json()["content"] == first["content"]
    assert api.get(f"/api/projects/{pid}").json()["documents"]["plan"]["stale"]
    assert api.get(f"/api/projects/{pid}/tasks").json()["stale"]
    assert api.post(f"/api/projects/{pid}/tasks/generate").status_code == 409


def test_export_contains_all_versions_and_no_secrets(api):
    pid = project(api)
    save_prd(api, pid)
    save_prd(api, pid, 1, "# 第二版")
    response = api.get(f"/api/projects/{pid}/export")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"prd/V1.md", "prd/V2.md", "project.json", "task-status.json"} <= set(archive.namelist())
        assert not any(".env" in name or ".sqlite" in name or "runs/" in name for name in archive.namelist())
        assert "第二版" in archive.read("prd/V2.md").decode()


def test_new_plan_marks_previous_task_batch_stale(api):
    pid = project(api)
    save_prd(api, pid)
    plan(api, pid)
    wait(api, api.post(f"/api/projects/{pid}/tasks/generate"))
    assert api.get(f"/api/projects/{pid}/tasks").json()["stale"] is False
    plan(api, pid)
    assert api.get(f"/api/projects/{pid}/tasks").json()["stale"] is True
    assert api.get(f"/api/projects/{pid}").json()["stage"] == "任务拆解"


def test_optimistic_version_conflict_preserves_content(api):
    pid = project(api)
    first = save_prd(api, pid)
    r = api.post(f"/api/projects/{pid}/prd", json={"content": "覆盖", "expected_version": 0})
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert len(api.get(f"/api/projects/{pid}/documents?kind=prd").json()["items"]) == 1


def test_cross_project_document_draft_and_task_are_rejected(api):
    a, b = project(api, "A"), project(api, "B")
    doc = save_prd(api, a)
    assert api.get(f"/api/projects/{b}/documents/{doc['id']}").status_code == 404
    draft = wait(api, api.post(f"/api/projects/{a}/interview", json={"message": "草稿"}))
    assert api.post(f"/api/projects/{b}/prd/confirm", json={"draft_id": draft["result"]["document_id"], "expected_version": 0}).status_code == 404
    plan(api, a)
    wait(api, api.post(f"/api/projects/{a}/tasks/generate"))
    task_id = api.get(f"/api/projects/{a}/tasks").json()["items"][0]["id"]
    assert api.patch(f"/api/projects/{b}/tasks/{task_id}", json={"status": "done"}).status_code == 404


@pytest.mark.parametrize("filename,content", [("../../PRD.md", "text"), ("a.pdf", "text"), ("x.md", "hello\x00world"), ("x.md", "")])
def test_import_rejects_invalid_files(api, filename, content):
    pid = project(api)
    assert api.post(f"/api/projects/{pid}/prd/import", json={"filename": filename, "content": content, "expected_version": 0}).status_code == 422


def test_host_origin_and_body_limits(api):
    assert api.get("/api/projects", headers={"Host": "evil.test"}).status_code == 403
    assert api.get("/api/projects", headers={"Origin": "https://evil.test"}).status_code == 403
    assert api.get("/api/projects", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert api.post("/api/projects", content="x" * 450001).status_code == 413
    r = api.post("/api/projects", content="not-json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "not-json" not in r.text
    assert api.get("/missing").json()["error"]["code"] == "HTTP_ERROR"


def test_restart_preserves_documents_jobs_and_task_status(tmp_path):
    with client_for(create_app(tmp_path, OfflineRunner)) as client:
        pid = project(client)
        doc = save_prd(client, pid)
        job = plan(client, pid)
        wait(client, client.post(f"/api/projects/{pid}/tasks/generate"))
        task = client.get(f"/api/projects/{pid}/tasks").json()["items"][0]
        client.patch(f"/api/projects/{pid}/tasks/{task['id']}", json={"status": "doing"})
    with client_for(create_app(tmp_path, OfflineRunner)) as client:
        assert client.get(f"/api/projects/{pid}/documents/{doc['id']}").json()["content"] == doc["content"]
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
        assert client.get(f"/api/projects/{pid}/tasks").json()["items"][0]["status"] == "doing"


def test_interrupted_job_requires_manual_retry(tmp_path):
    with client_for(create_app(tmp_path, OfflineRunner)) as client:
        pid = project(client)
        prd = save_prd(client, pid)
    store, job_id = Store(tmp_path), uid()
    with store.tx() as db:
        db.execute("INSERT INTO jobs(id,project_id,kind,status,payload,cache_id,created_at,updated_at) VALUES(?,?,?,'running',?,?,?,?)",
                   (job_id, pid, "plan", json.dumps({"prd_id": prd["id"]}), uid(), now(), now()))
    with client_for(create_app(tmp_path, OfflineRunner)) as client:
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "interrupted"
        assert client.get(f"/api/projects/{pid}/documents?kind=plan").json()["items"] == []
        retry = wait(client, client.post(f"/api/jobs/{job_id}/retry"))
        assert retry["status"] == "done" and retry["retry_of"] == job_id
        events = client.get(f"/api/jobs/{retry['id']}/events").json()["items"]
        assert events[-1]["type"] == "done"
        assert client.get(f"/api/jobs/{retry['id']}/events?after={events[-1]['seq']}").json()["items"] == []


def test_failure_redaction_and_schema_failure_keep_old_documents(tmp_path):
    class BadRunner(OfflineRunner):
        def complete(self, *args):
            raise RuntimeError("sk-secret-user-key full-private-document /Users/private")
    with client_for(create_app(tmp_path, BadRunner)) as client:
        pid = project(client)
        result = wait(client, client.post(f"/api/projects/{pid}/interview", json={"message": "hello"}))
        assert result["status"] == "failed"
        assert "sk-secret" not in result["error"]
        assert client.get(f"/api/projects/{pid}/documents").json()["items"] == []
        events = client.get(f"/api/jobs/{result['id']}/events").text
        assert "sk-secret" not in events and "full-private" not in events


def test_invalid_model_result_is_not_committed(tmp_path):
    class InvalidRunner(OfflineRunner):
        def complete(self, *args):
            return {"reply": "ok", "questions": ["Q"] * 6, "ready": False, "draft": "# text"}
    with client_for(create_app(tmp_path, InvalidRunner)) as client:
        pid = project(client)
        result = wait(client, client.post(f"/api/projects/{pid}/interview", json={"message": "hello"}))
        assert result["status"] == "failed"
        assert client.get(f"/api/projects/{pid}/documents").json()["items"] == []


def test_busy_project_rejects_competing_work(tmp_path):
    gate = threading.Event()
    entered = threading.Event()
    class BlockingRunner(OfflineRunner):
        def complete(self, *args):
            entered.set()
            gate.wait(5)
            return super().complete(*args)
    with client_for(create_app(tmp_path, BlockingRunner)) as client:
        pid = project(client)
        response = client.post(f"/api/projects/{pid}/interview", json={"message": "hello"})
        try:
            assert entered.wait(2)
            assert client.post(f"/api/projects/{pid}/interview", json={"message": "another"}).status_code == 409
            assert client.post(f"/api/projects/{pid}/prd", json={"content": "# text", "expected_version": 0}).status_code == 409
        finally:
            gate.set()
        assert wait(client, response)["status"] == "done"


def test_one_process_per_data_directory(tmp_path):
    with client_for(create_app(tmp_path, OfflineRunner)):
        with pytest.raises(RuntimeError, match="已有"):
            with client_for(create_app(tmp_path, OfflineRunner)):
                pass


def test_openapi_and_model_status_do_not_expose_key(api):
    assert api.get("/docs").status_code == 200
    assert "/api/projects/{project_id}/prd/import" in api.get("/openapi.json").json()["paths"]
    data = api.get("/api/model").json()
    assert "key" not in data and "api_key" not in data


def test_retry_failed_tasks_rejects_newer_plan(tmp_path):
    class FailedTasksRunner(OfflineRunner):
        def complete(self, stage, *args):
            if stage == "tasks":
                raise RuntimeError("synthetic task failure")
            return super().complete(stage, *args)

    with client_for(create_app(tmp_path, FailedTasksRunner)) as client:
        pid = project(client)
        save_prd(client, pid)
        first_plan = plan(client, pid)
        failed = wait(client, client.post(f"/api/projects/{pid}/tasks/generate"))
        assert failed["status"] == "failed"
        assert failed["payload"]["plan_id"] == first_plan["result"]["document_id"]
        newer_plan = plan(client, pid)
        assert newer_plan["result"]["document_id"] != failed["payload"]["plan_id"]

        response = client.post(f"/api/jobs/{failed['id']}/retry")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STALE_INPUT"
        assert len(client.get(f"/api/projects/{pid}/jobs").json()["items"]) == 3
        assert client.get(f"/api/projects/{pid}/tasks").json()["items"] == []


def test_retry_failed_interview_rejects_newer_prd(tmp_path):
    class FailedInterviewRunner(OfflineRunner):
        def complete(self, stage, *args):
            if stage == "interview":
                raise RuntimeError("synthetic interview failure")
            return super().complete(stage, *args)

    with client_for(create_app(tmp_path, FailedInterviewRunner)) as client:
        pid = project(client)
        first = save_prd(client, pid)
        failed = wait(client, client.post(f"/api/projects/{pid}/interview", json={"message": "增加备份"}))
        assert failed["status"] == "failed"
        assert failed["payload"]["prd_id"] == first["id"]
        save_prd(client, pid, 1, "# PRD V2\n已确认只做本地备份")

        response = client.post(f"/api/jobs/{failed['id']}/retry")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STALE_INPUT"
        assert len(client.get(f"/api/projects/{pid}/jobs").json()["items"]) == 1
        assert client.get(f"/api/projects/{pid}/documents?kind=draft").json()["items"] == []


def test_confirm_draft_rejects_newer_prd_baseline(api):
    pid = project(api)
    first = save_prd(api, pid)
    draft_job = wait(api, api.post(f"/api/projects/{pid}/interview", json={"message": "补充需求"}))
    assert draft_job["status"] == "done"
    draft_id = draft_job["result"]["document_id"]
    draft = api.get(f"/api/projects/{pid}/documents/{draft_id}").json()
    assert draft["prd_id"] == first["id"]
    current = save_prd(api, pid, 1, "# PRD V2\n新增已确认需求必须保留")

    response = api.post(f"/api/projects/{pid}/prd/confirm", json={
        "draft_id": draft_id, "expected_version": current["version"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_DRAFT"
    prds = api.get(f"/api/projects/{pid}/documents?kind=prd").json()["items"]
    assert len(prds) == 2
    assert prds[0]["id"] == current["id"]
    assert prds[0]["content"] == current["content"]


def test_structured_tasks_expands_refs_and_preserves_priority_enum():
    valid_task = {"title": "保存需求", "description": "保存需求正文", "acceptance": "重启后正文一致", "priority": "P0"}
    observed = []

    class SchemaCheckingRunner:
        def complete(self, stage, system, prompt, schema):
            observed.append(schema)
            assert stage == "tasks"
            assert json.loads(prompt)["output_schema"] == schema
            assert '"$ref"' not in json.dumps(schema)
            item = schema["properties"]["tasks"]["items"]
            assert item["properties"]["priority"]["enum"] == ["P0", "P1", "P2"]
            invalid_task = {**valid_task, "priority": "P9"}
            errors = validate(schema, {"tasks": [invalid_task]})
            assert any("priority" in error for error in errors)
            return {"tasks": [valid_task]}

    result = Jobs.structured(None, SchemaCheckingRunner(), "tasks", {"prd": "# PRD", "plan": "# 方案"}, TaskResult)
    assert result == {"tasks": [valid_task]}
    assert len(observed) == 1


@pytest.mark.parametrize("correct_on_retry", [True, False])
def test_structured_string_constraint_gets_only_one_correction(correct_on_retry):
    valid_task = {"title": "保存需求", "description": "保存正文", "acceptance": "重启后可读", "priority": "P0"}
    prompts = []

    class LengthRepairRunner:
        def complete(self, stage, system, prompt, schema):
            prompts.append(prompt)
            assert schema["properties"]["tasks"]["items"]["properties"]["title"]["maxLength"] == 150
            if len(prompts) == 2 and correct_on_retry:
                return {"tasks": [valid_task]}
            return {"tasks": [{**valid_task, "title": "x" * 151}]}

    if correct_on_retry:
        result = Jobs.structured(None, LengthRepairRunner(), "tasks", {}, TaskResult)
        assert result == {"tasks": [valid_task]}
    else:
        with pytest.raises(ValidationError):
            Jobs.structured(None, LengthRepairRunner(), "tasks", {}, TaskResult)
    assert len(prompts) == 2
    assert "输出未通过校验" in prompts[1]
    assert '["tasks", 0, "title"]' in prompts[1]


def test_shutdown_cancels_queue_stops_next_call_and_persists_interrupted(tmp_path):
    store = Store(tmp_path)
    project_ids = [uid() for _ in range(3)]
    with store.tx() as db:
        for pid in project_ids:
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (pid, "关闭测试", "合成测试需求", now(), now()))

    entered, release = threading.Event(), threading.Event()
    calls, close_errors = [], []

    class BlockingInvalidRunner:
        def complete(self, stage, system, prompt, schema):
            calls.append(stage)
            entered.set()
            assert release.wait(5), "test did not release the synthetic model call"
            # 强制触发下一次结构纠错调用，关闭信号必须在该调用发生前拦截。
            return {"reply": "x" * 12001, "questions": [], "ready": False, "draft": "# 合成草稿"}

    jobs = Jobs(store, BlockingInvalidRunner)

    def close_jobs():
        try:
            jobs.close()
        except BaseException as exc:
            close_errors.append(exc)

    closer = threading.Thread(target=close_jobs, daemon=True)
    try:
        running = jobs.enqueue(project_ids[0], "interview", {"message": "运行中"})
        assert entered.wait(2)
        queued = jobs.enqueue(project_ids[1], "interview", {"message": "排队中"})
        assert queued["status"] == "queued"
        closer.start()
        assert jobs.stopping.wait(2)

        with pytest.raises(Problem) as rejected:
            jobs.enqueue(project_ids[2], "interview", {"message": "关闭后新任务"})
        assert rejected.value.status == 503
        assert rejected.value.code == "SERVICE_STOPPING"
        release.set()
        closer.join(5)
        assert not closer.is_alive()
        assert close_errors == []
        assert calls == ["interview"]
        assert jobs.get(running["id"])["status"] == "interrupted"
        assert jobs.get(queued["id"])["status"] == "interrupted"

        reopened = Store(tmp_path)
        with reopened.tx() as db:
            statuses = [row["status"] for row in db.execute("SELECT status FROM jobs")]
            assert statuses == ["interrupted", "interrupted"]
            assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_ids[2],)).fetchone()[0] == 0
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(5)
        else:
            jobs.close()
