"""Public retrieval contract tests: no real keys, DNS, network or paid calls."""
import json
import os
import stat
import threading

import httpx
import pytest

from backend import research
from backend.engine import GenerationInterrupted


PAYLOAD = {"product_name": "示例产品", "product_url": "https://product.example.com/", "focus": "用户体验", "depth": "standard"}


@pytest.fixture
def provider(monkeypatch):
    requests = []
    handler = {"fn": lambda request: httpx.Response(200, json={"results": []})}
    original_client = httpx.Client

    def dispatch(request):
        requests.append(request)
        return handler["fn"](request)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 5 and kwargs["timeout"].read == 25
        return original_client(transport=httpx.MockTransport(dispatch), **kwargs)

    monkeypatch.setattr(research.httpx, "Client", client)
    monkeypatch.setattr(research, "config", lambda: {"key": "test-only-secret"})
    return requests, handler


def run(tmp_path, payload=None, stop=None, logs=None):
    return research.collect_research(payload or PAYLOAD, tmp_path, stop or threading.Event(), (logs or []).append)


def body(request):
    return json.loads(request.content)


def hit(url, content="", snippet="搜索摘要"):
    return {"url": url, "title": "页面标题", "content": content, "snippet": snippet,
            "summary": "长摘要", "date": "2026-10-28T00:00:00Z", "authority": 2, "rerank_score": 2.2}


@pytest.mark.parametrize("value", [
    "file:///etc/passwd", "ftp://www.deepseek.com/file", "//www.deepseek.com/", "https://user:pass@www.deepseek.com/",
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://0x7f.0.0.1/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://10.1.2.3/", "http://169.254.169.254/", "http://192.168.1.1/",
    "http://224.0.0.1/", "http://[ff00::1]/", "http://[fec0::1]/",
    "http://localhost/", "http://a.local/", "http://corp.internal/", "http://router.home.arpa/", "http://a.test/",
    "http://singlelabel/", "https://good.example.com\\@evil.example.com/", "https://a.example.com:0/", "https://a.example.com:bad/",
    "https://a.example.com/with space", "https://%31%32%37.0.0.1/", "https://bad..example.com/", "https://-bad.example.com/",
])
def test_public_url_rejects_local_or_ambiguous_targets(value):
    assert research.public_url(value) is None


def test_public_url_normalization_and_source_identity():
    assert research.public_url("HTTPS://WWW.DeepSeek.COM.:443/docs?a=1#part") == "https://www.deepseek.com/docs?a=1"
    assert research.public_url("http://8.8.8.8:80/") == "http://8.8.8.8/"
    assert research.public_url("https://[2606:4700:4700::1111]/") == "https://[2606:4700:4700::1111]/"


def test_seven_channels_fixed_contract_filtering_raw_cache_and_fetch(provider, tmp_path):
    calls, handler = provider

    def respond(request):
        request_body = body(request)
        assert request.headers["authorization"] == "Bearer test-only-secret"
        assert request.url.host == "api.deepseek.com"
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "产品页正文", "content": "正文证据"})
        assert set(request_body) == {"query", "count", "need_content"}
        assert request_body["count"] == 3 and request_body["need_content"] is True
        query = request_body["query"]
        if "site:xiaohongshu.com" in query:
            return httpx.Response(200, json={"results": [hit("https://www.xiaohongshu.com/explore/a"),
                                                       hit("https://xiaohongshu.com.evil.example.com/spoof"),
                                                       hit("http://127.0.0.1/private")]})
        if "site:bilibili.com" in query:
            return httpx.Response(200, json={"results": [hit("https://www.bilibili.com/video/1", "搜索接口正文")]*2})
        if "site:product.example.com" in query:
            return httpx.Response(200, json={"results": [hit("https://product.example.com/")]})
        return httpx.Response(200, json={"results": []})

    handler["fn"] = respond
    logs = []
    result = research.collect_research(PAYLOAD, tmp_path, threading.Event(), logs.append)
    assert len(result["channels"]) == 7
    assert [c["status"] for c in result["channels"]] == ["completed", "completed"] + ["no_results"]*5
    assert result["channels"][0]["source_count"] == result["channels"][1]["source_count"] == 1
    assert len(result["sources"]) == 3
    assert all("127.0" not in source["url"] and "spoof" not in source["url"] for source in result["sources"])
    assert result["sources"][0]["source_type"] == "fetched_page"
    assert result["sources"][1]["source_type"] == "search_content"
    assert result["sources"][1]["content"] == "搜索接口正文"
    assert result["sources"][0]["snippet"] == "搜索摘要"
    assert result["retrieval_usage"]["search_requests"] == 8
    assert result["retrieval_usage"]["fetch_requests"] == 2
    assert result["retrieval_usage"]["successful_nonempty_requests"] == 5
    saved = json.loads((tmp_path / "research-summary.json").read_text())
    assert saved == result
    raw = list((tmp_path / "research" / result["input_sha256"]).glob("*.json"))
    assert len(raw) == 10
    assert any(json.loads(path.read_text())["response"].get("results", [{}])[0].get("authority") == 2
               for path in raw if json.loads(path.read_text())["response"].get("results"))
    for path in raw + [tmp_path / "research-summary.json"]:
        # chmod permission bits have no owner/group meaning on Windows.
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "test-only-secret" not in path.read_text()
    assert "公开搜证完成" in logs[-1]
    assert all(request.method == "POST" for request in calls)


