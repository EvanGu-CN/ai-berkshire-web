"""Capture public documentation images using an isolated synthetic application."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'ai-berkshire-web'
PORT = 8766


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--playwright-module', default='playwright', help='Playwright 模块名称或绝对路径')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/showcase-output', help='截图输出目录')
    args = parser.parse_args()
    node = shutil.which('node')
    if not node:
        parser.error('需要 Node.js 与 Playwright；安装方式见 ops/README.md')
    with socket.socket() as port:
        try:
            port.bind(('127.0.0.1', PORT))
        except OSError:
            parser.error(f'本机 {PORT} 端口已被占用，未连接或修改已有服务')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temp_root = ROOT / '.local/checks'
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='showcase-', dir=temp_root) as temporary:
        # Inherit browser runtime settings, but never app data or model configuration.
        env = {key: value for key, value in os.environ.items() if not key.startswith('VC_')}
        env.update({'VC_SHOWCASE_ROOT': temporary, 'VC_SHOWCASE_TOKEN': secrets.token_urlsafe(24),
                    'PYTHONPATH': os.pathsep.join((str(APP), str(APP / 'tests'))),
                    'PLAYWRIGHT_MODULE': args.playwright_module, 'SHOWCASE_OUTPUT': str(output),
                    'TEMP': temporary, 'TMP': temporary, 'TMPDIR': temporary})
        with (output / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'showcase_server:app', '--host', '127.0.0.1', '--port', str(PORT)],
                cwd=APP, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            try:
                for _ in range(60):
                    if server.poll() is not None:
                        raise RuntimeError('截图服务启动失败，请查看输出目录 server.log')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/__showcase__', timeout=1) as response:
                            if json.load(response).get('token') == env['VC_SHOWCASE_TOKEN']:
                                break
                    except OSError:
                        pass
                    time.sleep(.25)
                else:
                    raise RuntimeError('截图服务启动超时')
                subprocess.run([node, str(ROOT / 'ops/showcase.cjs')], cwd=ROOT, env=env, check=True)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    print('showcase_capture=passed output=' + str(output))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else '截图未完成，请检查输出目录日志', file=sys.stderr)
        raise SystemExit(1)
