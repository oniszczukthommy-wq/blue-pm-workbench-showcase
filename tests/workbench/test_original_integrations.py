from fastapi.testclient import TestClient
from backend.app import create_app
from backend import original_integrations as integration


def test_integration_endpoints_preserve_local_origin_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(integration, 'start', lambda: {'url': integration.URL, 'running': True})
    starter = tmp_path / 'starter'
    starter.mkdir()
    (starter / 'README.md').write_text('# Original guide', encoding='utf-8')
    monkeypatch.setattr(integration, 'STARTER', starter)
    with TestClient(create_app(data_root=tmp_path / 'db'), base_url='http://127.0.0.1:8788') as client:
        assert client.get('/api/integrations/starter/guide').json()['content'] == '# Original guide'
        assert client.post('/api/integrations/agent/start').json()['url'] == integration.URL
        assert client.post('/api/integrations/agent/start', headers={'Origin': 'https://example.com'}).status_code == 403


def test_workshop_config_copied_once_without_overwriting_changes(tmp_path, monkeypatch):
    workshop = tmp_path / 'workshop'
    workshop.mkdir()
    (tmp_path / '.env').write_text('ANTHROPIC_API_KEY=example-test-key\nMODEL_ID=test-model\n', encoding='utf-8')
    monkeypatch.setattr(integration, 'ROOT', tmp_path)
    monkeypatch.setattr(integration, 'AGENT', workshop)
    integration._prepare_config()
    config = (workshop / '.env').read_text(encoding='utf-8')
    assert 'MODEL_ID=test-model' in config
    assert 'ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic' in config
    (workshop / '.env').write_text('MODEL_ID=user-choice\n', encoding='utf-8')
    integration._prepare_config()
    assert (workshop / '.env').read_text(encoding='utf-8') == 'MODEL_ID=user-choice\n'
