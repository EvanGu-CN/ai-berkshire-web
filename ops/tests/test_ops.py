import base64
import hashlib
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import paramiko
import pytest

from ops.config import Config, read_env
from ops.continuity import snapshot, verify
from ops.release import release_error, validate_members
from ops.remote import RememberHostKey, fingerprint


def config_file(tmp_path, extra=''):
    path = tmp_path / '.env.local'
    path.write_text('VC_SSH_HOST=example.invalid\nVC_SSH_USER=root\nVC_SSH_PASSWORD="p # with spaces"\n' + extra, encoding='utf-8')
    return path


def test_environment_overrides_file_and_secret_repr_is_hidden(tmp_path):
    cfg = Config.load(config_file(tmp_path), {'VC_SSH_PORT': '2222'})
    assert cfg.port == 2222
    assert cfg.password == 'p # with spaces'
    assert cfg.password not in repr(cfg)
    assert cfg.known_hosts == tmp_path / '.local/known_hosts'


@pytest.mark.parametrize('extra', [
    'VC_SSH_PORT=0\n', 'VC_SSH_PORT=wrong\n', 'VC_REMOTE_DIR=/\n',
    'VC_DATA_DIR=/var/../etc\n', 'VC_SERVICE=bad;cmd.service\n',
    'VC_PUBLIC_URL=https://user:password@example.invalid\n', 'VC_SSH_HOST_KEY_SHA256=wrong\n',
])
def test_invalid_configuration_is_rejected_without_values(tmp_path, extra):
    with pytest.raises(ValueError) as error:
        Config.load(config_file(tmp_path, extra), {})
    assert 'p # with spaces' not in str(error.value)


def test_relative_key_is_resolved_from_config_directory(tmp_path):
    (tmp_path / 'key').write_text('test placeholder')
    cfg = Config.load(config_file(tmp_path, 'VC_SSH_KEY_FILE=key\n'), {})
    assert cfg.key_file == str(tmp_path / 'key')


def test_quote_errors_never_echo_secret(tmp_path):
    path = tmp_path / '.env.local'
    path.write_text('VC_SSH_PASSWORD="secret-invalid', encoding='utf-8')
    with pytest.raises(ValueError) as error:
        read_env(path)
    assert 'secret-invalid' not in str(error.value)


def test_host_key_pin_rejects_wrong_key_before_enrollment(tmp_path):
    key = Mock()
    key.asbytes.return_value = b'server public key'
    cfg = Config(host='example.invalid', user='root', password='secret',
                 known_hosts=tmp_path / 'known_hosts', host_key_sha256='SHA256:' + 'A' * 43)
    client = Mock()
    with pytest.raises(paramiko.SSHException):
        RememberHostKey(cfg).missing_host_key(client, cfg.host, key)
    client.save_host_keys.assert_not_called()
    assert fingerprint(key) == 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


@pytest.mark.parametrize('name', [
    '../.env.local', '/etc/passwd', 'ai-berkshire-web/data/app.db',
    'ops/.env.local', 'ops/.local/known_hosts', 'ai-berkshire-web/.venv/bin/python',
    'ai-berkshire-web/shared-reports/private.md',
])
def test_release_refuses_private_or_runtime_files(name):
    with pytest.raises(ValueError):
        validate_members([name])


def test_release_accepts_source_and_placeholder_config():
    validate_members(['ai-berkshire-web/server/app.py', 'ops/deploy.sh', 'ops/.env.example'])


def test_continuity_allows_new_rows_and_columns_but_detects_changed_config(tmp_path):
    database = tmp_path / 'app.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE user_settings(user_id TEXT PRIMARY KEY, doc TEXT)')
        conn.execute('INSERT INTO user_settings VALUES (?,?)', ('u1', 'private model configuration'))
    baseline = snapshot(database)
    assert 'private model configuration' not in str(baseline)
    with sqlite3.connect(database) as conn:
        conn.execute('ALTER TABLE user_settings ADD COLUMN added TEXT')
        conn.execute('INSERT INTO user_settings VALUES (?,?,?)', ('u2', 'new configuration', 'new'))
    verify(database, baseline)
    with sqlite3.connect(database) as conn:
        conn.execute('UPDATE user_settings SET doc=? WHERE user_id=?', ('replaced', 'u1'))
    with pytest.raises(RuntimeError):
        verify(database, baseline)


def test_read_only_continuity_does_not_create_missing_database(tmp_path):
    database = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        snapshot(database)
    assert not database.exists()


@pytest.mark.parametrize('output,expected', [
    ('deployment_phase=prepare\nactive_research_tasks; retry deployment after completion', '有研究任务'),
    ('deployment_locked', '已有发布'),
    ('deployment_phase=health\nprivate-provider-response\nprevious_release_restarted=true', '服务健康检查失败'),
    ('deployment_phase=continuity\nsecret-value', '数据连续性检查失败'),
])
def test_release_failures_are_actionable_without_raw_output(output, expected):
    message = release_error(output, 1)
    assert expected in message
    assert 'private-provider-response' not in message
    assert 'secret-value' not in message


def test_browser_checks_refuse_existing_application_port(monkeypatch):
    from ops import check
    port = Mock()
    port.__enter__ = Mock(return_value=port)
    port.__exit__ = Mock(return_value=False)
    port.bind.side_effect = OSError('in use')
    monkeypatch.setattr(check.socket, 'socket', lambda: port)
    start = Mock()
    monkeypatch.setattr(check.subprocess, 'Popen', start)
    with pytest.raises(RuntimeError, match='8765'):
        check.browser_checks('node', 'playwright', {})
    start.assert_not_called()
