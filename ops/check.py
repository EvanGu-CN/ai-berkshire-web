"""Run local Python/JavaScript checks without SSH or production configuration."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ai-berkshire-web"


def commands(node: str | None):
    result = [
        [sys.executable, "-m", "pytest", "ops/tests", "ai-berkshire-web/tests", "-q"],
        [sys.executable, "-m", "compileall", "-q", "ops", "ai-berkshire-web/server"],
        [sys.executable, "-m", "py_compile", "update-ai-berkshire.py",
         "ai-berkshire-web/run.py", "ai-berkshire-web/migrate-data.py"],
    ]
    if node:
        result.extend([[node, "--check", str(path.relative_to(ROOT))]
                       for path in sorted((APP / "static").glob("*.js"))])
        result.append([node, "--test", *[str(path.relative_to(ROOT))
                                       for path in sorted((APP / "static/__tests__").glob("*.cjs"))]])
    result.append(["git", "diff", "--check"])
    return result


def browser_checks(node, module, env):
    # Never point synthetic UI tests at an application already using this port.
    with socket.socket() as port:
        try:
            port.bind(("127.0.0.1", 8765))
        except OSError:
            raise RuntimeError("本机 8765 端口已被占用；停止本地应用后再运行浏览器检查") from None
    output = ROOT / ".local/browser-results"
    output.mkdir(parents=True, exist_ok=True)
    test_env = {**env, "PYTHONPATH": os.pathsep.join((str(APP), str(APP / "tests"))),
                "PLAYWRIGHT_MODULE": module, "UI_TEST_OUTPUT": str(output),
                "READER_BROWSERS": env.get("READER_BROWSERS", "chromium,webkit")}
    subprocess.run([node, "-e", "require(process.argv[1])", module], cwd=ROOT, env=test_env, check=True)
    with (output / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "smoke_server:app", "--host", "127.0.0.1", "--port", "8765"],
            cwd=APP, env=test_env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            for _ in range(60):
                if server.poll() is not None:
                    raise RuntimeError("隔离测试服务启动失败，请查看 .local/browser-results/server.log")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/api/auth/policy", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("隔离测试服务启动超时")
            for script in ("ui-smoke.cjs", "reader-ui.cjs", "report-import-ui.cjs", "timeline-ui.cjs"):
                subprocess.run([node, str(APP / "tests" / script)], cwd=ROOT, env=test_env, check=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    print("browser_checks=passed screenshots=.local/browser-results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node", help="Node 可执行文件名称或路径")
    parser.add_argument("--python-only", action="store_true", help="仅检查 Python；明确跳过 JavaScript")
    parser.add_argument("--browser", action="store_true", help="额外运行隔离的桌面/手机浏览器检查")
    parser.add_argument("--playwright-module", default=os.getenv("PLAYWRIGHT_MODULE", "playwright"),
                        help="Playwright 模块名称或绝对路径，仅 --browser 使用")
    args = parser.parse_args()
    if not shutil.which("git"):
        parser.error("Git 不在 PATH 中")
    node = None if args.python_only else shutil.which(args.node)
    if not args.python_only and not node:
        parser.error("未找到 Node.js；安装 Node 或传入 --node <路径>，仅检查 Python 时用 --python-only")
    if args.python_only and args.browser:
        parser.error("--python-only 不能与 --browser 同时使用")
    temp_root = ROOT / ".local/checks"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=temp_root) as directory:
        # Do not load .env.local. Tests receive independent data and temp locations.
        env = {**os.environ, "TEMP": directory, "TMP": directory, "TMPDIR": directory,
               "VC_DATA_DIR": str(Path(directory) / "data")}
        for command in commands(node):
            print("Checking: " + " ".join(command), flush=True)
            subprocess.run(command, cwd=ROOT, env=env, check=True)
        if args.browser:
            browser_checks(node, args.playwright_module, env)
    if sys.platform == "win32":
        print("Windows: POSIX release-protocol tests are skipped; run them in an isolated Linux/WSL environment when changing deployment logic.")
    print("local_checks=passed" + (" javascript=skipped" if args.python_only else ""))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode or 1)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
