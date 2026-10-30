import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import headers, member
from server import reports_lib as reports, upstream_sync as sync


@pytest.fixture
def framework(tmp_path, monkeypatch):
    target = tmp_path / 'shared' / 'ai-berkshire'
    for name in ('skills', 'reports', 'tools', '.git.bak'):
        (target / name).mkdir(parents=True)
    (target / 'reports' / 'old.md').write_text('# Existing report', encoding='utf-8')
    (target / '.git.bak' / 'config').write_text('local history', encoding='utf-8')

    source = tmp_path / 'upstream'
    for name in ('skills', 'reports', 'tools', '.git', '.git.bak'):
        (source / name).mkdir(parents=True)
    (source / 'skills' / 'research.md').write_text('# Research skill', encoding='utf-8')
    (source / 'reports' / 'new.md').write_text('# Updated report', encoding='utf-8')
    (source / 'reports' / 'index.json').write_text(json.dumps({'reports': []}), encoding='utf-8')
    (source / 'tools' / 'financial_rigor.py').write_text('# tool', encoding='utf-8')
    (source / '.git' / 'config').write_text('upstream metadata', encoding='utf-8')
    (source / '.git.bak' / 'config').write_text('must not replace local history', encoding='utf-8')

    def run(command, **kwargs):
        if command[1] == 'clone':
            shutil.copytree(source, Path(command[-1]))
            return subprocess.CompletedProcess(command, 0, '', '')
        assert command[1] == '-C'
        return subprocess.CompletedProcess(command, 0, 'abc123 Updated framework\n', '')

    monkeypatch.setattr(sync.subprocess, 'run', run)
    monkeypatch.setattr(sync, 'AI_BERKSHIRE', target)
    monkeypatch.setattr(sync, '_status', {'state': 'running'})
    monkeypatch.setattr(reports, 'AI_BERKSHIRE', target)
    monkeypatch.setattr(reports, 'REPORTS_DIR', target / 'reports')
    monkeypatch.setattr(reports, 'REPORTS_INDEX', target / 'reports' / 'index.json')
    monkeypatch.setattr(reports, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(reports, 'CACHE_FILE', tmp_path / 'data' / 'reports_cache.json')
    monkeypatch.setattr(reports, '_INDEX', None)
    monkeypatch.setattr(reports, '_BUILT_AT', 0)
    return target, source


def assert_old_framework(target):
    assert (target / 'reports' / 'old.md').read_text(encoding='utf-8') == '# Existing report'
    assert not (target / 'reports' / 'new.md').exists()
    assert (target / '.git.bak' / 'config').read_text(encoding='utf-8') == 'local history'


@pytest.mark.parametrize('linked', [False, True])
def test_sync_preserves_framework_root_and_rebuilds_reports(framework, tmp_path, monkeypatch, linked):
    target, _ = framework
    root_inode = target.stat().st_ino
    link = tmp_path / 'release' / 'ai-berkshire'
    if linked:
        link.parent.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f'Directory symlinks unavailable: {exc}')
        monkeypatch.setattr(sync, 'AI_BERKSHIRE', link)
        monkeypatch.setattr(reports, 'AI_BERKSHIRE', link)
        monkeypatch.setattr(reports, 'REPORTS_DIR', link / 'reports')
        monkeypatch.setattr(reports, 'REPORTS_INDEX', link / 'reports' / 'index.json')
    # Production permits writes inside the framework, but not in its parent.
    mode = target.parent.stat().st_mode
    if os.name != 'nt':
        target.parent.chmod(0o555)
    try:
        sync._run('fixture-repo')
    finally:
        if os.name != 'nt':
            target.parent.chmod(mode)

    assert sync.get_status()['state'] == 'done', sync.get_status()
    assert target.stat().st_ino == root_inode
    if linked:
        assert link.is_symlink() and link.resolve() == target
    assert not (target / 'reports' / 'old.md').exists()
    assert not (target / '.git').exists()
    assert (target / '.git.bak' / 'config').read_text(encoding='utf-8') == 'local history'
    assert not list(target.glob('.upstream-sync-*'))
    assert reports.list_reports()['total'] == 1
    assert reports.get_report('reports/new.md')['content'] == '# Updated report'
    assert reports.CACHE_FILE.is_file()


