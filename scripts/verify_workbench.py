"""真实 HTTP 进程重启验证。临时目录写入自建样例，不调用模型。"""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def request(path, body=None):
        req = urllib.request.Request(base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read()
            return raw if response.headers.get_content_type() == "application/zip" else json.loads(raw)

    def start(env):
        process = subprocess.Popen([sys.executable, "-m", "backend"], cwd=ROOT,
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("验证服务启动失败")
            try:
                if request("/api/health")["status"] == "ok":
                    return process
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        stop(process)
        raise RuntimeError("验证服务启动超时")

    def stop(process):
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    with tempfile.TemporaryDirectory(prefix="pm-workbench-verify-") as temporary:
        env = {**os.environ, "WORKBENCH_DATA_DIR": temporary, "WORKBENCH_PORT": str(port)}
        process = start(env)
        content = "# 重启验收样例\n\n用户需求原文必须持久化。"
        try:
            project = request("/api/projects", {"name": "进程重启验证", "idea": "验证后端数据库持久化"})
            pid = project["id"]
            first = request(f"/api/projects/{pid}/prd/import", {
                "filename": "需求.md", "content": content, "expected_version": 0})
            second = request(f"/api/projects/{pid}/prd", {
                "content": content + "\n第二版新增需求。", "expected_version": 1, "change_note": "第二轮"})
        finally:
            stop(process)
        process = start(env)
        try:
            assert request(f"/api/projects/{pid}/documents/{first['id']}")["content"] == content
            assert request(f"/api/projects/{pid}/documents/{second['id']}")["version"] == 2
            assert len(request(f"/api/projects/{pid}/documents?kind=prd")["items"]) == 2
            with zipfile.ZipFile(io.BytesIO(request(f"/api/projects/{pid}/export"))) as archive:
                assert archive.read("prd/V1.md").decode() == content
                assert "prd/V2.md" in archive.namelist()
                assert all(".env" not in name and ".sqlite" not in name for name in archive.namelist())
        finally:
            stop(process)
    ignored = subprocess.run(["git", "check-ignore", ".env", "data/workbench.sqlite3"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assert set(ignored) == {".env", "data/workbench.sqlite3"}
    print(json.dumps({"http_process_restart": "passed", "prd_versions_restored": 2,
                      "historical_content": "passed", "zip_export": "passed", "git_secret_exclusion": "passed",
                      "real_model": "not_exercised_by_this_script", "model_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
