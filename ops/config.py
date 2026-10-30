"""Local SSH configuration. Never import or expose app/model secrets."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"配置文件第 {number} 行缺少等号")
        key, value = (part.strip() for part in line.split("=", 1))
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"配置文件第 {number} 行键名无效")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValueError(f"配置文件第 {number} 行引号无效") from None
            if not isinstance(value, str):
                raise ValueError(f"配置文件第 {number} 行必须是字符串")
        elif value.startswith("'"):
            if not value.endswith("'") or len(value) < 2:
                raise ValueError(f"配置文件第 {number} 行引号无效")
            value = value[1:-1]
        values[key] = value
    return values


def remote_path(value: str) -> str:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or str(path) == "/" or any(c in value for c in "\n\r\x00"):
        raise ValueError("远程目录必须是非根目录的绝对路径，且不可包含上级路径")
    return str(path)


@dataclass(frozen=True)
class Config:
    host: str
    user: str
    port: int = 22
    password: str = field(default="", repr=False)
    key_file: str = field(default="", repr=False)
    host_key_sha256: str = ""
    known_hosts: Path = ROOT / ".local/known_hosts"
    remote_dir: str = "/opt/ai-berkshire-web"
    data_dir: str = "/var/lib/ai-berkshire-web"
    backup_dir: str = "/var/backups/ai-berkshire-web"
    service: str = "ai-berkshire-web.service"
    remote_env: str = "/etc/ai-berkshire-web.env"
    health_url: str = "http://127.0.0.1:8765/api/auth/policy"
    public_url: str = ""

    @classmethod
    def load(cls, path: Path | None = None, environ: dict | None = None) -> "Config":
        target = Path(path).resolve() if path else ROOT / ".env.local"
        values = read_env(target)
        values.update(os.environ if environ is None else environ)
        def value(name, default=""):
            return values.get("VC_" + name, default)
        def local_path(name, default=""):
            raw = os.path.expandvars(os.path.expanduser(value(name, default)))
            return str((target.parent / raw).resolve()) if raw else ""
        try:
            port = int(value("SSH_PORT", "22"))
        except ValueError:
            raise ValueError("VC_SSH_PORT 必须是整数") from None
        if not 1 <= port <= 65535:
            raise ValueError("VC_SSH_PORT 超出范围")
        host, user = value("SSH_HOST").strip(), value("SSH_USER").strip()
        if not host or not user:
            raise ValueError("请配置 VC_SSH_HOST 和 VC_SSH_USER")
        key_file, password = local_path("SSH_KEY_FILE"), value("SSH_PASSWORD")
        if not key_file and not password:
            raise ValueError("请配置 VC_SSH_KEY_FILE 或 VC_SSH_PASSWORD")
        if key_file and not Path(key_file).is_file():
            raise ValueError("VC_SSH_KEY_FILE 不存在")
        service = value("SERVICE", "ai-berkshire-web.service")
        if not re.fullmatch(r"[A-Za-z0-9_@.-]+\.service", service) or service.startswith("-"):
            raise ValueError("VC_SERVICE 无效")
        pin = value("SSH_HOST_KEY_SHA256")
        if pin and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}=?", pin):
            raise ValueError("VC_SSH_HOST_KEY_SHA256 无效")
        urls = [value("HEALTH_URL", cls.health_url), value("PUBLIC_URL")]
        for url in filter(None, urls):
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("健康检查 URL 无效或包含凭据")
        return cls(host=host, user=user, port=port, password=password, key_file=key_file,
                   host_key_sha256=pin, known_hosts=Path(local_path("SSH_KNOWN_HOSTS", ".local/known_hosts")),
                   remote_dir=remote_path(value("REMOTE_DIR", cls.remote_dir)),
                   data_dir=remote_path(value("DATA_DIR", cls.data_dir)),
                   backup_dir=remote_path(value("BACKUP_DIR", cls.backup_dir)), service=service,
                   remote_env=remote_path(value("REMOTE_ENV", cls.remote_env)),
                   health_url=urls[0], public_url=urls[1])
