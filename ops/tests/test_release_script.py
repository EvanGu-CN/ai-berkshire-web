"""Exercise the real release shell against isolated files and fake services."""
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX release protocol requires a local Linux/WSL environment')
ROOT = Path(__file__).resolve().parents[2]


def executable(path, content):
    path.write_text('#!/bin/bash\nset -eu\n' + content, encoding='utf-8')
    path.chmod(0o755)


def installation(tmp_path, *, fail_health=False, active_task=False, fail_continuity=False):
    import pwd
    import grp
    root = tmp_path / 'installation'
    old = root / 'releases/previous'
    (old / 'ai-berkshire-web/server').mkdir(parents=True)
    (root / 'shared/ai-berkshire/skills').mkdir(parents=True)
    (root / 'current').symlink_to(old)
    data = tmp_path / 'data'
    data.mkdir()
    with sqlite3.connect(data / 'app.db') as conn:
        conn.execute('CREATE TABLE users(id TEXT PRIMARY KEY, password_hash TEXT)')
        conn.execute("INSERT INTO users VALUES ('original-user', 'original-hash')")
        conn.execute('CREATE TABLE research_usage(id TEXT PRIMARY KEY, status TEXT)')
        if active_task:
            conn.execute("INSERT INTO research_usage VALUES ('task', 'running')")
    environment = tmp_path / 'runtime.env'
    environment.write_text('existing-runtime-configuration', encoding='utf-8')
    source = tmp_path / 'source'
    (source / 'ops').mkdir(parents=True)
    (source / 'ops/continuity.py').write_bytes((ROOT / 'ops/continuity.py').read_bytes())
    (source / 'ai-berkshire-web/server').mkdir(parents=True)
    (source / 'ai-berkshire-web/requirements.txt').touch()
    archive = tmp_path / 'release.tar.gz'
    with tarfile.open(archive, 'w:gz') as out:
        for name in ('ops', 'ai-berkshire-web'):
            out.add(source / name, arcname=name)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    executable(binaries / 'systemctl', '''
case "$1" in
 show) if [ "$3" = User ]; then echo "$TEST_USER"; else echo "$TEST_GROUP"; fi;;
 stop) echo stopped > "$TEST_STATE";;
 start|restart) echo active > "$TEST_STATE";;
 is-active) test "$(cat "$TEST_STATE")" = active;;
 *) exit 1;;
esac
''')
    executable(binaries / 'curl', '''
if [ "$TEST_FAIL_HEALTH" = 1 ] && [ "$(readlink -f "$VC_REMOTE_DIR/current")" != "$TEST_OLD" ]; then exit 22; fi
''')
    executable(binaries / 'sleep', 'exit 0\n')
    executable(binaries / 'python3', '''
if [ "$1" = -m ] && [ "$2" = venv ]; then
 mkdir -p "$3/bin"
 printf '#!/bin/bash\\nexit 0\\n' > "$3/bin/python"
 chmod +x "$3/bin/python"
elif [ "$TEST_FAIL_CONTINUITY" = 1 ] && [ "${2:-}" = verify ]; then
 exit 1
else
 exec "$TEST_REAL_PYTHON" "$@"
fi
''')
    state = tmp_path / 'service-state'
    state.write_text('active\n')
    env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
           'VC_REMOTE_DIR': str(root), 'VC_DATA_DIR': str(data), 'VC_BACKUP_DIR': str(tmp_path / 'backups'),
           'VC_REMOTE_ENV': str(environment), 'VC_SERVICE': 'ai-berkshire-web.service',
           'VC_ARCHIVE': str(archive), 'VC_ARCHIVE_SHA256': hashlib.sha256(archive.read_bytes()).hexdigest(),
           'VC_COMMIT': 'a' * 40, 'VC_HEALTH_URL': 'http://example.invalid',
           'TEST_USER': pwd.getpwuid(os.getuid()).pw_name, 'TEST_GROUP': grp.getgrgid(os.getgid()).gr_name,
           'TEST_REAL_PYTHON': sys.executable, 'TEST_STATE': str(state), 'TEST_OLD': str(old),
           'TEST_FAIL_HEALTH': str(int(fail_health)), 'TEST_FAIL_CONTINUITY': str(int(fail_continuity))}
    return root, old, environment, state, env


@pytest.mark.parametrize('failure', [None, 'health', 'continuity', 'active_task'])
def test_release_preserves_data_and_environment_and_rolls_back_failures(tmp_path, failure):
    root, old, environment, state, env = installation(
        tmp_path, fail_health=failure == 'health', fail_continuity=failure == 'continuity',
        active_task=failure == 'active_task')
    result = subprocess.run(['bash', str(ROOT / 'ops/deploy.sh')], env=env, capture_output=True, text=True, timeout=20)
    assert environment.read_text() == 'existing-runtime-configuration'
    assert state.read_text().strip() == 'active'
    with sqlite3.connect(tmp_path / 'data/app.db') as conn:
        assert conn.execute('SELECT * FROM users').fetchall() == [('original-user', 'original-hash')]
    if failure:
        assert result.returncode != 0, result.stdout + result.stderr
        assert (root / 'current').resolve() == old
        if failure != 'active_task':
            assert 'previous_release_restarted=true' in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / 'current').resolve() != old
        assert (root / 'current/REVISION').read_text().strip() == 'a' * 40
        assert 'data_continuity=passed' in result.stdout
    assert bool(list((tmp_path / 'backups').glob('*/data.tar.gz'))) == (failure != 'active_task')


def test_already_current_release_cleans_uploaded_archive_without_backup(tmp_path):
    root, old, _, state, env = installation(tmp_path)
    (old / 'REVISION').write_text('a' * 40 + '\n')
    result = subprocess.run(['bash', str(ROOT / 'ops/deploy.sh')], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'already_current=true' in result.stdout
    assert (root / 'current').resolve() == old
    assert state.read_text().strip() == 'active'
    assert not Path(env['VC_ARCHIVE']).exists()
    assert not (tmp_path / 'backups').exists()
