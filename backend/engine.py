"""供应商适配：密钥只读本机 .env，不通过 API 传输或返回。"""
from pathlib import Path
from dotenv import dotenv_values

from backend.store import Problem

ROOT = Path(__file__).resolve().parents[1]


def config():
    values = dotenv_values(ROOT / ".env")
    return {
        "key": values.get("ANTHROPIC_API_KEY") or "",
        "model": values.get("MODEL_ID") or "deepseek-flash",
        "base_url": "https://api.deepseek.com/anthropic",
    }


def model_status():
    cfg = config()
    key = cfg["key"]
    return {"provider": "deepseek", "model": cfg["model"],
            "runtime": "deepseek-harness-sdk", "runtime_version": "0.2.1rc2", "profile": "workbench-documents",
            "code_execution": False, "session_log_upload": False,
            "configured": bool(key and not any(x in key.lower() for x in ("xxx", "placeholder", "your"))),
            "base_url": cfg["base_url"], "validation": "需要真实生成任务验证"}


class GenerationInterrupted(BaseException):
    """终止信号不进入 SDK 的 Exception 重试或引擎的降级重试。"""


class StopAwareRunner:
    def __init__(self, runner, stop_event):
        self._runner, self._stop_event = runner, stop_event

    def __getattr__(self, name):
        value = getattr(self._runner, name)
        if name in ("complete", "step"):
            def call(*args, **kwargs):
                if self._stop_event.is_set():
                    raise GenerationInterrupted()
                return value(*args, **kwargs)
            return call
        return value


def real_runner(stop_event=None, reasoning_effort=None):
    if not model_status()["configured"]:
        raise Problem(409, "MODEL_NOT_CONFIGURED", "请先在本机 .env 中填写 DeepSeek API Key")
    cfg = config()
    import os
    import uuid
    from backend.harness import HarnessRunner
    root = Path(os.environ.get("WORKBENCH_DATA_DIR", ROOT / "data")) / "harness" / uuid.uuid4().hex
    return HarnessRunner(api_key=cfg["key"], model=cfg["model"], root=root, stop_event=stop_event,
                         reasoning_effort=reasoning_effort)


def safe_model_error(exc):
    from blueprint.llm import BudgetExceeded, StageOutputInvalid
    name = type(exc).__name__
    if isinstance(exc, BudgetExceeded):
        return "达到本次任务的调用或 token 上限；请查看进度后决定是否重试"
    if isinstance(exc, StageOutputInvalid) or name == "ValidationError":
        return "模型输出未通过结构校验，已保留之前的数据，可手动重试"
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "模型密钥无效、余额不足或没有访问权限，请检查 DeepSeek 账户"
    if name == "RateLimitError":
        return "模型服务限流或额度不足，请稍后重试"
    if name in ("APIConnectionError", "APITimeoutError", "TimeoutError"):
        return "连接模型服务失败或超时，请检查网络后重试"
    if name == "HarnessFailure":
        return "DeepSeek Harness 运行未完成，请检查密钥、余额或网络后手动重试"
    return "生成未完成，已有文档不受影响，请检查模型配置后重试"
