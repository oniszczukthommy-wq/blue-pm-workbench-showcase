"""真实模型技术验收；只操作明确标记的验收样例，绝不代替本人验收业务质量。

前置：data/real-smoke.json 中有本轮创建的样例产品及访谈 job_id。
再次执行会复用 manifest 里已记录的任务，不会自动重试失败任务。
"""
import io
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(os.environ.get("WORKBENCH_SMOKE_MANIFEST", ROOT / "data/real-smoke.json"))
EVIDENCE = Path(os.environ.get("WORKBENCH_SMOKE_EVIDENCE_DIR", ROOT / "docs/workbench/真实验收"))


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    state = json.loads(MANIFEST.read_text())
    base, pid = state["base_url"], state["project_id"]
    key = dotenv_values(ROOT / ".env").get("ANTHROPIC_API_KEY", "")

    def save_state():
        MANIFEST.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def request(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            assert not key or key.encode() not in raw, "响应中发现敏感配置，未保存响应"
            return raw if r.headers.get_content_type() == "application/zip" else json.loads(raw)

    def wait_job(job_id):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            job = request("/api/jobs/" + job_id)
            if job["status"] == "done":
                return job
            if job["status"] not in ("queued", "running"):
                raise RuntimeError("任务未完成：" + str(job["error"]))
            time.sleep(3)
        raise RuntimeError("任务等待超时；任务仍保留在工作台，稍后查看，不自动重试")

    def document(job, filename):
        doc = request(f"/api/projects/{pid}/documents/" + job["result"]["document_id"])
        (EVIDENCE / filename).write_text(doc["content"], encoding="utf-8")
        return doc

    interview = wait_job(state["interview_job_id"])
    draft = document(interview, "01-需求草稿.md")
    assert len(draft["metadata"]["questions"]) <= 5
    if "prd_id" not in state:
        prd = request(f"/api/projects/{pid}/prd/confirm", {"draft_id": draft["id"], "expected_version": 0,
                      "change_note": "技术验收样例：仅验证确认接口，不代表本人已验收业务质量"})
        state["prd_id"] = prd["id"]
        save_state()
    if "plan_job_id" not in state:
        state["plan_job_id"] = request(f"/api/projects/{pid}/plan", {})["id"]
        save_state()
    plan_job = wait_job(state["plan_job_id"])
    plan = document(plan_job, "02-技术方案.md")
    assert plan["prd_id"] == state["prd_id"]
    if "tasks_job_id" not in state:
        state["tasks_job_id"] = request(f"/api/projects/{pid}/tasks/generate", {})["id"]
        save_state()
    tasks_job = wait_job(state["tasks_job_id"])
    tasks = document(tasks_job, "03-开发任务.md")
    rows = request(f"/api/projects/{pid}/tasks")
    assert not rows["stale"] and 1 <= len(rows["items"]) <= 40
    task = rows["items"][0]
    req = urllib.request.Request(base + f"/api/projects/{pid}/tasks/{task['id']}",
        data=json.dumps({"status": "doing"}).encode(), method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert json.load(r)["status"] == "doing"
    exported = request(f"/api/projects/{pid}/export")
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        for name in archive.namelist():
            assert ".env" not in name and ".sqlite" not in name
            assert not key or key.encode() not in archive.read(name), "导出中发现敏感配置"
        assert {"prd/V1.md", "plan/V1.md", "tasks/V1.md"} <= set(archive.namelist())
    (EVIDENCE / "样例文档包.zip").write_bytes(exported)
    records = request(f"/api/projects/{pid}/jobs")
    evidence = {"status": "technical_flow_passed", "project_id": pid, "model": request('/api/model')["model"],
                "real_model": True, "interview": {"job_id": interview["id"], "status": interview["status"],
                    "questions": draft["metadata"]["questions"], "ready": draft["metadata"]["ready"]},
                "plan": {"job_id": plan_job["id"], "status": plan_job["status"], "prd_id": plan["prd_id"]},
                "tasks": {"job_id": tasks_job["id"], "status": tasks_job["status"], "count": len(rows["items"])},
                "usage": records["usage"], "export_and_key_exclusion": "passed", "human_quality_review": "pending"}
    evidence['runtime'] = request('/api/model').get('runtime', 'legacy-direct')
    evidence['runtime_version'] = request('/api/model').get('runtime_version')
    evidence['jobs'] = [{k:job[k] for k in ('id','kind','status','usage')} for job in records['items']]
    (EVIDENCE / "结果.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 只记录本脚本的失败类型；不输出网络请求、密钥和供应商原始响应。
        print(json.dumps({"status": "not_passed", "error_type": type(exc).__name__}, ensure_ascii=False))
        raise SystemExit(1) from None
