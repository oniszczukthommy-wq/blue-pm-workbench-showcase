"""DSH 适配层回归：只使用内存假 client 与临时目录，不加载密钥或联网。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest
import yaml

from backend.engine import GenerationInterrupted, StopAwareRunner
from backend.harness import HarnessFailure, HarnessRunner, PINNED_VERSION, PROFILE
from backend.jobs import Jobs
from backend.store import Store, now, uid
from blueprint.agent import supports_agent
from blueprint.forward import run_plan
from blueprint.llm import Budget, BudgetExceeded, StageOutputInvalid
from blueprint.mock import MockRunner


SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string", "minLength": 1}},
}
API_KEY = "test-only-not-a-provider-key"
MODEL = "deepseek-test-model"


def assistant_usage(input_tokens=7, output_tokens=3, **extra):
    return {"type": "assistant/message", "data": {"usage": {
        "inputTokens": input_tokens, "outputTokens": output_tokens, **extra,
    }}}


def result(text='{"answer":"合法输出"}', *, finish_reason="completed", events=None):
    return SimpleNamespace(
        final_response=text,
        finish_reason=finish_reason,
        events=[assistant_usage()] if events is None else events,
        # SDK 的其他字段不能被误当作每次模型调用的 usage。
        usage={"inputTokens": 900000, "outputTokens": 900000},
    )


class FakeHarness:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []
        self.close_calls = 0

    def run(self, prompt, *, session_id):
        payload = json.loads(prompt)
        self.calls.append({"payload": payload, "session_id": session_id})
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response(payload) if callable(response) else response

    def close(self):
        self.close_calls += 1


class BlockingHarness(FakeHarness):
    def __init__(self, stop_event=None):
        super().__init__()
        self.stop_event = stop_event
        self.started = threading.Event()
        self.released = threading.Event()
        self.returned = threading.Event()

    def run(self, prompt, *, session_id):
        self.calls.append({"payload": json.loads(prompt), "session_id": session_id})
        self.started.set()
        if self.stop_event is not None:
            self.stop_event.set()
        if not self.released.wait(timeout=2):
            raise AssertionError("适配器未关闭阻塞 client")
        self.returned.set()
        return result()

    def close(self):
        super().close()
        self.released.set()


def test_cancel_during_lazy_start_does_not_restart_or_submit_model(tmp_path):
    stop = threading.Event()
    class SlowStart(FakeHarness):
        def start_session(self, session_id):
            stop.set()
            # 模拟 native runtime 正在初始化，close 必须等它结束再关闭。
            threading.Event().wait(.15)
            return SimpleNamespace(run=lambda prompt: self.calls.append(prompt))
    fake = SlowStart()
    runner = HarnessRunner(api_key=API_KEY, model=MODEL, root=tmp_path,
                           stop_event=stop, harness_factory=lambda **kwargs: fake)
    with pytest.raises(GenerationInterrupted):
        runner.complete('interview', 'system', 'prompt', SCHEMA)
    assert fake.calls == []
    assert fake.close_calls >= 1
    assert runner.token_usage_complete is False


@pytest.fixture(autouse=True)
def forbid_real_model_and_environment_files(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("harness 测试不得联网或加载本机模型凭据")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("backend.engine.config", forbidden)
    monkeypatch.setattr("blueprint.llm.load_config", forbidden)


@pytest.fixture
def make_runner(tmp_path):
    clients = []

    def make(responses=(), *, client=None, **options):
        client = client if client is not None else FakeHarness(responses)
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return client

        runner = HarnessRunner(
            api_key=API_KEY, model=MODEL, root=tmp_path / f"runtime-{len(clients)}",
            harness_factory=factory, **options,
        )
        clients.append(client)
        return runner, client, captured

    return make


def complete(runner):
    return runner.complete("document", "固定系统规则", "仅用于测试的任务输入", SCHEMA)


def test_factory_receives_pinned_profile_and_isolated_runtime_paths(make_runner):
    runner, client, options = make_runner()
    workspace = runner.root / "workspace"
    assert runner.client is client
    assert options["profile"] == "sdk-minimal"
    assert options["patches"] == (str(PROFILE),)
    assert Path(options["dsh_home"]) == runner.root / "home"
    assert Path(options["cwd"]) == workspace
    assert Path(options["runtime_cwd"]) == workspace
    assert workspace.is_dir()
    # Windows access control uses inherited ACLs, not POSIX mode bits.
    if os.name != "nt":
        assert runner.root.stat().st_mode & 0o077 == 0
        assert workspace.stat().st_mode & 0o077 == 0
    assert options["provider"] == "deepseek-official"
    assert options["model"] == MODEL
    assert options["api_key"] == API_KEY
    assert options["base_url"] == "https://api.deepseek.com/anthropic"
    assert options["max_tokens"] == 16384
    assert options["initialize_timeout_seconds"] == 30
    assert options["request_timeout_seconds"] == 180
    assert options["shutdown_timeout_seconds"] == 2
    assert runner.name == f"deepseek-harness-sdk@{PINNED_VERSION}"
    assert runner.profile_hash == hashlib.sha256(PROFILE.read_bytes()).hexdigest()


def test_document_research_can_disable_thinking_without_increasing_token_limit(make_runner):
    runner, client, options = make_runner(reasoning_effort="off")
    assert options["reasoning_effort"] == "off"
    assert options["max_tokens"] == 16384
    assert runner.reasoning_effort == "off"


def test_close_reclaims_native_package_cache_but_keeps_session_evidence(make_runner):
    runner, client, _ = make_runner()
    cache = runner.root / ".cache/pkg/unpacked"
    cache.mkdir(parents=True)
    (cache / "binary").write_text("synthetic package cache")
    evidence = runner.root / "home/sessions/evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("synthetic session evidence")
    runner.close()
    assert not cache.exists()
    assert evidence.read_text() == "synthetic session evidence"
    assert client.close_calls == 1


def test_runtime_environment_clears_unrelated_credentials_and_inherited_code(make_runner, monkeypatch, tmp_path):
    inherited = (
        "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY", "HTTP_PROXY", "PYTHONPATH",
    )
    for name in inherited:
        monkeypatch.setenv(name, f"test-only-inherited-{name}")
    monkeypatch.setenv("HOME", str(tmp_path / "parent-home"))
    monkeypatch.setenv("PATH", "/test-only/parent/bin")
    allowed = {name: str(tmp_path / name.lower())
               for name in ("TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")}
    for name, value in allowed.items():
        monkeypatch.setenv(name, value)
    runner, _, options = make_runner()
    env = options["env"]
    assert all(env.get(name, "") == "" for name in inherited)
    assert all(f"test-only-inherited-{name}" not in env.values() for name in inherited)
    assert env["HOME"] == str(runner.root)
    assert env["PATH"] == os.defpath
    assert env["TZ"] == "Asia/Shanghai"
    assert {name: env[name] for name in allowed} == allowed


def test_profile_removes_execution_tools_and_extra_reporting():
    plugins = {entry["id"]: entry for entry in yaml.safe_load(PROFILE.read_text(encoding="utf-8"))}
    for name in (
        "plugin-package-inventory-deepseek", "persistent-bash", "persistent-pwsh",
        "terminal-bash", "terminal-pwsh", "tool-fs", "tool-working-directory",
        "mcp-resources", "compaction-basic", "image-offload", "llm-retry",
    ):
        assert plugins[name]["disabled"] is True
    assert plugins["session-log-deepseek"]["config"]["enabled"] is False
    assert plugins["sandbox-policy"]["config"]["mode"] == "read-only"


def test_complete_preserves_instructions_schema_and_input_boundaries(make_runner):
    runner, client, _ = make_runner([result()])
    assert complete(runner) == {"answer": "合法输出"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["payload"]["stage"] == "document"
    assert call["payload"]["instructions"] == "固定系统规则"
    assert call["payload"]["task_input"] == "仅用于测试的任务输入"
    assert call["payload"]["output_schema"] == SCHEMA
    assert call["payload"]["corrections"] == []
    assert runner.sessions == [call["session_id"]]
    assert call["session_id"].startswith("pm-")
    assert runner.budget.summary()["calls"] == 1


def test_schema_failure_gets_one_repair_in_a_new_session(make_runner):
    runner, client, _ = make_runner([result("{}"), result()])
    assert complete(runner) == {"answer": "合法输出"}
    assert len(client.calls) == 2
    assert client.calls[0]["payload"]["corrections"] == []
    assert any("answer" in error for error in client.calls[1]["payload"]["corrections"])
    assert len(set(runner.sessions)) == 2
    assert len(runner.attempts) == 1
    assert runner.budget.calls == 2


@pytest.mark.parametrize("invalid", ["not JSON", '{"answer":4}'])
def test_two_invalid_outputs_fail_without_a_third_attempt(make_runner, invalid):
    runner, client, _ = make_runner([result(invalid), result(invalid), result()])
    with pytest.raises(StageOutputInvalid):
        complete(runner)
    assert len(client.calls) == len(runner.sessions) == 2
    assert len(runner.attempts) == 2
    assert all(attempt["errors"] for attempt in runner.attempts)
    assert runner.budget.calls == 2


def test_max_tokens_retries_even_if_the_partial_response_looks_valid(make_runner):
    runner, client, _ = make_runner([result(finish_reason="max-tokens"), result()])
    assert complete(runner) == {"answer": "合法输出"}
    assert len(client.calls) == 2
    assert any("截断" in error for error in client.calls[1]["payload"]["corrections"])
    assert runner.attempts[0]["stop_reason"] == "max-tokens"
    assert runner.budget.calls == 2


def test_usage_comes_only_from_assistant_message_data(make_runner):
    events = [
        {"type": "step/end", "data": {"usage": {"inputTokens": 10000, "outputTokens": 10000}}},
        {"type": "assistant/message", "usage": {"inputTokens": 20000},
         "data": {"usage": {"inputTokens": 11, "outputTokens": 5, "reasoningTokens": 4}}},
    ]
    runner, _, _ = make_runner([result(events=events)])
    complete(runner)
    assert runner.budget.calls == 1
    assert runner.budget.input_tokens == 11
    assert runner.budget.output_tokens == 5
    assert runner.budget.history == [{"stage": "document", "input_tokens": 11, "output_tokens": 5}]


def test_cache_tokens_and_last_attempt_usage_are_counted_once(make_runner):
    events = [
        {"type": "assistant/attempt", "data": {"stream": [
            {"type": "chunk", "chunk": {"type": "usage", "usage": {
                "inputTokens": 2, "outputTokens": 1, "cacheReadTokens": 4,
            }}},
            {"type": "chunk", "chunk": {"type": "text", "text": "ignored"}},
            {"type": "chunk", "chunk": {"type": "usage", "usage": {
                "inputTokens": 2, "outputTokens": 6, "cacheReadTokens": 4,
                "cacheWriteTokens": 3, "reasoningTokens": 5,
            }}},
        ]}},
        assistant_usage(7, 10, cacheReadTokens=11, cacheWriteTokens=13, reasoningTokens=9),
    ]
    runner, _, _ = make_runner([result(events=events)])
    complete(runner)
    assert runner.budget.calls == 2
    assert runner.budget.input_tokens == (2 + 4 + 3) + (7 + 11 + 13)
    assert runner.budget.output_tokens == 6 + 10
    assert len(runner.budget.history) == 2


@pytest.mark.parametrize("events", [
    [],
    [{"type": "step/end", "data": {"usage": {"inputTokens": 10, "outputTokens": 3}}}],
    [{"type": "assistant/message", "data": {}}],
    [{"type": "assistant/attempt", "data": {"stream": []}}],
])
def test_missing_usage_is_unknown_and_prevents_later_requests(make_runner, events):
    runner, client, _ = make_runner([result(events=events), result()])
    with pytest.raises(HarnessFailure, match="用量|usage"):
        complete(runner)
    assert runner.token_usage_complete is False
    assert len(client.calls) == 1
    with pytest.raises(HarnessFailure, match="用量|usage"):
        complete(runner)
    assert len(client.calls) == 1


def test_exhausted_budget_blocks_the_first_request(make_runner):
    runner, client, _ = make_runner([result()], budget=Budget(max_calls=0))
    with pytest.raises(BudgetExceeded):
        complete(runner)
    assert client.calls == []
    assert runner.sessions == []


@pytest.mark.parametrize("budget", [Budget(max_calls=1), Budget(max_tokens=5)])
def test_usage_budget_blocks_schema_repair(make_runner, budget):
    runner, client, _ = make_runner(
        [result("{}", events=[assistant_usage(2, 3)]), result()], budget=budget,
    )
    with pytest.raises(BudgetExceeded):
        complete(runner)
    assert len(client.calls) == len(runner.sessions) == 1
    assert runner.budget.calls == 1


@pytest.mark.parametrize("finish_reason", ["cancelled", "error"])
def test_unsuccessful_runtime_result_is_not_retried(make_runner, finish_reason):
    runner, client, _ = make_runner([result(finish_reason=finish_reason), result()])
    with pytest.raises(HarnessFailure):
        complete(runner)
    assert len(client.calls) == 1


def test_runtime_exception_does_not_expose_model_text_or_credentials(make_runner):
    runner, client, _ = make_runner([RuntimeError("test-secret-key private-model-response")])
    with pytest.raises(HarnessFailure) as error:
        complete(runner)
    assert "test-secret-key" not in str(error.value)
    assert "private-model-response" not in str(error.value)
    assert len(client.calls) == 1


def test_stop_before_request_does_not_call_runtime(make_runner):
    stopping = threading.Event()
    stopping.set()
    runner, client, _ = make_runner([result()], stop_event=stopping)
    with pytest.raises(GenerationInterrupted):
        complete(runner)
    assert client.calls == []


def test_stop_during_request_closes_client_and_releases_worker(make_runner):
    stopping = threading.Event()
    client = BlockingHarness(stopping)
    runner, _, _ = make_runner(client=client, stop_event=stopping)
    try:
        with pytest.raises(GenerationInterrupted):
            complete(runner)
        assert client.started.is_set()
        assert client.returned.is_set()
        assert client.close_calls == 1
    finally:
        client.released.set()


def test_timeout_closes_client_and_releases_worker(make_runner):
    client = BlockingHarness()
    runner, _, options = make_runner(client=client, request_timeout=0.01)
    try:
        with pytest.raises(TimeoutError, match="超时"):
            complete(runner)
        assert options["request_timeout_seconds"] == 0.01
        assert client.started.is_set()
        assert client.returned.is_set()
        assert client.close_calls == 1
    finally:
        client.released.set()


def test_complete_only_runner_uses_blueprints_fixed_plan_flow(make_runner, monkeypatch, tmp_path):
    mock = MockRunner()

    def stage_response(payload):
        data = mock.complete(payload["stage"], payload["instructions"],
                             payload["task_input"], payload["output_schema"])
        return result(json.dumps(data, ensure_ascii=False))

    runner, client, _ = make_runner([stage_response] * 4)
    monkeypatch.delenv("BLUEPRINT_AGENT", raising=False)
    monkeypatch.setattr("blueprint.plan_agent.run_design_agent",
                        lambda *_args, **_kwargs: pytest.fail("不应进入工具 agent 循环"))
    wrapped = StopAwareRunner(runner, threading.Event())
    assert not hasattr(runner, "step")
    assert not supports_agent(runner)
    assert not supports_agent(wrapped)
    plan, document = run_plan("为个人产品经理保存需求文档和任务进度", wrapped,
                              tmp_path / "plan-output", log=lambda _message: None)
    assert plan.design_mode == "fixed"
    assert "不支持工具调用" in plan.design_note
    assert [call["payload"]["stage"] for call in client.calls] == ["extract", "model", "design", "review"]
    assert document.is_file()
    assert len(runner.sessions) == 4


@pytest.mark.parametrize("has_usage", [True, False])
def test_jobs_persist_runtime_sessions_and_usage_completeness(make_runner, tmp_path, has_usage):
    document = {"reply": "请确认草稿", "questions": [], "ready": True,
                "draft": "# 需求草稿\n为个人产品经理保存需求和开发任务。"}
    events = [assistant_usage(13, 8)] if has_usage else [{"type": "assistant/message", "data": {}}]
    runner, client, _ = make_runner([result(json.dumps(document, ensure_ascii=False), events=events)])
    store = Store(tmp_path / "test-store")
    project_id, job_id, stamp = uid(), uid(), now()
    payload = {"message": "整理需求", "messages": [], "prd_id": None}
    with store.tx() as db:
        db.execute("INSERT INTO projects VALUES(?,?,?,?,?)",
                   (project_id, "测试工作台", "仅测试文档生成", stamp, stamp))
        db.execute("INSERT INTO jobs(id,project_id,kind,status,payload,cache_id,created_at,updated_at) "
                   "VALUES(?,?,'interview','queued',?,?,?,?)",
                   (job_id, project_id, json.dumps(payload), uid(), stamp, stamp))
    jobs = Jobs(store, lambda: runner)
    try:
        jobs.run(job_id)
        job = jobs.get(job_id)
        assert job["status"] == ("done" if has_usage else "failed")
        usage = job["usage"]
        assert usage["mode"] == "test"
        assert usage["model"] == MODEL
        assert usage["runtime"] == runner.name
        assert usage["profile_sha256"] == runner.profile_hash
        assert usage["harness_sessions"] == runner.sessions
        assert len(usage["harness_sessions"]) == 1
        assert usage["token_usage_complete"] is has_usage
        if has_usage:
            assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (1, 13, 8)
        else:
            assert job["result"] is None
            with store.tx() as db:
                assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert client.close_calls == 1
    finally:
        jobs.close()
