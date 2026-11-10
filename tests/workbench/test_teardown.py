import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.models import TeardownResult
from backend.store import Problem
from backend.teardown import validate_report
from blueprint.mock import MockRunner


def result():
    paragraph = "【页面事实】[产品说明](https://example.com/product)展示输入与输出。" + "【合理推断】可能帮助用户缩短整理流程。" + "【尚未确认】各类人群占比没有公开证据。"
    return {"content": "# 示例产品拆解\n\n" + "\n\n".join(f"## {i}. {title}\n\n{paragraph}" for i, title in enumerate(
        ["概览", "业务矩阵", "用户层", "技术层", "模型层与基础层", "市场层", "三个 PM 洞察"])),
        "matrix": [{"label": title, "value": "【合理推断】任务辅助", "tone": tone} for title, tone in [("核心业务", "blue"), ("技术边界", "purple")]],
        "journey": [{"stage": stage, "user": "【合理推断】输入任务", "system": "【合理推断】反馈结果", "checkpoint": "【尚未确认】确认机制", "exception": "【尚未确认】失败处理"} for stage in ["入口", "输入", "过程", "输出", "交付"]],
        "comparison": [{"label": label, "product": "【合理推断】工具辅助", "alternatives": "【合理推断】手动整理"} for label in ["操作方式", "交付方式"]],
        "insights": [{"title": title, "body": "【合理推断】围绕具体任务组织交付", "tone": "blue"} for title in ["任务", "反馈", "确认"]]}


def evidence(payload, directory, stop, log):
    data = {"sources": [{"id": "S1", "title": "产品说明", "url": "https://example.com/product", "channel": "supplementary", "snippet": "公开说明", "content": "公开正文", "source_type": "fetched_page"}],
            "channels": [{"id": "sspai", "name": "少数派", "status": "no_results", "query": "示例产品 site:sspai.com", "source_count": 0}],
            "retrieval_usage": {"requests": 8, "cache_hits": 0}}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "research-summary.json").write_text(json.dumps(data))
    log("公开搜证测试完成")
    return data


class Runner(MockRunner):
    prompts = []

    def complete(self, stage, system, prompt, schema):
        assert stage == "teardown"
        self.prompts.append((system, prompt))
        return result()


def wait(client, response):
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    for _ in range(500):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(.01)
    pytest.fail("job did not finish")


def project(client):
    return client.post("/api/projects", json={"name": "私有产品", "idea": "不要向公开搜索发送的私有产品想法"}).json()["id"]


def test_teardown_without_prd_and_export_and_provenance(tmp_path):
    with TestClient(create_app(tmp_path, Runner, research_factory=evidence), base_url="http://localhost") as client:
        pid = project(client)
        body = {"product_name": "示例产品", "product_url": "https://example.com/product", "depth": "standard"}
        job = wait(client, client.post(f"/api/projects/{pid}/teardown", json=body))
        assert job["status"] == "done", job
        doc = client.get(f"/api/projects/{pid}/documents?kind=teardown").json()["items"][0]
        assert doc["prd_id"] is None and doc["version"] == 1
        assert doc["metadata"]["skill_id"] == "ai-product-teardown"
        assert len(doc["metadata"]["skill_sha256"]) == 64
        assert doc["metadata"]["sources"][0]["source_type"] == "fetched_page"
        assert job["usage"]["retrieval"]["requests"] == 8
        assert client.get(f"/api/jobs/{job['id']}/research").json()["sources"][0]["id"] == "S1"
        html = client.get(f"/api/projects/{pid}/documents/{doc['id']}/report.html")
        assert html.status_code == 200 and "text/html" in html.headers["content-type"]
        assert "attachment" in html.headers["content-disposition"]
        assert "default-src 'none'" in html.headers["content-security-policy"]
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{pid}/export").content)) as archive:
            assert {"teardown/V1.md", "teardown/V1.json", "teardown/V1.html"} <= set(archive.namelist())
        assert client.get(f"/api/projects/{pid}").json()["documents"]["teardown"]["id"] == doc["id"]
        assert "不要向公开搜索" not in Runner.prompts[-1][1]
        assert "方法论" in Runner.prompts[-1][0]
        other = project(client)
        assert client.get(f"/api/projects/{other}/documents/{doc['id']}/report.html").status_code == 404


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost:8788", "http://127.0.0.1", "http://10.1.2.3", "https://user:password@example.com"])
def test_teardown_input_rejects_nonpublic_url(tmp_path, url):
    def never(*args):
        pytest.fail("invalid input must not search")
    with TestClient(create_app(tmp_path, Runner, research_factory=never), base_url="http://localhost") as client:
        pid = project(client)
        assert client.post(f"/api/projects/{pid}/teardown", json={"product_name": "测试", "product_url": url}).status_code == 422
        assert client.get(f"/api/projects/{pid}/jobs").json()["items"] == []


