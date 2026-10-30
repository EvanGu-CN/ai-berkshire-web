"""Package committed source only, reuse the installed framework, back up and release."""
from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import PurePosixPath
import shlex
import subprocess
import tarfile
import uuid

from .config import ROOT
from .remote import health, run


def release_error(output, code):
    """Describe known release failures without exposing raw command/provider output."""
    lines = output.splitlines()
    if any('active_research_tasks;' in line for line in lines):
        reason = '有研究任务正在运行，请等待完成后重新发布'
    elif 'deployment_locked' in lines:
        reason = '已有发布正在进行，请等待完成'
    elif 'invalid_current_release' in lines:
        reason = '当前版本目录不符合配置，请检查 VC_REMOTE_DIR'
    else:
        phases = {'prepare': '准备版本', 'backup': '备份数据', 'activate': '切换版本',
                  'health': '服务健康检查', 'continuity': '数据连续性检查'}
        phase = next((phases[line.split('=', 1)[1]] for line in reversed(lines)
                      if line.startswith('deployment_phase=') and line.split('=', 1)[1] in phases), '发布预检')
        reason = phase + '失败，请定向检查；原始命令输出未展示'
    if 'previous_release_restarted=true' in lines:
        reason += '；已尝试恢复原版本，请运行 status 确认'
    return f'发布失败（exit {code}）：{reason}'


def validate_members(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError("发布包包含不安全路径")
        if any(part in ('.git', '.venv', 'data', 'reports', 'shared-reports', '.local', '__pycache__') for part in path.parts):
            raise ValueError("发布包包含运行数据或本地配置")
        if any(part == '.env' or (part.startswith('.env.') and part not in ('.env.example', '.env.app.example')) for part in path.parts):
            raise ValueError("发布包包含私密环境配置")


def package_commit(ref):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    commit = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    raw = git('archive', '--format=tar', commit, '--', 'ai-berkshire-web', 'ops', 'update-ai-berkshire.py')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = archive.getmembers()
        validate_members(m.name for m in members)
        if any(m.issym() or m.islnk() or not (m.isfile() or m.isdir()) for m in members):
            raise ValueError("发布包只允许普通文件和目录")
        # Execute the release protocol from the same commit as the application.
        script = archive.extractfile('ops/deploy.sh').read().decode('utf-8')
    return commit, gzip.compress(raw, mtime=0), script


def deploy_commit(client, cfg, ref):
    commit, content, script = package_commit(ref)
    archive = f'/tmp/ai-berkshire-web-{commit}-{uuid.uuid4().hex}.tar.gz'
    values = {
        'VC_REMOTE_DIR': cfg.remote_dir, 'VC_DATA_DIR': cfg.data_dir,
        'VC_BACKUP_DIR': cfg.backup_dir, 'VC_REMOTE_ENV': cfg.remote_env,
        'VC_SERVICE': cfg.service, 'VC_HEALTH_URL': cfg.health_url,
        'VC_COMMIT': commit, 'VC_ARCHIVE': archive,
        'VC_ARCHIVE_SHA256': hashlib.sha256(content).hexdigest(),
    }
    print(f'Uploading committed release {commit[:12]} ({len(content)} bytes)', flush=True)
    with client.open_sftp() as sftp:
        sftp.putfo(io.BytesIO(content), archive)
        sftp.chmod(archive, 0o600)
    assignments = ' '.join(f'{name}={shlex.quote(value)}' for name, value in values.items())
    print('Preparing release, backing up data, and checking continuity', flush=True)
    output, code = run(client, assignments + ' bash -s', input_text=script, timeout=1200)
    if code:
        raise RuntimeError(release_error(output, code))
    # Only report fixed status messages and the verified commit; no raw process logs.
    for line in output.splitlines():
        if line in ('data_continuity=passed', 'local_health=passed') or line.startswith(('deployed_commit=', 'backup_dir=')):
            print(line)
    health(client, cfg)
