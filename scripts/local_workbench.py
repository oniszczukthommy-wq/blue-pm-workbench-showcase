"""Desktop launcher for the existing local workbench; never starts model jobs."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NAME = "Blue产品经理工作台"
LABEL = "com.blue.pm-workbench"
TARGET = f"gui/{os.getuid()}/{LABEL}"
SUPPORT = Path.home() / "Library" / "Application Support" / NAME
PLIST = SUPPORT / f"{LABEL}.plist"
LOGS = Path.home() / "Library" / "Logs" / NAME
URL = "http://127.0.0.1:8788"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path: str):
    with HTTP.open(URL + path, timeout=2) as response:
        return json.load(response)


def healthy() -> bool:
    try:
        state = request("/api/health")
        return isinstance(state, dict) and state.get("status") == "ok" and state.get("mode") == "local-single-user"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def own_listener() -> bool:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-t", "-iTCP:8788", "-sTCP:LISTEN"],
        capture_output=True, text=True,
    )
    for pid in set(result.stdout.split()):
        cwd = subprocess.run(["/usr/sbin/lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                             capture_output=True, text=True)
        command = subprocess.run(["/bin/ps", "-p", pid, "-o", "command="],
                                 capture_output=True, text=True)
        if f"n{ROOT}" in cwd.stdout.splitlines() and "-m backend" in command.stdout:
            return True
    return False


def registered() -> bool:
    return subprocess.run(["/bin/launchctl", "print", TARGET],
                          capture_output=True).returncode == 0


def busy_port() -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", 8788)) == 0


def start():
    if healthy() and own_listener():
        return
    if busy_port():
        raise RuntimeError("8788 端口已被其他服务使用，或工作台仍在启动，请稍后再试。")
    if not (ROOT / ".venv/bin/python").is_file() or not (ROOT / "workbench-ui/dist/index.html").is_file():
        raise RuntimeError("未找到工作台运行环境或前端构建，请保留原项目目录。")
    if not PLIST.is_file():
        raise RuntimeError(f"本机启动配置缺失，请检查安装配置：{PLIST}。")
    command = (["/bin/launchctl", "kickstart", TARGET] if registered() else
               ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"后台服务未能启动，请查看 {LOGS}。")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if healthy() and own_listener():
            return
        time.sleep(0.3)
    raise RuntimeError(f"工作台未能在30秒内就绪，请查看 {LOGS}。")


def stop():
    if not registered():
        if healthy() and own_listener():
            raise RuntimeError("当前工作台由开发终端启动，请在原终端按 Ctrl+C 停止。")
        print("工作台已停止。")
        return
    if healthy():
        if request("/api/health").get("active_chats", 0):
            raise RuntimeError("产品助理仍在回复，请等待完成后再停止。")
        projects = request("/api/projects")["items"]
        if any(project.get("active_job") for project in projects):
            raise RuntimeError("仍有 AI 任务进行中，请等待完成后再停止。")
    result = subprocess.run(["/bin/launchctl", "bootout", TARGET], capture_output=True)
    if result.returncode:
        raise RuntimeError("未能停止工作台，请稍后重试。")
    deadline = time.monotonic() + 25
    while busy_port() and time.monotonic() < deadline:
        time.sleep(0.2)
    if own_listener():
        raise RuntimeError("服务尚未完全退出，请稍后再试。")
    print("工作台已停止，产品与文档保留在本机。")


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else "open"
    if action not in {"open", "start", "stop", "status"}:
        raise RuntimeError("用法：local_workbench.py [open|start|stop|status]")
    os.umask(0o077)
    SUPPORT.mkdir(parents=True, exist_ok=True)
    with (SUPPORT / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "status":
            print("运行中：" + URL if healthy() and own_listener() else "工作台未运行。")
        elif action == "stop":
            stop()
        else:
            start()
            if action == "open":
                subprocess.run(["/usr/bin/open", URL + "/?v=desktop#/assistant"], check=True)
            print("工作台已就绪：" + URL)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
