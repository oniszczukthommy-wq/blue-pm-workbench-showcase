"""Conversation workflows exercised offline against a temporary local database.

The runner is injected only into these test apps. No provider keys or real service
data are loaded, and model success here is not evidence of supplier availability.
"""
from __future__ import annotations

import io
import json
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from blueprint.mock import MockRunner


class Script:
    def __init__(self, reply=None):
        self.reply = reply or {"reply": "先讨论用户问题，不必立刻写 PRD。", "reference_ids": [], "actions": []}
        self.calls = []
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_first = False
        self.interview_error = None

    def factory(self):
        script = self

        class Runner(MockRunner):
            def complete(self, stage, system, prompt, schema):
                with script.lock:
                    script.calls.append((stage, prompt))
                    assistant_number = sum(kind == "assistant" for kind, _ in script.calls)
                if stage == "assistant":
                    if script.block_first and assistant_number == 1:
                        script.started.set()
                        if not script.release.wait(10):
                            raise RuntimeError("offline test release timed out")
                    if isinstance(script.reply, Exception):
                        raise script.reply
                    payload = json.JSONDecoder().raw_decode(prompt)[0]["input"]
                    return script.reply(payload) if callable(script.reply) else script.reply
                if stage == "interview":
                    if script.interview_error:
                        raise script.interview_error
                    return {"reply": "已整理为待你确认的草稿。", "questions": [], "ready": True,
                            "draft": "# 需求草稿\n\n保留已有需求，并整理本次用户讨论；仍需人工确认。"}
                if stage == "tasks":
                    return {"tasks": [{"title": "实现确认后的需求", "description": "按 PRD 保存资料",
                                       "acceptance": "保存后可重新读取", "priority": "P0"}]}
                return super().complete(stage, system, prompt, schema)

        return Runner()

    @property
    def assistant_calls(self):
        return [prompt for stage, prompt in self.calls if stage == "assistant"]

    def input(self, index=-1):
        return json.JSONDecoder().raw_decode(self.assistant_calls[index])[0]["input"]


def client_for(app):
    return TestClient(app, base_url="http://127.0.0.1:8787")


@pytest.fixture
def service(tmp_path):
    script = Script()
    app = create_app(tmp_path / "data", runner_factory=script.factory)
    with client_for(app) as client:
        yield client, script
        script.release.set()