@pytest.mark.parametrize('failure', ['copy', 'backup', 'install'])
def test_failed_install_preserves_old_framework(framework, monkeypatch, failure):
    target, _ = framework
    copytree, rename = shutil.copytree, Path.rename
    failed = False

    def fail_copy(source, destination, *args, **kwargs):
        if Path(destination).name == 'incoming':
            Path(destination).mkdir()
            (Path(destination) / 'partial').write_text('unfinished', encoding='utf-8')
            raise OSError('simulated copy failure')
        return copytree(source, destination, *args, **kwargs)

    def fail_rename(source, destination):
        nonlocal failed
        match = (failure == 'backup' and source == target / 'tools') or (
            failure == 'install' and source.parent.name == 'incoming' and source.name == 'reports')
        if not failed and match:
            failed = True
            raise OSError('simulated rename failure')
        return rename(source, destination)

    if failure == 'copy':
        monkeypatch.setattr(sync.shutil, 'copytree', fail_copy)
    else:
        monkeypatch.setattr(Path, 'rename', fail_rename)
    sync._run('fixture-repo')
    assert sync.get_status()['state'] == 'error'
    assert_old_framework(target)
    assert not list(target.glob('.upstream-sync-*'))
    assert not reports.CACHE_FILE.exists()


def test_failed_rollback_keeps_recoverable_files(framework, monkeypatch):
    target, _ = framework
    rename = Path.rename

    def fail_rename(source, destination):
        if (source.parent.name == 'incoming' and source.name == 'reports') or (
                source.parent.name == 'previous' and source.name == 'reports'):
            raise OSError('simulated filesystem failure')
        return rename(source, destination)

    monkeypatch.setattr(Path, 'rename', fail_rename)
    sync._run('fixture-repo')
    status = sync.get_status()
    assert status['state'] == 'error' and '备份保留' in status['message']
    backups = list(target.glob('.upstream-sync-*/previous/reports/old.md'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding='utf-8') == '# Existing report'
    assert (target / '.git.bak' / 'config').read_text(encoding='utf-8') == 'local history'


@pytest.mark.parametrize('failure', ['clone', 'missing_skills', 'missing_index', 'missing_tool'])
def test_invalid_upstream_does_not_replace_framework(framework, monkeypatch, failure):
    target, source = framework
    if failure == 'clone':
        monkeypatch.setattr(sync.subprocess, 'run', lambda command, **kwargs:
                            subprocess.CompletedProcess(command, 1, '', 'clone unavailable'))
    elif failure == 'missing_skills':
        shutil.rmtree(source / 'skills')
    elif failure == 'missing_index':
        (source / 'reports' / 'index.json').unlink()
    else:
        (source / 'tools' / 'financial_rigor.py').unlink()
    sync._run('fixture-repo')
    assert sync.get_status()['state'] == 'error'
    assert_old_framework(target)
    assert not list(target.glob('.upstream-sync-*'))
    assert not reports.CACHE_FILE.exists()


def test_temp_directory_failure_finishes_with_error(framework, monkeypatch):
    target, _ = framework

    def fail_temp(**kwargs):
        raise OSError('no temporary space')

    monkeypatch.setattr(sync.tempfile, 'mkdtemp', fail_temp)
    sync._run('fixture-repo')
    assert sync.get_status()['state'] == 'error'
    assert sync.get_status()['finished_at']
    assert_old_framework(target)


def test_sync_api_keeps_admin_authorization(env, monkeypatch):
    client, root = env
    normal = member()
    calls = []

    def start():
        calls.append(True)
        return {'state': 'running', 'message': 'sync started'}

    monkeypatch.setattr(sync, 'start_sync', start)
    assert client.post('/api/admin/sync-ai-berkshire').status_code == 401
    assert client.post('/api/admin/sync-ai-berkshire', headers=headers(normal)).status_code == 403
    assert not calls
    response = client.post('/api/admin/sync-ai-berkshire', headers=headers(root))
    assert response.status_code == 200 and response.json()['state'] == 'running'
    assert calls == [True]
