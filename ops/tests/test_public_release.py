import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from ops import public_release as release


@pytest.mark.parametrize('path', [
    '.env.local', '.git/config', 'ai-berkshire-web/data/app.db',
    'ai-berkshire-web/shared-reports/private.md', 'ai-berkshire/reports/private.md',
    'ops/.local/known_hosts', 'ops/backup.db', '../README.md',
    'ai-berkshire-web/static/../../private.py', 'platform.local.json',
    'docs/screenshots/private.png', 'docs/screenshots/data/reader.png', 'docs/private-capture.png',
])
def test_public_allowlist_rejects_private_runtime_and_report_files(path):
    assert not release.allowed_path(path)


def test_public_allowlist_accepts_only_source_and_examples():
    for path in ('README.md', 'LICENSE', '.env.app.example', 'platform.example.json',
                 'ai-berkshire-web/server/auth.py', 'ai-berkshire-web/static/brand/mark.svg',
                 'ops/tests/test_public_release.py', 'docs/screenshots/workbench.png',
                 'docs/screenshots/mobile.png'):
        assert release.allowed_path(path)


def test_scanner_never_prints_matched_secret_values():
    secret = b'sk-' + b'a' * 32
    findings = release.scan_content('source.py', secret + b'\nprivate-host.example.invalid',
                                    [b'private-host.example.invalid'])
    assert {row['category'] for row in findings} == {'access-token', 'private-value'}
    assert secret.decode() not in json.dumps(findings)
    assert 'private-host.example.invalid' not in json.dumps(findings)


def source_tree(monkeypatch):
    files = {'README.md': b'# Public source', 'LICENSE': b'MIT License',
             'SECURITY.md': b'No real credentials', 'ai-berkshire-web/server/app.py': b'# synthetic app',
             '.env.local': b'NEVER_READ', 'ai-berkshire-web/shared-reports/private.md': b'NEVER_READ'}
    objects = {str(i): value for i, value in enumerate(files.values())}
    listing = b''.join(b'100644 blob ' + str(i).encode() + b'\t' + name.encode() + b'\0'
                       for i, name in enumerate(files))
    original = release.git

    def fake_git(root, *args, **kwargs):
        if root != release.ROOT:
            return original(root, *args, **kwargs)
        if args[0] == 'ls-tree':
            return listing
        body = objects[args[-1]]
        assert body != b'NEVER_READ', 'private files must never be read into the export'
        return body

    monkeypatch.setattr(release, 'git', fake_git)


def test_export_has_no_history_data_or_private_identity_and_only_one_root_commit(tmp_path, monkeypatch):
    source_tree(monkeypatch)
    target = tmp_path / 'PublicSource'
    result = release.export('unused-test-ref', target, init_git=True)
    assert result['old_history_included'] is False and result['root_commit']
    assert subprocess.check_output(['git', 'rev-list', '--count', '--all'], cwd=target).strip() == b'1'
    commit = subprocess.check_output(['git', 'cat-file', '-p', 'HEAD'], cwd=target)
    assert b'parent ' not in commit
    assert b'contributors@example.invalid' in commit
    assert subprocess.check_output(['git', 'remote'], cwd=target).strip() == b''
    with zipfile.ZipFile(result['archive']) as archive:
        names = archive.namelist()
        assert len(names) == 5
        assert all('/.git/' not in name and '/data/' not in name and 'shared-reports' not in name for name in names)
    manifest = json.loads((target / 'PUBLIC_RELEASE.json').read_text())
    assert len(manifest['files']) == 4 and 'source_commit' not in manifest


def test_export_refuses_existing_target_or_archive_before_writing(tmp_path):
    target = tmp_path / 'existing'
    target.mkdir()
    marker = target / 'keep.txt'
    marker.write_text('keep')
    with pytest.raises(ValueError, match='目标目录'):
        release.export('HEAD', target)
    assert marker.read_text() == 'keep'
    target = tmp_path / 'archive-conflict'
    target.with_suffix('.zip').write_bytes(b'keep')
    with pytest.raises(ValueError, match='压缩包'):
        release.export('HEAD', target)
    assert not target.exists()