def test_no_sources_does_not_generate_and_keeps_research(tmp_path):
    def no_evidence(payload, directory, stop, log):
        data = evidence(payload, directory, stop, log)
        data["sources"] = []
        (directory / "research-summary.json").write_text(json.dumps(data))
        return data
    def no_runner():
        runner = Runner()
        runner.complete = lambda *args: pytest.fail("no evidence must not call the model")
        return runner
    with TestClient(create_app(tmp_path, no_runner, research_factory=no_evidence), base_url="http://localhost") as client:
        pid = project(client)
        job = wait(client, client.post(f"/api/projects/{pid}/teardown", json={"product_name": "测试"}))
        assert job["status"] == "failed" and "没有取得可用证据" in job["error"]
        assert job["usage"]["retrieval"]["requests"] == 8
        assert client.get(f"/api/projects/{pid}/documents").json()["items"] == []


def test_retry_reuses_cache_and_is_independent_from_prd(tmp_path):
    attempts = []
    class FailsOnce(Runner):
        def complete(self, *args):
            if len(attempts) == 1:
                raise RuntimeError("synthetic failure")
            return super().complete(*args)
    def record(*args):
        attempts.append(args[1])
        data = evidence(*args)
        if len(attempts) > 1:
            data["retrieval_usage"].update(requests=0, cache_hits=8)
            (args[1] / "research-summary.json").write_text(json.dumps(data))
        return data
    with TestClient(create_app(tmp_path, FailsOnce, research_factory=record), base_url="http://localhost") as client:
        pid = project(client)
        first = wait(client, client.post(f"/api/projects/{pid}/teardown", json={"product_name": "测试"}))
        assert first["status"] == "failed"
        client.post(f"/api/projects/{pid}/prd", json={"content": "新的私有需求", "expected_version": 0})
        retry = wait(client, client.post(f"/api/jobs/{first['id']}/retry"))
        assert retry["status"] == "done" and attempts[0] == attempts[1]
        assert retry["retry_of"] == first["id"]
        docs = client.get(f"/api/projects/{pid}/documents?kind=teardown").json()["items"]
        assert len(docs) == 1 and docs[0]["version"] == 1
        assert client.get(f"/api/jobs/{first['id']}/research").json()["retrieval_usage"]["requests"] == 8
        assert client.get(f"/api/jobs/{retry['id']}/research").json()["retrieval_usage"]["requests"] == 0
        assert retry["usage"]["retrieval"]["cache_hits"] == 8


def test_abrupt_recovery_keeps_retrieval_usage(tmp_path):
    def fail():
        runner = Runner()
        runner.complete = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic"))
        return runner
    app = create_app(tmp_path, fail, research_factory=evidence)
    with TestClient(app, base_url="http://localhost") as client:
        pid = project(client)
        job = wait(client, client.post(f"/api/projects/{pid}/teardown", json={"product_name": "测试"}))
    with app.state.store.tx() as db:
        db.execute("UPDATE jobs SET status='running',usage='{}' WHERE id=?", (job["id"],))
    with TestClient(create_app(tmp_path, Runner, research_factory=evidence), base_url="http://localhost") as client:
        recovered = client.get(f"/api/jobs/{job['id']}").json()
        assert recovered["status"] == "interrupted"
        assert recovered["usage"]["retrieval"]["requests"] == 8
        assert recovered["usage"]["token_usage_complete"] is False


def test_report_validation_requires_actual_sources_and_labels():
    research = {"sources": [{"url": "https://example.com/product"}]}
    report = TeardownResult.model_validate(result()).model_dump()
    validate_report(report, research)
    report["content"] = report["content"].replace("https://example.com/product", "https://invented.example.com")
    with pytest.raises(Problem, match="报告引用"):
        validate_report(report, research)


@pytest.mark.parametrize("extra", ["<https://unretrieved.com/fake>", "[外部][ref]\n\n[ref]: https://unretrieved.com/fake", "[外部](<https://unretrieved.com/fake>)", "https://unretrieved.com/fake", "www.unretrieved.com/fake"])
def test_all_markdown_url_forms_must_be_collected(extra):
    report = result()
    report["content"] += "\n\n" + extra
    with pytest.raises(Problem, match="报告引用"):
        validate_report(report, {"sources": [{"url": "https://example.com/product"}]})


def test_short_source_ids_expand_to_collected_links_and_unknown_ids_fail():
    report = result()
    report["content"] = report["content"].replace("[产品说明](https://example.com/product)", "[S123]")
    research = {"sources": [{"id": "S123", "url": "https://example.com/product"}]}
    validate_report(report, research)
    assert "[S123](https://example.com/product)" in report["content"]
    report["content"] += " [S999]"
    with pytest.raises(Problem, match="未收集的来源"):
        validate_report(report, research)


def test_ungraded_visual_is_conservatively_unknown():
    report = result()
    report["journey"][0]["user"] = "打开产品页面"
    validate_report(report, {"sources": [{"url": "https://example.com/product"}]})
    assert report["journey"][0]["user"] == "【尚未确认】打开产品页面"
