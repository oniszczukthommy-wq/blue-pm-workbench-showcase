import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import skills
from backend.store import Problem


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    root = tmp_path / '.agents/skills'
    folder = root / 'test-teardown'
    (folder / 'references').mkdir(parents=True)
    files = {'SKILL.md': '# 测试技能\n按证据拆解产品。', 'references/framework.md': '# 方法参考'}
    for relative, content in files.items():
        (folder / relative).write_bytes(content.encode('utf-8'))
    manifest = [{'id': 'test-teardown', 'name': '测试产品拆解', 'installed_at': '2026-10-28',
                 'files': {path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()},
                 'requirements': ['联网搜证'], 'runtime_status': 'installed-not-enabled'}]
    (root.parent / 'skill-installations.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(skills, 'SKILLS_ROOT', root)
    return root


def test_skill_catalog_and_readonly_content_api(skill_root, tmp_path):
    def no_model():
        raise AssertionError('安装与读取技能不能调用模型')
    with TestClient(create_app(tmp_path / 'data', no_model), base_url='http://127.0.0.1:8788') as client:
        catalog = client.get('/api/skills')
        assert catalog.status_code == 200
        item = catalog.json()['items'][0]
        assert item['file_count'] == 2 and item['status'] == 'installed'
        assert item['runtime_status'] == 'installed-not-enabled'
        assert str(tmp_path) not in catalog.text
        detail = client.get('/api/skills/test-teardown')
        assert detail.status_code == 200
        assert detail.json()['content'].startswith('# 测试技能')
        assert detail.json()['references'][0]['path'] == 'references/framework.md'
        assert client.get('/api/skills/not-installed').status_code == 404
        assert client.get('/api/skills', headers={'Origin': 'https://evil.test'}).status_code == 403


def test_modified_skill_is_rejected(skill_root):
    (skill_root / 'test-teardown/SKILL.md').write_text('已篡改')
    with pytest.raises(Problem) as error:
        skills.load_skill('test-teardown')
    assert error.value.code == 'SKILL_CHANGED'


def test_skill_traversal_and_external_symlink_are_rejected(skill_root, tmp_path):
    for skill_id in ('../.env', '/tmp', 'test-teardown/../..'):
        with pytest.raises(Problem) as error:
            skills.load_skill(skill_id)
        assert error.value.status == 404
    private = tmp_path / 'private.txt'
    private.write_text('synthetic-private-data')
    reference = skill_root / 'test-teardown/references/framework.md'
    reference.unlink()
    reference.symlink_to(private)
    with pytest.raises(Problem) as error:
        skills.load_skill('test-teardown')
    assert error.value.code == 'SKILL_INVALID'
