"""Local launch and documentation for the unchanged upstream projects."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import Request
from dotenv import dotenv_values
from backend.engine import ROOT
from backend.store import Problem

AGENT = ROOT / 'integrations' / 'agent-workshop'
STARTER = ROOT / 'integrations' / 'ai-product-starter-kit'
URL = 'http://127.0.0.1:18765/'
_lock = threading.Lock()
_process = None

def _ready():
    try:
        with urllib.request.urlopen(URL + '__blue_workshop_identity__', timeout=1) as response:
            return json.load(response).get('service') == 'blue-original-agent-workshop-v1'
    except (OSError, ValueError):
        return False

def _prepare_config():
    target = AGENT / '.env'
    if target.exists():
        return
    values = dotenv_values(ROOT / '.env')
    key = values.get('ANTHROPIC_API_KEY') or ''
    model = values.get('MODEL_ID') or 'deepseek-flash'
    # Only copy values from the user's existing local configuration, never return them.
    with target.open('x', encoding='utf-8', newline='\n') as file:
        file.write(f'ANTHROPIC_API_KEY={key}\nMODEL_ID={model}\nANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic\n')

def start():
    global _process
    with _lock:
        if _ready():
            return {'url': URL, 'running': True}
        if not (AGENT / 'blueprint' / 'web' / 'server.py').is_file():
            raise Problem(503, 'UPSTREAM_MISSING', 'Agent 工坊文件缺失，请检查安装。')
        _prepare_config()
        data = Path(os.environ.get('WORKBENCH_DATA_DIR', ROOT / 'data'))
        data.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'MODEL_ID', 'API_FORMAT', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'):
            env.pop(name, None)
        env['BLUEPRINT_OUT'] = str(data / 'agent-workshop')
        env['PYTHONUTF8'] = '1'
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        with (data / 'agent-workshop.log').open('ab') as log:
            _process = subprocess.Popen([sys.executable, str(ROOT / 'backend' / 'original_workshop_server.py')],
                cwd=str(AGENT), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=flags, close_fds=True)
        for _ in range(40):
            if _ready():
                return {'url': URL, 'running': True}
            if _process.poll() is not None:
                break
            time.sleep(.1)
        raise Problem(503, 'UPSTREAM_START_FAILED', 'Agent 工坊启动失败；请检查 18765 端口和本地日志。')

def routes(app):
    @app.post('/api/integrations/agent/start')
    def start_agent(request: Request):
        return start()

    @app.get('/api/integrations/starter/guide')
    def starter_guide():
        path = STARTER / 'README.md'
        if not path.is_file():
            raise Problem(503, 'UPSTREAM_MISSING', '工具包文件缺失，请检查安装。')
        return {'content': path.read_text(encoding='utf-8'), 'path': str(STARTER)}

    @app.post('/api/integrations/starter/open-folder')
    def open_starter_folder():
        if os.name != 'nt' or not STARTER.is_dir():
            raise Problem(503, 'FOLDER_UNAVAILABLE', '无法打开工具包目录。')
        os.startfile(str(STARTER))
        return {'ok': True}
