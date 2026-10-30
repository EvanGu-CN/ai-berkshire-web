"""本地配置直连 SSH：status 查看健康，logs 查看日志，exec 执行命令，deploy 发布 Git 提交。"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time
import urllib.request

import paramiko

from .config import Config


def fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class RememberHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def missing_host_key(self, client, hostname, key):
        if self.cfg.host_key_sha256 and fingerprint(key) != self.cfg.host_key_sha256.rstrip("="):
            raise paramiko.SSHException("SSH 主机指纹与配置不符")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self.cfg.known_hosts))


def connect(cfg: Config):
    cfg.known_hosts.parent.mkdir(parents=True, exist_ok=True)
    client = paramiko.SSHClient()
    if cfg.known_hosts.exists():
        client.load_host_keys(str(cfg.known_hosts))
    client.set_missing_host_key_policy(RememberHostKey(cfg))
    try:
        client.connect(hostname=cfg.host, port=cfg.port, username=cfg.user,
                       password=cfg.password or None, key_filename=cfg.key_file or None,
                       look_for_keys=False, allow_agent=False, timeout=20,
                       banner_timeout=20, auth_timeout=20)
        transport = client.get_transport()
        if cfg.host_key_sha256 and fingerprint(transport.get_remote_server_key()) != cfg.host_key_sha256.rstrip("="):
            raise paramiko.SSHException("SSH 主机指纹与配置不符")
        transport.set_keepalive(15)
        return client
    except Exception:
        client.close()
        raise


def run(client, command: str, *, input_text: str | None = None, timeout: int = 120) -> tuple[str, int]:
    """Drain both channels together to avoid deadlocks on large stderr output."""
    stdin, stdout, _ = client.exec_command(command, timeout=timeout)
    if input_text is not None:
        stdin.write(input_text)
        stdin.flush()
    stdin.channel.shutdown_write()
    channel = stdout.channel
    chunks = []
    deadline = time.monotonic() + timeout
    while True:
        while channel.recv_ready():
            chunks.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            chunks.append(channel.recv_stderr(65536))
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            return b"".join(chunks).decode("utf-8", "replace"), channel.recv_exit_status()
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError("远程命令超时")
        time.sleep(0.05)


def checked(client, command, **kwargs):
    output, code = run(client, command, **kwargs)
    if code:
        # Provider responses and runtime configuration can appear in server logs.
        # Do not surface an arbitrary failed command's output implicitly.
        raise RuntimeError(f"远程操作失败（exit {code}）；请用 logs 或 exec 定向检查")
    return output


def health(client, cfg):
    checked(client, f"systemctl is-active --quiet {shlex.quote(cfg.service)}")
    checked(client, f"curl -fsS --max-time 10 {shlex.quote(cfg.health_url)} >/dev/null")
    if cfg.public_url:
        with urllib.request.urlopen(cfg.public_url, timeout=15) as response:
            if response.status != 200 or not isinstance(json.load(response), dict):
                raise RuntimeError("公网健康检查失败")
    print("service=active local_health=passed" + (" public_health=passed" if cfg.public_url else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="默认使用仓库根 .env.local；环境变量优先")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="检查服务及内外网健康，显示当前版本目录")
    logs = sub.add_parser("logs", help="按时间和行数读取服务日志到本机")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--since", default="1 hour ago")
    command = sub.add_parser("exec", help="执行远程命令或本地 shell 文件")
    command.add_argument("command", nargs="?")
    command.add_argument("--script", type=Path, help="通过标准输入执行本地 shell 文件")
    command.add_argument("--timeout", type=int, default=120)
    deploy = sub.add_parser("deploy", help="备份数据并发布已检查、已推送的 Git 提交")
    deploy.add_argument("--ref", default="HEAD", help="Git 提交/引用，默认 HEAD；不包含未提交文件")
    args = parser.parse_args()
    cfg = Config.load(args.env_file)
    if args.action == "exec" and bool(args.command) == bool(args.script):
        parser.error("exec 需要提供 command 或 --script 其中一项")
    if args.action == "logs" and not 1 <= args.lines <= 10000:
        parser.error("--lines 必须为 1–10000")
    client = connect(cfg)
    try:
        if args.action == "status":
            health(client, cfg)
            output = checked(client, f"readlink -f {shlex.quote(cfg.remote_dir + '/current')}")
            print("release=" + output.strip())
        elif args.action == "logs":
            print(checked(client, f"journalctl -u {shlex.quote(cfg.service)} -n {args.lines} "
                          f"--since {shlex.quote(args.since)} --no-pager"), end="")
        elif args.action == "exec":
            output, code = run(client, "bash -s" if args.script else args.command,
                               input_text=args.script.read_text(encoding="utf-8-sig") if args.script else None,
                               timeout=args.timeout)
            print(output, end="")
            if code:
                raise RuntimeError(f"远程命令失败（exit {code}）")
        else:
            from .release import deploy_commit
            deploy_commit(client, cfg, args.ref)
    finally:
        client.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as exc:
        # Config errors are deliberately value-free; network/library errors may not be.
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else
              f"远程操作未完成：{type(exc).__name__}（原始响应未输出）", file=sys.stderr)
        sys.exit(1)
