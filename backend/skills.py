"""项目技能库：只读装载已登记的本地文档，不执行技能包中的命令。"""
import hashlib
import json
import re
from pathlib import Path

from backend.engine import ROOT
from backend.store import Problem

SKILLS_ROOT = ROOT / ".agents" / "skills"


def _installed():
    manifest = SKILLS_ROOT.parent / "skill-installations.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else []


def _entry(skill_id):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id):
        raise Problem(404, "SKILL_NOT_FOUND", "没有找到这个技能")
    entry = next((item for item in _installed() if item["id"] == skill_id), None)
    if entry is None:
        raise Problem(404, "SKILL_NOT_FOUND", "没有找到这个技能")
    return entry


def load_skill(skill_id):
    entry = _entry(skill_id)
    root = SKILLS_ROOT / skill_id
    if root.is_symlink() or not root.resolve().is_relative_to(SKILLS_ROOT.resolve()):
        raise Problem(409, "SKILL_INVALID", "技能目录与安装记录不一致")
    contents = {}
    for relative, expected_hash in entry["files"].items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise Problem(409, "SKILL_INVALID", "技能文件缺失或指向目录之外")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise Problem(409, "SKILL_CHANGED", "技能文件已变化，请重新检查安装记录")
        contents[relative] = content.decode("utf-8")
    return contents


def catalog():
    result = []
    for entry in _installed():
        contents = load_skill(entry["id"])
        result.append({"id": entry["id"], "name": entry["name"], "installed_at": entry["installed_at"],
                       "file_count": len(contents), "status": "installed",
                       "description": entry.get("description", "按定位、用户、技术、模型与基础层拆解产品，区分事实、推断和未确认信息。"),
                       "requirements": entry["requirements"], "runtime_status": entry["runtime_status"],
                       "capability_note": "可在产品助理输入框旁选择，按本轮方法讨论；不会自动确认或修改正式文档。" if entry.get("integration") == "assistant-method" else "已接入产品页的「产品拆解」：七路公开搜证、彩色报告与版本保存；需点击开始才会调用 DeepSeek。" if entry["runtime_status"] == "enabled" else "技能已安装，运行入口尚未启用。"})
    return result


METHODS = {
    'aipm-problem-definition': '问题定义',
    'aipm-prioritization-scope': '优先级与范围',
    'aipm-prd-writing-review': 'PRD 审查',
}


def method_prompt(skill_id):
    if not skill_id:
        return ''
    if skill_id not in METHODS:
        raise Problem(422, 'METHOD_NOT_AVAILABLE', '请选择已接入聊天的产品方法')
    files = load_skill(skill_id)
    return '\n\n'.join(f'【{path}】\n{content}' for path, content in files.items())
