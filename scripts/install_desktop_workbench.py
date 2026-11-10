"""Install a macOS desktop launcher without enabling login autostart."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import tempfile

from local_workbench import LABEL, LOGS, NAME, PLIST, ROOT, SUPPORT


def install():
    os.umask(0o077)
    app = Path.home() / "Desktop" / (NAME + ".app")
    if app.exists():
        raise RuntimeError(f"桌面已存在同名应用，未覆盖：{app}")
    if not (ROOT / ".venv/bin/python").is_file() or not (ROOT / "workbench-ui/dist/index.html").is_file():
        raise RuntimeError("请先完成本机 Python 环境与前端构建。")
    SUPPORT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(ROOT / ".venv/bin/python"), "-m", "backend"],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": str(ROOT / ".venv/bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
            "WORKBENCH_PORT": "8788",
            "WORKBENCH_DATA_DIR": str(ROOT / "data"),
            "PYTHONUNBUFFERED": "1",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ExitTimeOut": 20,
        "Umask": 0o077,
        "StandardOutPath": str(LOGS / "server.log"),
        "StandardErrorPath": str(LOGS / "server-error.log"),
    }
    PLIST.write_bytes(plistlib.dumps(config))
    PLIST.chmod(0o600)
    command = shlex.join([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/local_workbench.py"), "open"])
    source = (
        "on run\ntry\ndo shell script " + json.dumps(command, ensure_ascii=False)
        + '\non error messageText\ndisplay alert "Blue产品经理工作台启动失败" '
          'message messageText as critical\nend try\nend run\n'
    )
    subprocess.run(["/usr/bin/osacompile", "-o", str(app), "-"], input=source, text=True, check=True)
    resources = app / "Contents/Resources"
    icon = ROOT / "workbench-ui/public/assets/brand/blue-path-mark.png"
    with tempfile.TemporaryDirectory(prefix="blue-workbench-icon-") as temporary:
        iconset = Path(temporary) / "BlueWorkbench.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                pixels = str(size * scale)
                output = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(["/usr/bin/sips", "-z", pixels, pixels, str(icon), "--out", str(output)],
                               check=True, capture_output=True)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", "-o", str(resources / "BlueWorkbench.icns"), str(iconset)],
                       check=True, capture_output=True)
    info = app / "Contents/Info.plist"
    metadata = plistlib.loads(info.read_bytes())
    metadata.update({"CFBundleIdentifier": "com.blue.pm-workbench.desktop", "CFBundleDisplayName": NAME,
                     "CFBundleName": NAME, "CFBundleIconFile": "BlueWorkbench.icns",
                     "LSUIElement": True, "NSHighResolutionCapable": True})
    info.write_bytes(plistlib.dumps(metadata))
    # Re-sign the local AppleScript bundle after changing metadata and its icon.
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
                   check=True, capture_output=True)
    print("桌面启动应用：" + str(app))
    print("本机后台配置：" + str(PLIST))
    print("服务日志：" + str(LOGS))


if __name__ == "__main__":
    install()