def test_retry_reuses_success_and_empty_cache_without_new_http(provider, tmp_path):
    calls, handler = provider
    handler["fn"] = lambda request: httpx.Response(200, json={"title": "页面", "content": "产品页内容"} if request.url.path.endswith("/fetch") else {"results": []})
    first = run(tmp_path)
    count = len(calls)
    second = run(tmp_path)
    assert count == 9 and len(calls) == count
    assert second["retrieval_usage"]["requests"] == 0
    assert second["retrieval_usage"]["cache_hits"] == 9
    assert second["retrieval_usage"]["cached_nonempty_responses"] == 1
    assert second["retrieval_usage"]["successful_nonempty_requests"] == 0
    assert second["sources"][0]["id"] == first["sources"][0]["id"]
    assert second["sources"][0]["fetched_at"] == first["sources"][0]["fetched_at"]
    run(tmp_path, {**PAYLOAD, "product_name": "另一个产品"})
    assert len(calls) == count * 2


def test_partial_errors_are_safe_and_are_retried_only_explicitly(provider, tmp_path):
    calls, handler = provider

    def respond(request):
        if "site:xiaohongshu.com" in body(request).get("query", ""):
            return httpx.Response(402, json={"error": "unsafe private provider text test-only-secret"})
        if request.url.path.endswith("/fetch"):
            raise httpx.ReadTimeout("request secret test-only-secret")
        return httpx.Response(200, json={"results": []})

    handler["fn"] = respond
    result = run(tmp_path)
    assert len(calls) == 9
    assert result["channels"][0]["status"] == "error"
    assert result["channels"][0]["error"] == "检索服务余额不足"
    assert result["retrieval_usage"]["failed_requests"] == 2
    assert result["sources"] == []
    assert "test-only-secret" not in json.dumps(result)
    assert all("test-only-secret" not in path.read_text() for path in tmp_path.rglob("*.json"))
    second = run(tmp_path)
    assert len(calls) == 11
    assert second["retrieval_usage"]["cache_hits"] == 7
    assert second["retrieval_usage"]["requests"] == 2


def test_cancel_preserves_completed_channels_and_cache(provider, tmp_path):
    calls, handler = provider
    stop = threading.Event()

    def respond(request):
        stop.set()
        return httpx.Response(200, json={"results": []})

    handler["fn"] = respond
    with pytest.raises(GenerationInterrupted):
        run(tmp_path, stop=stop)
    saved = json.loads((tmp_path / "research-summary.json").read_text())
    assert len(calls) == 1
    assert saved["channels"][0]["status"] == "pending"
    assert saved["retrieval_usage"]["interrupted_requests"] == 1
    assert "completed" not in saved


def test_cancel_before_request_never_loads_key_or_calls_http(provider, tmp_path, monkeypatch):
    calls, _ = provider
    def no_key():
        raise AssertionError("must not read config after cancellation")
    monkeypatch.setattr(research, "config", no_key)
    stop = threading.Event()
    stop.set()
    with pytest.raises(GenerationInterrupted):
        run(tmp_path, stop=stop)
    assert calls == []
    assert json.loads((tmp_path / "research-summary.json").read_text())["retrieval_usage"]["requests"] == 0


