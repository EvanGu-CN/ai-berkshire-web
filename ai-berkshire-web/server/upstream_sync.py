# -*- coding: utf-8 -*-
"""上游 ai-berkshire 同步：网页触发、后台线程执行。

- 同步源：https://github.com/xbtlin/ai-berkshire.git
- 剔除上游 .git/（避免嵌套仓库打断父仓库版本管理）
- 保留本地 .git.bak/
- 保留部署链接及共享目录，在目录内暂存新文件；切换失败恢复旧文件
- 完成后自动重建报告库索引
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from .paths import AI_BERKSHIRE
from .ensure_framework import UPSTREAM

_STAGING_PREFIX = ".upstream-sync-"

_status: dict = {
    "state": "idle",          # idle | running | done | error
    "message": "尚未同步",
    "upstream_commit": "",
    "started_at": "",
    "finished_at": "",
}
_lock = threading.Lock()
_thread: threading.Thread | None = None


def get_status() -> dict:
    with _lock:
        return dict(_status)


def _set(**kw) -> None:
    with _lock:
        _status.update(kw)


def start_sync(repo: str = UPSTREAM) -> dict:
    """启动后台同步。已在运行则返回当前状态。"""
    global _thread
    with _lock:
        if _status.get("state") == "running":
            return dict(_status)
        _status.update(state="running", message="正在克隆上游仓库…",
                       started_at=datetime.now().isoformat(timespec="seconds"),
                       finished_at="", upstream_commit="")
    _thread = threading.Thread(target=_run, args=(repo,), daemon=True)
    _thread.start()
    return get_status()


def _replace_framework(clone_dir: Path) -> None:
    """先备齐新文件，再更新目录内容；不删除发布链接或共享目录本身。"""
    target = AI_BERKSHIRE.resolve()
    target.mkdir(parents=True, exist_ok=True)
    # 共享目录的父目录可由 root 管理；同步只需要共享目录自身的写权限。
    work = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=target))
    incoming, previous = work / "incoming", work / "previous"
    moved: list[Path] = []
    installed: list[Path] = []
    keep_backup = False
    try:
        shutil.copytree(clone_dir, incoming,
                        ignore=shutil.ignore_patterns(".git", ".git.bak"))
        previous.mkdir()
        try:
            for entry in list(target.iterdir()):
                # 保留旧历史与可能需要人工恢复的同步备份。
                if entry.name == ".git.bak" or entry.name.startswith(_STAGING_PREFIX):
                    continue
                entry.rename(previous / entry.name)
                moved.append(entry)
            for entry in list(incoming.iterdir()):
                destination = target / entry.name
                entry.rename(destination)
                installed.append(destination)
        except Exception as install_error:
            try:
                for entry in reversed(installed):
                    entry.rename(incoming / entry.name)
                for entry in reversed(moved):
                    (previous / entry.name).rename(entry)
            except Exception as rollback_error:
                keep_backup = True
                raise RuntimeError(
                    f"切换失败，旧文件备份保留于 {previous}：{rollback_error}"
                ) from install_error
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(work, ignore_errors=True)


def _run(repo: str) -> None:
    tmp: Path | None = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="ai-berkshire-sync-"))
        clone_dir = tmp / "ai-berkshire"
        _set(message="正在克隆上游仓库…")
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", repo, str(clone_dir)],
            capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            _set(state="error",
                 message=f"克隆失败：{(proc.stderr or proc.stdout)[-200:]}",
                 finished_at=datetime.now().isoformat(timespec="seconds"))
            return

        head = subprocess.run(
            ["git", "-C", str(clone_dir), "log", "-1", "--oneline"],
            capture_output=True, text=True, check=True, timeout=30).stdout.strip()

        # 依赖面校验
        checks = {
            "skills/": (clone_dir / "skills").is_dir(),
            "reports/": (clone_dir / "reports").is_dir(),
            "reports/index.json": (clone_dir / "reports" / "index.json").is_file(),
            "tools/financial_rigor.py": (clone_dir / "tools" / "financial_rigor.py").is_file(),
        }
        missing = [k for k, exists in checks.items() if not exists]
        if missing:
            raise ValueError(f"上游仓库缺失依赖：{', '.join(missing)}")

        _set(upstream_commit=head, message="正在替换本地文件…")
        _replace_framework(clone_dir)

        # 自动重建报告库索引
        _set(message="正在重建报告库索引…")
        try:
            from . import reports_lib as rl
            rl.build_index(force=True)
        except Exception as e:  # noqa: BLE001
            _set(state="error",
                 message=f"文件已替换，但索引重建失败：{e}",
                 finished_at=datetime.now().isoformat(timespec="seconds"))
            return

        msg = f"同步完成 · {head}"
        _set(state="done", message=msg,
             finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001
        _set(state="error", message=f"同步失败：{e}",
             finished_at=datetime.now().isoformat(timespec="seconds"))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
