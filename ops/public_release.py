"""Export audited committed source into a new repository with no inherited history."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

from .config import ROOT, read_env

ROOT_FILES = {'.gitignore', '.env.example', '.env.app.example', 'platform.example.json',
              'README.md', 'AGENTS.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
              'THIRD_PARTY_NOTICES.md', 'update-ai-berkshire.py'}
APP_FILES = {'README.md', 'run.py', 'migrate-data.py', 'requirements.txt',
             'requirements-dev.txt', '启动.bat'}
PRIVATE_PARTS = {'.git', '.git.bak', '.local', '.venv', 'node_modules', '__pycache__',
                 'data', 'reports', 'shared-reports', 'backups', 'exports', 'ai-berkshire'}
TEXT_SUFFIXES = {'.py', '.js', '.cjs', '.css', '.html', '.svg', '.md', '.json', '.txt', '.sh', '.bat'}
# Reviewed synthetic UI captures only; arbitrary/private screenshots stay excluded.
SHOWCASE_IMAGES = {f'docs/screenshots/{name}.png' for name in
                   ('workbench', 'reports', 'reader', 'import', 'timeline', 'settings', 'mobile')}


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name or any(p in PRIVATE_PARTS for p in path.parts):
        return False
    if name in ROOT_FILES:
        return True
    if name in SHOWCASE_IMAGES:
        return True
    if len(path.parts) == 2 and path.parts[0] == 'docs':
        return path.suffix == '.md'
    if path.parts[0] == 'ops':
        return path.suffix in {'.py', '.sh', '.cjs', '.md', '.txt'} and len(path.parts) in (2, 3)
    if path.parts[0] != 'ai-berkshire-web':
        return False
    if len(path.parts) == 2:
        return path.name in APP_FILES
    if path.parts[1] == 'server':
        return len(path.parts) == 3 and path.suffix == '.py'
    if path.parts[1] == 'tests':
        return len(path.parts) == 3 and path.suffix in {'.py', '.cjs'}
    if path.parts[1] == 'static':
        return path.suffix in {'.js', '.cjs', '.css', '.html', '.svg', '.ico', '.png'}
    return False


def private_values(env_file: Path | None, deny_file: Path | None) -> list[bytes]:
    values = []
    if env_file:
        for key, value in read_env(env_file).items():
            if len(value) >= 6 and any(part in key for part in ('PASSWORD', 'TOKEN', 'SECRET', 'SSH_HOST', 'PUBLIC_URL')):
                values.append(value.encode())
    if deny_file:
        tokens = json.loads(deny_file.read_text(encoding='utf-8'))
        if not isinstance(tokens, list) or any(not isinstance(token, str) or len(token) < 3 for token in tokens):
            raise ValueError('个人标识检查文件必须是字符串数组')
        values.extend(token.encode() for token in tokens)
    return values


def scan_content(name: str, content: bytes, known: list[bytes] = ()) -> list[dict]:
    findings = []
    patterns = {
        'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'access-token': rb'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{20,})\b',
        'personal-home': rb'(?i)[A-Z]:[\\/]+Users[\\/]+(?!Public[\\/])[A-Za-z0-9_.-]+[\\/]',
        'report-export': rb'(?m)^author_id:\s*\S+',
    }
    for category, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            findings.append({'path': name, 'category': category,
                             'line': content[:match.start()].count(b'\n') + 1})
    for value in known:
        match = re.search(rb'(?<![A-Za-z0-9_])' + re.escape(value) + rb'(?![A-Za-z0-9_])', content, re.I)
        if match:
            findings.append({'path': name, 'category': 'private-value',
                             'line': content[:match.start()].count(b'\n') + 1})
    return findings


def git(root, *args, env=None):
    return subprocess.check_output(['git', *args], cwd=root, env=env, stderr=subprocess.PIPE)


def committed_source(ref: str, known=()) -> dict[str, bytes]:
    # Only explicit paths from one committed tree; no working files or Git objects copied.
    source = {}
    findings = []
    for entry in git(ROOT, 'ls-tree', '-r', '-z', ref).split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid = meta.split()
        path = name.decode('utf-8')
        if not allowed_path(path):
            continue
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError('公开源码不允许符号链接或嵌套仓库')
        content = git(ROOT, 'cat-file', 'blob', oid.decode())
        findings.extend(scan_content(path, content, known))
        source[path] = content
    if findings:
        raise ValueError('公开源码检查未通过：' + json.dumps(findings, ensure_ascii=False))
    for required in ('README.md', 'LICENSE', 'ai-berkshire-web/server/app.py', 'SECURITY.md'):
        if required not in source:
            raise ValueError('公开源码缺少必要文件：' + required)
    return source


def initialize_history(target: Path):
    env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_AUTHOR_NAME': 'AI Berkshire Web contributors', 'GIT_COMMITTER_NAME': 'AI Berkshire Web contributors',
           'GIT_AUTHOR_EMAIL': 'contributors@example.invalid', 'GIT_COMMITTER_EMAIL': 'contributors@example.invalid'}
    git(target, 'init', '--initial-branch=main', '--template=', env=env)
    git(target, '-c', 'core.autocrlf=false', 'add', '.', env=env)
    git(target, '-c', 'commit.gpgsign=false', 'commit', '-m', 'Initial public release', env=env)
    if git(target, 'rev-list', '--count', '--all').strip() != b'1':
        raise ValueError('公开仓库必须只有一个初始提交')
    parents = git(target, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
    if len(parents) != 1 or git(target, 'remote').strip():
        raise ValueError('公开仓库不得继承父提交或原远端')
    return parents[0].decode()


def export(ref: str, target: Path, *, known=(), init_git=False):
    target = target.resolve()
    if target.exists():
        raise ValueError('目标目录必须不存在，避免混入旧数据或覆盖用户文件')
    archive = target.with_suffix('.zip')
    if archive.exists():
        raise ValueError('同名压缩包已存在，未覆盖')
    files = committed_source(ref, known)
    target.mkdir(parents=True)
    for name, content in files.items():
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    # No source commit, author, remote, logs or private audit findings in the manifest.
    manifest = {'format': 1, 'files': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    (target / 'PUBLIC_RELEASE.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as out:
        for name in [*sorted(files), 'PUBLIC_RELEASE.json']:
            out.write(target / name, target.name + '/' + name)
    commit = initialize_history(target) if init_git else ''
    return {'files': len(files), 'archive': str(archive), 'root_commit': commit,
            'scan': 'passed', 'old_history_included': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='已审查并提交的源码引用')
    parser.add_argument('--output', type=Path, help='不存在的新目录；省略则只检查')
    parser.add_argument('--private-env', type=Path, help='仅本地比较已知私密值；不输出匹配值')
    parser.add_argument('--deny-file', type=Path, help='仅本地的个人标识数组，不进入公开包')
    parser.add_argument('--init-git', action='store_true', help='新目录中建立仅一个根提交的独立仓库')
    args = parser.parse_args()
    known = private_values(args.private_env, args.deny_file)
    if args.output:
        result = export(args.ref, args.output, known=known, init_git=args.init_git)
    else:
        result = {'scan': 'passed', 'files': len(committed_source(args.ref, known))}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Do not print command output, Git configuration or private input values.
        print(str(exc) if isinstance(exc, ValueError) else '公开导出失败，请检查目标目录和 Git 环境')
        raise SystemExit(1)