def test_cancel_between_channels_keeps_completed_request_for_retry(provider, tmp_path):
    calls, handler = provider
    handler["fn"] = lambda request: httpx.Response(200, json={"title": "产品", "content": "内容"} if request.url.path.endswith("/fetch") else {"results": []})
    stop = threading.Event()

    def interrupt_after_first_channel(message):
        if message == "联网搜证：B站":
            stop.set()

    with pytest.raises(GenerationInterrupted):
        research.collect_research(PAYLOAD, tmp_path, stop, interrupt_after_first_channel)
    saved = json.loads((tmp_path / "research-summary.json").read_text())
    assert len(calls) == 1
    assert saved["channels"][0]["status"] == "no_results"
    assert saved["retrieval_usage"]["completed_requests"] == 1
    assert saved["retrieval_usage"]["requests"] == 1
    retried = run(tmp_path)
    assert len(calls) == 9
    assert retried["retrieval_usage"]["requests"] == 8
    assert retried["retrieval_usage"]["cache_hits"] == 1


def test_corrupt_cached_response_is_refetched_instead_of_used(provider, tmp_path):
    calls, handler = provider
    handler["fn"] = lambda request: httpx.Response(200, json={"title": "产品", "content": "内容"} if request.url.path.endswith("/fetch") else {"results": []})
    first = run(tmp_path)
    raw = next((tmp_path / "research" / first["input_sha256"]).glob("*.json"))
    record = json.loads(raw.read_text())
    record["response"] = {"injected": True}
    raw.write_text(json.dumps(record))
    retried = run(tmp_path)
    assert len(calls) == 10
    assert retried["retrieval_usage"]["requests"] == 1
    assert retried["retrieval_usage"]["cache_hits"] == 8


def test_channel_query_length_and_product_input_cannot_override_domains(provider, tmp_path):
    payload = {**PAYLOAD, "product_name": '很长的产品名称' * 60 + ' site:evil.example.com OR -site:x.com', "product_url": ""}
    result = run(tmp_path, payload)
    for channel in result["channels"]:
        assert 1 <= len(channel["query"]) <= 128
        assert "site:evil.example.com" not in channel["query"]
    assert result["sources"] == []
    assert result["supplementary"]["status"] == "no_results"


def test_invalid_input_url_is_never_sent_to_fetch(provider, tmp_path):
    calls, _ = provider
    result = run(tmp_path, {**PAYLOAD, "product_url": "http://169.254.169.254/metadata"})
    assert len(calls) == 8
    assert all(not request.url.path.endswith("/fetch") for request in calls)
    assert result["product_url"] == ""


def test_login_page_remains_search_evidence_and_fetch_budget_is_four(provider, tmp_path):
    calls, handler = provider

    def respond(request):
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "登录", "content": "请先登录后查看内容"})
        query = body(request)["query"]
        host = next((domains[0] for _, _, domains in research.CHANNELS if "site:"+domains[0] in query), "product.example.com")
        return httpx.Response(200, json={"results": [hit("https://www." + host + "/a")]})

    handler["fn"] = respond
    result = run(tmp_path)
    assert result["retrieval_usage"]["fetch_requests"] == 4
    blocked = [source for source in result["sources"] if source["fetch_status"] == "blocked"]
    assert len(blocked) == 3
    assert all(source["source_type"] == "search_summary" for source in blocked)
    assert all(source["content"] == "长摘要" for source in blocked)
    assert all(source["content"] != "请先登录后查看内容" for source in result["sources"])


def test_content_budget_bound_and_invalid_provider_payload(provider, tmp_path):
    _, handler = provider

    def respond(request):
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "", "content": "正文"*8000})
        query = body(request)["query"]
        host = next((domains[0] for _, _, domains in research.CHANNELS if "site:"+domains[0] in query), "product.example.com")
        return httpx.Response(200, json={"results": [hit(f"https://{host}/{i}", "多"*10000) for i in range(3)]})

    handler["fn"] = respond
    result = run(tmp_path)
    assert len(result["sources"]) <= 25
    assert sum(len(source["content"]) for source in result["sources"]) <= 80000
    assert max(len(source["content"]) for source in result["sources"]) <= 6000
    handler["fn"] = lambda request: httpx.Response(200, json={"unexpected": "test-only-secret"})
    bad = run(tmp_path / "bad")
    assert all(channel["status"] == "error" for channel in bad["channels"])
    assert "test-only-secret" not in json.dumps(bad)


def test_provider_redirect_never_forwards_key_or_gets_cached(provider, tmp_path):
    calls, handler = provider
    handler["fn"] = lambda request: httpx.Response(307, headers={"location": "https://evil.example.com/"})
    result = run(tmp_path)
    assert len(calls) == 9  # eight searches plus the validated, explicit product page
    assert all(request.url.host == "api.deepseek.com" for request in calls)
    assert result["retrieval_usage"]["failed_requests"] == 9
    assert result["sources"] == []
    assert not list((tmp_path / "research").rglob("*.json"))
