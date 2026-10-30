import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('VC_ADMIN_PASS', 'test-bootstrap-password')
os.environ['VC_DATA_DIR'] = tempfile.mkdtemp(prefix='ai-berkshire-web-test-import-')

import pytest
from fastapi.testclient import TestClient
from server import auth, accounts, paths, skills_catalog
from server.app import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    monkeypatch.setattr(auth, 'DATA_DIR', data)
    monkeypatch.setattr(auth, 'DB_PATH', data / 'app.db')
    monkeypatch.setattr(auth, 'SETTINGS_FILE', data / 'settings.json')
    monkeypatch.setattr(auth, 'HISTORY_DIR', data / 'history')
    monkeypatch.setattr(auth, 'ADMIN_PASSWORD', 'test-bootstrap-password')
    auth.init_db()
    skills = tmp_path / 'skills'
    skills.mkdir()
    for name in ('investment-research','quality-screen','investment-checklist'):
        (skills / f'{name}.md').write_text('# 测试研究方法\n对 $ARGUMENTS 执行研究。', encoding='utf-8')
    monkeypatch.setattr(skills_catalog, 'SKILLS_DIR', skills)
    client = TestClient(app)
    root = auth.login(auth.ADMIN_USERNAME, 'test-bootstrap-password')
    return client, root


def headers(user):
    return {'Authorization': 'Bearer ' + user['token']}


def member(name='member'):
    result = auth.register(name, 'test-member-password')
    assert result.get('ok'), result
    return auth.login(name, 'test-member-password')