def project(client, name="产品A"):
    response = client.post("/api/projects", json={"name": name, "idea": f"{name} 的独立需求"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def prd(client, pid, version=0, text="# PRD\n个人产品需求"):
    response = client.post(f"/api/projects/{pid}/prd", json={"content": text, "expected_version": version})
    assert response.status_code == 201, response.text
    return response.json()


def conversation(client, pid=None, title=None):
    response = client.post("/api/conversations", json={"project_id": pid, "title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def messages(client, cid):
    response = client.get(f"/api/conversations/{cid}")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def send(client, cid, text="讨论这个想法", request_id=None):
    response = client.post(f"/api/conversations/{cid}/messages", json={
        "message": text, "request_id": request_id or uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    return response.json()


def wait_message(client, cid, mid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = next(row for row in messages(client, cid) if row["id"] == mid)
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(.01)
    pytest.fail("assistant reply did not finish")


def chat(client, cid, text="讨论这个想法"):
    pending = send(client, cid, text)
    result = wait_message(client, cid, pending["id"])
    assert result["status"] == "done", result
    return result


def wait_job(client, job_id, expected="done"):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] not in {"queued", "running"}:
            assert result["status"] == expected, result
            return result
        time.sleep(.01)
    pytest.fail("workbench job did not finish")


def proposal(kind, payload=None):
    return {"reply": "可以按下面的动作继续，尚未执行。", "reference_ids": [],
            "actions": [{"type": kind, "label": "继续产品工作", "payload": payload or {}}]}


def execute(client, cid, reply, pid=None):
    action = reply["actions"][0]
    return client.post(f"/api/conversations/{cid}/messages/{reply['id']}/actions/{action['id']}/execute",
                       json={"project_id": pid})


def save_note(client, cid, reply, pid=None, title=None):
    return client.post(f"/api/conversations/{cid}/messages/{reply['id']}/notes",
                       json={"project_id": pid, "title": title})


def test_global_discussion_does_not_require_or_create_a_product_or_prd(service):
    client, script = service
    cid = conversation(client)
    reply = chat(client, cid, "还没立项，想讨论一个用户痛点。")
    assert reply["references"] == [] and reply["actions"] == []
    assert reply["usage"]["mode"] == "test"
    assert script.input()["context"]["project"] is None
    assert script.input()["context"]["documents"] == []
    assert client.get("/api/projects").json()["items"] == []
    assert [m["role"] for m in messages(client, cid)] == ["user", "assistant"]
    assert client.get("/api/conversations").json()["items"][0]["id"] == cid


def test_product_context_uses_latest_versions_and_reference_snapshot_stays_fixed(service):
    client, script = service
    a, b = project(client), project(client, "产品B")
    old = prd(client, a, text="# 旧需求\nOLD_A_ONLY")
    current = prd(client, a, 1, "# 当前需求\nCURRENT_A_ONLY")
    private_b = prd(client, b, text="# B私有需求\nPRIVATE_B_ONLY")
    script.reply = {"reply": "依据当前需求 V2 讨论。", "reference_ids": [current["id"]], "actions": []}
    cid = conversation(client, a)
    reply = chat(client, cid)
    prompt = script.assistant_calls[0]
    assert "CURRENT_A_ONLY" in prompt
    assert "OLD_A_ONLY" not in prompt and "PRIVATE_B_ONLY" not in prompt
    refs = reply["references"]
    assert [(r["document_id"], r["version"], r["project_id"]) for r in refs] == [(current["id"], 2, a)]
    assert private_b["id"] not in json.dumps(refs) and old["id"] not in json.dumps(refs)
    prd(client, a, 2, "# 第三版\nNEWER_A_ONLY")
    reread = next(m for m in messages(client, cid) if m["id"] == reply["id"])
    assert reread["references"] == refs


def test_invalid_or_other_product_reference_cannot_be_saved_as_a_real_citation(service):
    client, script = service
    a, b = project(client), project(client, "产品B")
    prd(client, a)
    foreign = prd(client, b, text="# 其他产品\n禁止混入当前对话")
    script.reply = {"reply": "伪引用不能成为真实证据。", "reference_ids": [foreign["id"], "invented-id"], "actions": []}
    cid = conversation(client, a)
    result = wait_message(client, cid, send(client, cid)["id"])
    assert result["status"] == "failed" and result["references"] == []
    assert len(script.assistant_calls) == 2  # One validation correction, no unlimited retries.
    assert save_note(client, cid, result).status_code == 409


def test_context_marks_outdated_plan_tasks_and_retains_manual_status_with_its_snapshot(service):
    client, script = service
    pid = project(client)
    prd(client, pid)
    generated_plan = client.post(f"/api/projects/{pid}/plan")
    wait_job(client, generated_plan.json()["id"])
    generated_tasks = client.post(f"/api/projects/{pid}/tasks/generate")
    wait_job(client, generated_tasks.json()["id"])
    task = client.get(f"/api/projects/{pid}/tasks").json()["items"][0]
    assert client.patch(f"/api/projects/{pid}/tasks/{task['id']}", json={"status": "doing"}).status_code == 200
    current_prd = prd(client, pid, 1, "# 改版PRD\n最新用户需求已经改变")
    script.reply = lambda data: {
        "reply": "旧方案与任务需按最新需求复核，人工状态只说明历史跟进情况。",
        "reference_ids": [item["reference"]["id"] for item in data["context"]["documents"]], "actions": []}
    reply = chat(client, conversation(client, pid))
    context = script.input()["context"]
    documents = {item["reference"]["kind"]: item for item in context["documents"]}
    assert documents["prd"]["reference"]["document_id"] == current_prd["id"]
    assert documents["prd"]["stale"] is False
    assert documents["plan"]["stale"] is True and documents["tasks"]["stale"] is True
    assert context["task_status_stale"] is True
    assert context["task_status"][0]["status"] == "doing"
    assert context["task_status"][0]["stale"] is True
    refs = {ref["kind"]: ref for ref in reply["references"]}
    assert refs["plan"]["stale"] is True and refs["tasks"]["stale"] is True


def test_long_product_document_is_explicitly_a_partial_context_not_claimed_as_full_read(service):
    client, script = service
    pid = project(client)
    body = "# 长需求\n" + "需要保留的需求内容。" * 1800
    document = prd(client, pid, text=body)
    chat(client, conversation(client, pid))
    supplied = next(item for item in script.input()["context"]["documents"]
                    if item["reference"]["document_id"] == document["id"])
    assert supplied["truncated"] is True
    assert supplied["original_characters"] == len(body)
    assert 0 < len(supplied["content"]) < len(body)


def test_saved_product_note_can_be_reused_and_opened_without_cross_product_or_version_leaks(service):
    client, script = service
    a, b = project(client), project(client, "产品B")
    first_prd = prd(client, a, text="# PRD第一版\n当时确认的范围")
    script.reply = {"reply": "NOTE_A_PRIVATE_BODY：保留这次需求取舍结论。",
                    "reference_ids": [first_prd["id"]], "actions": []}
    initial_cid = conversation(client, a)
    original_reply = chat(client, initial_cid)
    note = save_note(client, initial_cid, original_reply, title="需求取舍记录").json()
    original_refs = note["metadata"]["references"]
    newer_prd = prd(client, a, 1, "# PRD第二版\n当前正式需求已升级")
    script.reply = {"reply": "已读保存的讨论结论，正式需求以当前PRD为准。",
                    "reference_ids": [note["id"]], "actions": []}
    reused = chat(client, conversation(client, a))
    supplied = script.input()["context"]["documents"]
    note_context = next(item for item in supplied if item["reference"]["document_id"] == note["id"])
    assert "NOTE_A_PRIVATE_BODY" in note_context["content"]
    assert note_context["reference"]["kind"] == "note" and note_context["reference"]["version"] == 1
    assert note_context["stale"] is False
    assert reused["references"][0]["document_id"] == note["id"]
    assert any(item["reference"]["document_id"] == newer_prd["id"] for item in supplied)
    opened = client.get(f"/api/projects/{a}/documents/{note['id']}")
    assert opened.status_code == 200
    assert opened.json()["content"] == note["content"]
    assert opened.json()["metadata"]["references"] == original_refs
    assert original_refs[0]["document_id"] == first_prd["id"] and original_refs[0]["version"] == 1
    foreign = client.get(f"/api/projects/{b}/documents/{note['id']}")
    assert foreign.status_code == 404 and "NOTE_A_PRIVATE_BODY" not in foreign.text
    script.reply = {"reply": "只讨论本会话提供的内容。", "reference_ids": [], "actions": []}
    chat(client, conversation(client, b))
    assert note["id"] not in script.assistant_calls[-1] and "NOTE_A_PRIVATE_BODY" not in script.assistant_calls[-1]
    chat(client, conversation(client))
    assert script.input()["context"]["project"] is None and script.input()["context"]["documents"] == []
    assert note["id"] not in script.assistant_calls[-1] and "NOTE_A_PRIVATE_BODY" not in script.assistant_calls[-1]


def test_separate_conversations_do_not_mix_chat_history(service):
    client, script = service
    a, b = conversation(client), conversation(client)
    chat(client, a, "ALPHA_TOPIC_PRIVATE")
    chat(client, b, "BETA_TOPIC_PRIVATE")
    assert "ALPHA_TOPIC_PRIVATE" not in script.assistant_calls[-1]
    chat(client, a, "继续刚才的话题")
    assert "ALPHA_TOPIC_PRIVATE" in script.assistant_calls[-1]
    assert "BETA_TOPIC_PRIVATE" not in script.assistant_calls[-1]


def test_message_idempotency_and_active_conversation_guard(service):
    client, script = service
    script.block_first = True
    cid = conversation(client)
    request_id = uuid.uuid4().hex
    pending = send(client, cid, "只发送一次", request_id)
    try:
        assert script.started.wait(3)
        repeated = send(client, cid, "只发送一次", request_id)
        assert repeated["id"] == pending["id"]
        conflicting = client.post(f"/api/conversations/{cid}/messages", json={
            "message": "同一个请求编号的另一个内容", "request_id": request_id})
        assert conflicting.status_code == 409
        busy = client.post(f"/api/conversations/{cid}/messages", json={
            "message": "第二条", "request_id": uuid.uuid4().hex})
        assert busy.status_code == 409
        assert len(messages(client, cid)) == 2
    finally:
        script.release.set()
    assert wait_message(client, cid, pending["id"])["status"] == "done"
    assert len(script.assistant_calls) == 1


def test_conversations_and_existing_workflows_share_one_serial_queue(service):
    client, script = service
    script.block_first = True
    first, second = conversation(client), conversation(client)
    pid = project(client)
    pending = send(client, first)
    try:
        assert script.started.wait(3)
        existing = client.post(f"/api/projects/{pid}/interview", json={"message": "整理产品需求"})
        assert existing.status_code == 202
        another = send(client, second)
        assert client.get(f"/api/jobs/{existing.json()['id']}").json()["status"] == "queued"
        assert next(m for m in messages(client, second) if m["id"] == another["id"])["status"] == "queued"
        assert len(script.calls) == 1
    finally:
        script.release.set()
    assert wait_message(client, first, pending["id"])["status"] == "done"
    wait_job(client, existing.json()["id"])
    assert wait_message(client, second, another["id"])["status"] == "done"
    assert [stage for stage, _ in script.calls] == ["assistant", "interview", "assistant"]


def test_failure_is_redacted_and_manual_retry_keeps_failed_record_and_single_user(service):
    client, script = service
    script.reply = RuntimeError("SYNTHETIC_PROVIDER_PRIVATE_ERROR_do_not_echo")
    cid = conversation(client)
    failed = wait_message(client, cid, send(client, cid)["id"])
    assert failed["status"] == "failed"
    assert "SYNTHETIC_PROVIDER_PRIVATE_ERROR" not in json.dumps(failed)
    for _ in range(3):
        messages(client, cid)
    assert len(script.assistant_calls) == 1
    script.reply = {"reply": "手动重试成功", "reference_ids": [], "actions": []}
    request_id = uuid.uuid4().hex
    path = f"/api/conversations/{cid}/messages/{failed['id']}/retry"
    retry = client.post(path, json={"request_id": request_id})
    assert retry.status_code == 202, retry.text
    assert retry.json()["id"] != failed["id"]
    repeated = client.post(path, json={"request_id": request_id})
    assert repeated.status_code == 202 and repeated.json()["id"] == retry.json()["id"]
    done = wait_message(client, cid, retry.json()["id"])
    assert done["status"] == "done"
    rows = messages(client, cid)
    assert sum(row["role"] == "user" for row in rows) == 1
    assert next(row for row in rows if row["id"] == failed["id"])["status"] == "failed"
    assert len(script.assistant_calls) == 2
    assert client.post(f"/api/conversations/{cid}/messages/{done['id']}/retry",
                       json={"request_id": uuid.uuid4().hex}).status_code == 409


def test_queued_chat_is_interrupted_on_close_and_restart_does_not_call_model(tmp_path):
    script = Script()
    script.block_first = True
    data = tmp_path / "data"
    app = create_app(data, runner_factory=script.factory)
    client = client_for(app)
    client.__enter__()
    errors = []
    closing = None
    try:
        first, second = conversation(client), conversation(client)
        send(client, first)
        assert script.started.wait(3)
        pending = send(client, second)

        def shutdown():
            try:
                client.__exit__(None, None, None)
            except BaseException as error:
                errors.append(error)

        closing = threading.Thread(target=shutdown)
        closing.start()
        assert app.state.jobs.stopping.wait(3)
    finally:
        script.release.set()
        if closing is not None:
            closing.join(10)
            assert not closing.is_alive()
        else:
            client.__exit__(None, None, None)
    assert not errors
    calls = len(script.assistant_calls)
    with client_for(create_app(data, runner_factory=script.factory)) as restored:
        message = next(row for row in messages(restored, second) if row["id"] == pending["id"])
        assert message["status"] == "interrupted"
        assert len(script.assistant_calls) == calls
        assert len(messages(restored, second)) == 2
        retry = restored.post(f"/api/conversations/{second}/messages/{pending['id']}/retry",
                              json={"request_id": uuid.uuid4().hex})
        assert retry.status_code == 202
        assert wait_message(restored, second, retry.json()["id"])["status"] == "done"
        assert len(script.assistant_calls) == calls + 1


def test_conclusions_are_idempotent_persistent_and_exports_stay_product_scoped(tmp_path):
    script = Script()
    data = tmp_path / "data"
    with client_for(create_app(data, runner_factory=script.factory)) as client:
        a, b = project(client), project(client, "产品B")
        global_cid = conversation(client)
        script.reply = {"reply": "GLOBAL_NOTE_ONLY", "reference_ids": [], "actions": []}
        global_reply = chat(client, global_cid)
        global_note = save_note(client, global_cid, global_reply).json()
        assert global_note["project_id"] is None
        assert client.get(f"/api/projects/{a}/documents/{global_note['id']}").status_code == 404
        assert client.get(f"/api/projects/{b}/documents/{global_note['id']}").status_code == 404
        cid = conversation(client, a)
        script.reply = {"reply": "PRODUCT_A_NOTE_ONLY", "reference_ids": [], "actions": []}
        reply = chat(client, cid)
        assert save_note(client, cid, reply, b).status_code == 409
        saved = save_note(client, cid, reply, title="产品讨论结论")
        assert saved.status_code == 201, saved.text
        note = saved.json()
        assert note["kind"] == "note" and note["version"] == 1 and note["project_id"] == a
        assert note["metadata"]["message_id"] == reply["id"]
        repeated = save_note(client, cid, reply, title="重复保存不改原文")
        assert repeated.json() == note
        assert len(script.assistant_calls) == 2
        assert len(client.get("/api/notes").json()["items"]) == 2
        user = next(row for row in messages(client, cid) if row["role"] == "user")
        assert save_note(client, cid, user).status_code == 409
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{a}/export").content)) as archive:
            assert f"notes/{note['id']}.md" in archive.namelist()
            assert f"notes/{global_note['id']}.md" not in archive.namelist()
            assert "PRODUCT_A_NOTE_ONLY" in archive.read(f"notes/{note['id']}.md").decode()
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{b}/export").content)) as archive:
            assert not any(path.startswith("notes/") for path in archive.namelist())
    with client_for(create_app(data, runner_factory=script.factory)) as restored:
        notes = restored.get("/api/notes").json()["items"]
        assert {row["id"] for row in notes} == {note["id"], global_note["id"]}
        assert len(script.assistant_calls) == 2


def test_draft_action_only_runs_explicitly_once_and_requires_existing_prd_confirmation(service):
    client, script = service
    script.reply = proposal("draft")
    a, b = project(client), project(client, "产品B")
    cid = conversation(client, a)
    reply = chat(client, cid, "请把讨论整理为 PRD 草稿")
    assert client.get(f"/api/projects/{a}/jobs").json()["items"] == []
    assert execute(client, cid, reply, b).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        started, duplicate = list(pool.map(lambda _: execute(client, cid, reply), range(2)))
    assert started.status_code == 200, started.text
    job_id = started.json()["result"]["job_id"]
    assert duplicate.status_code == 200 and duplicate.json()["result"]["job_id"] == job_id
    assert execute(client, cid, reply).json()["result"]["job_id"] == job_id
    job = wait_job(client, job_id)
    assert len(client.get(f"/api/projects/{a}/jobs").json()["items"]) == 1
    assert client.get(f"/api/projects/{a}/documents?kind=prd").json()["items"] == []
    draft_id = job["result"]["document_id"]
    assert job["result"]["kind"] == "draft"
    confirmation = client.post(f"/api/projects/{a}/prd/confirm", json={"draft_id": draft_id, "expected_version": 0})
    assert confirmation.status_code == 201 and confirmation.json()["version"] == 1
    action = next(m for m in messages(client, cid) if m["id"] == reply["id"])["actions"][0]
    assert action["result"]["job_status"] == "done" and action["result"]["document_id"] == draft_id


def test_global_action_requires_explicit_target_and_original_prerequisites(service):
    client, script = service
    script.reply = proposal("plan")
    pid = project(client)
    cid = conversation(client)
    reply = chat(client, cid)
    assert execute(client, cid, reply).status_code == 409
    missing = execute(client, cid, reply, pid)
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "PRD_REQUIRED"
    assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []


def test_global_followup_only_receives_its_own_executed_action_and_real_job_status(service):
    client, script = service
    first_product, other_product = project(client), project(client, "其他会话的产品")
    first, other = conversation(client), conversation(client)
    script.reply = proposal("draft")
    script.reply["actions"].append({"type": "plan", "label": "尚未执行的方案建议", "payload": {}})
    reply = chat(client, first, "帮我把这个想法整理为草稿")
    first_action = reply["actions"][0]
    unexecuted_action = reply["actions"][1]
    started = execute(client, first, reply, first_product)
    assert started.status_code == 200, started.text
    first_job = wait_job(client, started.json()["result"]["job_id"])
    other_reply = chat(client, other, "另一个产品的独立讨论")
    other_started = execute(client, other, other_reply, other_product)
    assert other_started.status_code == 200, other_started.text
    other_job = wait_job(client, other_started.json()["result"]["job_id"])
    script.reply = {"reply": "你发起的需求草稿已生成，正式 PRD 仍需你确认。", "reference_ids": [], "actions": []}
    chat(client, first, "刚才生成好了吗？")
    supplied = script.input()
    history = supplied["actions_history"]
    assert len(history) == 1
    own = history[0]
    assert own["action_id"] == first_action["id"] and own["action_status"] == "done"
    assert own["project_id"] == first_product
    assert own["job_id"] == first_job["id"] and own["job_status"] == first_job["status"] == "done"
    assert own["document_id"] == first_job["result"]["document_id"]
    assert own["document_kind"] == "draft" and own["document_version"] == 1
    serialized = json.dumps(history)
    assert unexecuted_action["id"] not in serialized
    assert other_reply["actions"][0]["id"] not in serialized
    assert other_job["id"] not in serialized and other_product not in serialized
    assert supplied["context"]["project"] is None and supplied["context"]["documents"] == []
    # Knowing an artifact exists must not silently confirm it as a formal PRD.
    assert client.get(f"/api/projects/{first_product}/documents?kind=prd").json()["items"] == []


def test_manual_workflow_retry_updates_chat_action_history_without_starting_another_job(service):
    client, script = service
    pid = project(client)
    cid = conversation(client)
    script.reply = proposal("draft")
    script.interview_error = RuntimeError("offline first draft attempt fails")
    reply = chat(client, cid, "整理一份待确认草稿")
    started = execute(client, cid, reply, pid)
    assert started.status_code == 200, started.text
    first = wait_job(client, started.json()["result"]["job_id"], expected="failed")
    script.interview_error = None
    retried = client.post(f"/api/jobs/{first['id']}/retry")
    assert retried.status_code == 202, retried.text
    second = wait_job(client, retried.json()["id"])
    assert second["id"] != first["id"] and second["retry_of"] == first["id"]
    action = next(m for m in messages(client, cid) if m["id"] == reply["id"])["actions"][0]
    assert action["result"]["job_id"] == second["id"]
    assert action["result"]["job_status"] == "done"
    assert action["result"]["document_id"] == second["result"]["document_id"]
    repeated = execute(client, cid, reply, pid)
    assert repeated.status_code == 200 and repeated.json()["result"]["job_id"] == second["id"]
    assert len(client.get(f"/api/projects/{pid}/jobs").json()["items"]) == 2
    script.reply = {"reply": "手动重试生成的草稿已完成，仍需本人确认。", "reference_ids": [], "actions": []}
    chat(client, cid, "我在生成记录里重试过了，现在好了么？")
    history = script.input()["actions_history"]
    assert len(history) == 1 and history[0]["job_id"] == second["id"]
    assert history[0]["job_status"] == "done"
    assert history[0]["document_id"] == second["result"]["document_id"]
    assert client.get(f"/api/jobs/{first['id']}").json()["status"] == "failed"
    assert client.get(f"/api/projects/{pid}/documents?kind=prd").json()["items"] == []


def test_action_from_old_prd_or_plan_cannot_generate_current_product_work(service):
    client, script = service
    pid = project(client)
    prd(client, pid)
    script.reply = proposal("plan")
    cid = conversation(client, pid)
    old_prd = chat(client, cid)
    prd(client, pid, 1, "# 新需求\n本次已变更")
    rejected = execute(client, cid, old_prd)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "STALE_INPUT"
    first_plan = client.post(f"/api/projects/{pid}/plan")
    assert first_plan.status_code == 202
    wait_job(client, first_plan.json()["id"])
    script.reply = proposal("tasks")
    task_reply = chat(client, cid)
    next_plan = client.post(f"/api/projects/{pid}/plan")
    wait_job(client, next_plan.json()["id"])
    rejected = execute(client, cid, task_reply)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "STALE_INPUT"
    assert client.get(f"/api/projects/{pid}/tasks").json()["items"] == []


def test_prd_change_between_action_validation_and_enqueue_is_rechecked_atomically(service, monkeypatch):
    client, script = service
    pid = project(client)
    prd(client, pid)
    script.reply = proposal("plan")
    cid = conversation(client, pid)
    reply = chat(client, cid)
    original_enqueue = client.app.state.jobs.enqueue
    changed = False

    def update_before_enqueue(project_id, kind, payload=None, retry=None):
        nonlocal changed
        if not changed:
            changed = True
            prd(client, pid, 1, "# 并发编辑的新PRD\n旧动作不能依据新文档执行")
        return original_enqueue(project_id, kind, payload, retry)

    monkeypatch.setattr(client.app.state.jobs, "enqueue", update_before_enqueue)
    rejected = execute(client, cid, reply)
    assert changed
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "STALE_INPUT"
    assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []


def test_private_research_url_is_rejected_before_a_job_or_search_is_started(service):
    client, script = service
    script.reply = proposal("teardown", {"product_name": "测试产品", "product_url": "http://127.0.0.1:8788/"})
    pid = project(client)
    cid = conversation(client, pid)
    reply = wait_message(client, cid, send(client, cid)["id"])
    assert reply["status"] == "failed" and reply["actions"] == []
    assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []


def test_message_and_action_ids_are_bound_to_their_conversation(service):
    client, script = service
    script.reply = proposal("draft")
    pid = project(client)
    a, b = conversation(client, pid), conversation(client, pid)
    reply = chat(client, a)
    assert save_note(client, b, reply).status_code == 404
    assert execute(client, b, reply).status_code == 404
    assert client.post(f"/api/conversations/{b}/messages/{reply['id']}/retry",
                       json={"request_id": uuid.uuid4().hex}).status_code == 404
    assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []


def test_new_routes_preserve_local_guards_and_no_key_is_not_fake_success(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.engine.config", lambda: {
        "key": "", "model": "deepseek-flash", "base_url": "https://api.deepseek.com/anthropic"})
    with client_for(create_app(tmp_path / "data")) as client:
        cid = conversation(client)
        response = client.post(f"/api/conversations/{cid}/messages", json={"message": "产品讨论", "request_id": uuid.uuid4().hex})
        assert response.status_code == 409 and response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert messages(client, cid) == []
        for path in ("/api/conversations", f"/api/conversations/{cid}", "/api/notes"):
            assert client.get(path, headers={"Host": "evil.test"}).status_code == 403
            assert client.get(path, headers={"Origin": "https://evil.test"}).status_code == 403
            assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_history_limit_rejects_new_turn_without_silently_removing_earlier_messages(service):
    client, script = service
    cid = conversation(client)
    for marker in ("甲", "乙", "丙"):
        chat(client, cid, marker * 12000)
    before = messages(client, cid)
    response = client.post(f"/api/conversations/{cid}/messages", json={
        "message": "丁" * 12000, "request_id": uuid.uuid4().hex})
    assert response.status_code == 409 and response.json()["error"]["code"] == "CONVERSATION_LIMIT"
    assert messages(client, cid) == before
    assert len(script.assistant_calls) == 3

# Simin Lab capability integration: real store/jobs with an isolated injected model.
@pytest.mark.parametrize('method', ['aipm-problem-definition','aipm-prioritization-scope','aipm-prd-writing-review'])
def test_methods_load_complete_registered_materials_without_changing_free_discussion(service, method):
    client, script = service
    cid = conversation(client)
    response = client.post(f'/api/conversations/{cid}/messages', json={
        'message': '用这个方法讨论当前问题', 'request_id': uuid.uuid4().hex, 'skill_id': method})
    assert response.status_code == 202
    assert wait_message(client,cid,response.json()['id'])['status']=='done'
    assert script.input()['skill_id']==method
    assert 'references/' in script.input()['method'] and len(script.input()['method'])>2500
    assert next(m for m in messages(client,cid) if m['role']=='user')['skill_id']==method
    chat(client,cid,'继续自由讨论')
    assert script.input()['method']==''


def test_unknown_method_and_incorrect_manual_reference_never_enqueue(service):
    client,script=service
    a,b=project(client),project(client,'另一个产品')
    document=prd(client,a,text='# 文档\n完整且真实的原始段落。')
    cid=conversation(client,a)
    def request(**extras):
        return client.post(f'/api/conversations/{cid}/messages',json={'message':'分析资料','request_id':uuid.uuid4().hex,**extras})
    assert request(skill_id='untrusted-method').status_code==422
    assert request(attachments=[{'document_id':document['id'],'quote':'模型编造的段落'}]).status_code==422
    other=prd(client,b)
    assert request(attachments=[{'document_id':other['id']}]).status_code==404
    assert messages(client,cid)==[] and script.assistant_calls==[]


def test_manual_reference_sends_exact_verified_excerpt_and_retains_source_version(service):
    client,script=service
    pid=project(client)
    document=prd(client,pid,text='# 来源\n正确的引用片段\n其余正文')
    cid=conversation(client,pid)
    response=client.post(f'/api/conversations/{cid}/messages',json={
        'message':'只分析我选中的段落','request_id':uuid.uuid4().hex,
        'attachments':[{'document_id':document['id'],'quote':'正确的引用片段'}]})
    assert response.status_code==202
    assert wait_message(client,cid,response.json()['id'])['status']=='done'
    selected=next(d for d in script.input()['context']['documents'] if d['reference']['id']==document['id'])
    assert selected['content']=='正确的引用片段' and selected['truncated']
    assert selected['reference']['version']==1 and selected['reference']['selection']
    user=next(m for m in messages(client,cid) if m['role']=='user')
    assert user['attachments'][0]['quote']=='正确的引用片段'


def proposed_change(client,script,pid,cid,document,content='# 修改版\n新增已讨论的要求'):
    script.reply={'reply':'提出修改供你审阅。','actions':[],'reference_ids':[document['id']],
                  'document_changes':[{'document_id':document['id'],'content':content,'reason':'按本轮要求补充验收条件'}]}
    reply=chat(client,cid,'请修改这份文档')
    return reply, reply['proposals'][0]


def test_document_change_waits_for_review_keeps_old_version_and_is_idempotent(service):
    client,script=service
    pid=project(client);original=prd(client,pid);cid=conversation(client,pid)
    reply,proposal=proposed_change(client,script,pid,cid,original)
    assert client.get(f'/api/projects/{pid}/documents?kind=prd').json()['items'][0]['id']==original['id']
    detail=client.get('/api/proposals/'+proposal['id']).json()
    assert detail['before']==original['content'] and any(line.startswith('+新增') for line in detail['diff'])
    approved=client.post('/api/proposals/'+proposal['id']+'/decide',json={'allow':True})
    assert approved.status_code==200 and approved.json()['status']=='saved'
    docs=client.get(f'/api/projects/{pid}/documents?kind=prd').json()['items']
    assert len(docs)==2 and docs[0]['version']==2 and docs[1]['content']==original['content']
    assert client.post('/api/proposals/'+proposal['id']+'/decide',json={'allow':True}).json()['result_id']==approved.json()['result_id']
    assert len(client.get(f'/api/projects/{pid}/documents?kind=prd').json()['items'])==2
    assert client.post('/api/proposals/'+proposal['id']+'/decide',json={'allow':False}).status_code==409


def test_rejected_and_conflicting_proposals_cannot_modify_current_document(service):
    client,script=service
    pid=project(client);original=prd(client,pid);cid=conversation(client,pid)
    _,proposal=proposed_change(client,script,pid,cid,original)
    assert client.post('/api/proposals/'+proposal['id']+'/decide',json={'allow':False}).json()['status']=='rejected'
    _,pending=proposed_change(client,script,pid,cid,original)
    latest=prd(client,pid,1,'# 人工新版本\n不能覆盖')
    result=client.post('/api/proposals/'+pending['id']+'/decide',json={'allow':True})
    assert result.status_code==409
    docs=client.get(f'/api/projects/{pid}/documents?kind=prd').json()['items']
    assert len(docs)==2 and docs[0]['id']==latest['id']


def test_partial_document_is_not_accepted_as_replacement_source(service):
    client,script=service
    pid=project(client);document=prd(client,pid,text='# 超长PRD\n'+'内容'*12000);cid=conversation(client,pid)
    script.reply={'reply':'修改','reference_ids':[],'actions':[],
                  'document_changes':[{'document_id':document['id'],'content':'# 不完整的替换','reason':'修改'}]}
    reply=wait_message(client,cid,send(client,cid,'改一下文档')['id'])
    assert reply['status']=='failed' and reply['proposals']==[]
    assert len(client.get(f'/api/projects/{pid}/documents').json()['items'])==1


def test_search_finds_document_body_conversation_note_tasks_and_literal_sql_characters(service):
    client,script=service
    pid=project(client);prd(client,pid,text='# 无关标题\n只有正文拥有「检索针_100%」')
    cid=conversation(client,pid)
    script.reply={'reply':'对话独有标记：针叶松','actions':[],'reference_ids':[]}
    reply=chat(client,cid,'历史用户正文：长颈鹿')
    note=save_note(client,cid,reply).json()
    hits=client.get('/api/search',params={'q':'检索针_100%'}).json()['items']
    assert len(hits)==1 and hits[0]['type']=='document' and '检索针_100%' in hits[0]['snippet']
    assert any(h['type']=='conversation' and h['message_id'] for h in client.get('/api/search?q=长颈鹿').json()['items'])
    assert any(h['type']=='note' and h['id']==note['id'] for h in client.get('/api/search?q=针叶松').json()['items'])
    assert client.get('/api/search',params={'q':"' OR 1=1 --"}).json()['items']==[]
    other=project(client,'隔离产品')
    assert client.get('/api/search',params={'q':'检索针','project_id':other}).json()['items']==[]


def test_trash_project_hides_all_related_resources_and_restore_preserves_versions(tmp_path):
    script=Script();root=tmp_path/'data'
    with client_for(create_app(root,runner_factory=script.factory)) as client:
        pid=project(client);document=prd(client,pid,text='# 本文档\n项目可恢复标记');cid=conversation(client,pid)
        reply=chat(client,cid);note=save_note(client,cid,reply).json()
        body={'type':'project','item_id':pid}
        assert client.post('/api/trash',json=body).status_code==200
        assert client.get('/api/projects').json()['items']==[]
        assert client.get('/api/conversations').json()['items']==[]
        assert client.get('/api/notes').json()['items']==[]
        assert client.get('/api/search?q=可恢复标记').json()['items']==[]
        assert client.get(f'/api/projects/{pid}/documents/{document["id"]}').status_code==404
        assert client.get(f'/api/conversations/{cid}').status_code==404
        assert client.post(f'/api/projects/{pid}/plan').status_code==404
    with client_for(create_app(root,runner_factory=script.factory)) as client:
        assert len(client.get('/api/trash').json()['items'])==1
        assert client.post('/api/trash/restore',json=body).status_code==200
        assert client.get(f'/api/projects/{pid}/documents/{document["id"]}').json()['content']==document['content']
        assert len(messages(client,cid))==2
        assert client.get('/api/notes').json()['items'][0]['id']==note['id']


def test_document_trash_does_not_promote_old_prd_and_new_versions_stay_monotonic(service):
    client,script=service
    pid=project(client);old=prd(client,pid);new=prd(client,pid,1,'# 新版\n第二版内容')
    body={'type':'document','item_id':new['id']}
    assert client.post('/api/trash',json=body).status_code==200
    assert client.get(f'/api/projects/{pid}').json()['documents']['prd'] is None
    docs=client.get(f'/api/projects/{pid}/documents').json()['items']
    assert len(docs)==1 and docs[0]['id']==old['id'] and docs[0]['current'] is False
    assert client.get(f'/api/projects/{pid}/documents/{new["id"]}').status_code==404
    next_doc=prd(client,pid,0,'# 第三版\n明确重新创建')
    assert next_doc['version']==3
    assert client.post('/api/trash/restore',json=body).status_code==200
    assert client.get(f'/api/projects/{pid}').json()['documents']['prd']['id']==next_doc['id']


def test_dependency_and_running_chat_prevent_unsafe_removal(service):
    client,script=service
    pid=project(client);document=prd(client,pid)
    plan=client.post(f'/api/projects/{pid}/plan');wait_job(client,plan.json()['id'])
    assert client.post('/api/trash',json={'type':'document','item_id':document['id']}).status_code==409
    script.block_first=True
    cid=conversation(client,pid);pending=send(client,cid)
    try:
        assert script.started.wait(3)
        assert client.post('/api/trash',json={'type':'project','item_id':pid}).status_code==409
        assert client.post('/api/trash',json={'type':'conversation','item_id':cid}).status_code==409
    finally: script.release.set()
    assert wait_message(client,cid,pending['id'])['status']=='done'


def test_notes_and_conversations_can_be_recovered_independently(service):
    client,script=service
    cid=conversation(client);reply=chat(client,cid,'稀有对话正文');note=save_note(client,cid,reply).json()
    for kind,identity in [('note',note['id']),('conversation',cid)]:
        body={'type':kind,'item_id':identity}
        assert client.post('/api/trash',json=body).status_code==200
        assert client.post('/api/trash',json=body).status_code==200
        if kind=='note':
            assert client.get('/api/notes').json()['items']==[]
            assert client.get('/api/notes/'+identity).status_code==404
        else:
            assert client.get('/api/conversations').json()['items']==[]
            assert client.get('/api/search?q=稀有对话正文').json()['items']==[]
        assert client.post('/api/trash/restore',json=body).status_code==200
    assert len(messages(client,cid))==2 and len(client.get('/api/notes').json()['items'])==1


def test_new_workspace_routes_keep_same_origin_guard(service):
    client,_=service
    for path in ['/api/search?q=x','/api/trash','/api/proposals/no-such-id','/api/notes/no-such-id']:
        assert client.get(path,headers={'Origin':'https://untrusted.example'}).status_code==403


def test_manual_task_reference_keeps_stale_plan_warning(service):
    client,script=service
    pid=project(client);prd(client,pid)
    wait_job(client,client.post(f'/api/projects/{pid}/plan').json()['id'])
    task_job=wait_job(client,client.post(f'/api/projects/{pid}/tasks/generate').json()['id'])
    wait_job(client,client.post(f'/api/projects/{pid}/plan').json()['id'])
    cid=conversation(client,pid)
    response=client.post(f'/api/conversations/{cid}/messages',json={'message':'查看任务状态','request_id':uuid.uuid4().hex,
                         'attachments':[{'document_id':task_job['result']['document_id']}]})
    assert response.status_code==202
    assert wait_message(client,cid,response.json()['id'])['status']=='done'
    assert next(d for d in script.input()['context']['documents'] if d['reference']['kind']=='tasks')['stale'] is True


def test_retry_does_not_send_removed_reference_to_model(service):
    client,script=service
    pid=project(client);doc=prd(client,pid);cid=conversation(client,pid)
    script.reply=RuntimeError('synthetic failure')
    failed=wait_message(client,cid,send(client,cid)['id'])
    assert failed['status']=='failed'
    # Removing another non-versioned reference invalidates its saved retry snapshot too.
    script.reply={'reply':'待移除的结论','actions':[],'reference_ids':[]}
    other=chat(client,cid,'保存一句结论')
    note=save_note(client,cid,other).json()
    script.reply=RuntimeError('synthetic failure')
    failed=wait_message(client,cid,send(client,cid)['id'])
    assert client.post('/api/trash',json={'type':'note','item_id':note['id']}).status_code==200
    count=len(script.assistant_calls)
    retry=client.post(f'/api/conversations/{cid}/messages/{failed["id"]}/retry',json={'request_id':uuid.uuid4().hex})
    assert retry.status_code==409 and retry.json()['error']['code']=='REFERENCE_TRASHED'
    assert len(script.assistant_calls)==count


def test_followup_sees_own_review_outcome_without_other_conversation_changes(service):
    client,script=service
    pid=project(client);doc=prd(client,pid);cid=conversation(client,pid)
    _,proposal=proposed_change(client,script,pid,cid,doc)
    assert client.post('/api/proposals/'+proposal['id']+'/decide',json={'allow':True}).status_code==200
    latest=client.get(f'/api/projects/{pid}/documents?kind=prd').json()['items'][0]
    other=conversation(client,pid)
    _,other_proposal=proposed_change(client,script,pid,other,latest,'# 另一个会话的建议')
    script.reply={'reply':'已确认保存 PRD V2。','actions':[],'reference_ids':[]}
    chat(client,cid,'刚才的修改保存了吗')
    reviews=script.input()['review_history']
    assert len(reviews)==1 and reviews[0]['id']==proposal['id']
    assert reviews[0]['status']=='saved' and reviews[0]['saved_version']==2
    assert other_proposal['id'] not in json.dumps(reviews)


def test_global_conversation_with_queued_action_cannot_be_removed(service):
    client,script=service
    pid=project(client);cid=conversation(client)
    script.reply=proposal('draft');reply=chat(client,cid)
    started=threading.Event();release=threading.Event()
    def occupy_queue():
        started.set();assert release.wait(8)
    blocker=client.app.state.jobs.pool.submit(occupy_queue)
    try:
        assert started.wait(3)
        action=execute(client,cid,reply,pid)
        assert action.status_code==200
        assert client.post('/api/trash',json={'type':'conversation','item_id':cid}).status_code==409
    finally:
        release.set();blocker.result(timeout=8)
    wait_job(client,action.json()['result']['job_id'])
