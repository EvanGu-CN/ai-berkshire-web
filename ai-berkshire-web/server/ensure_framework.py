# -*- coding: utf-8 -*-
"""ai-berkshire 框架保障：启动时自动拉取（代码不再入库）。

设计原则：
- 代码与数据分离 —— ai-berkshire/ 是上游代码+官方报告，不进 git
- 生成的报告存 app.db（data/ 目录），与框架更新完全隔离
- 首次启动 / 目录缺失时自动从 GitHub 克隆
- 已存在则跳过（更新走网页「同步官方仓库」或 update-ai-berkshire.py）
"""
from __future__ import annotations

import subprocess
import os
from pathlib import Path

from .paths import AI_BERKSHIRE

UPSTREAM = os.environ.get("VC_FRAMEWORK_REPO", "https://github.com/xbtlin/ai-berkshire.git")

# 最低可用标志：这三个存在才算框架完整
_MARKERS = ("skills", "reports", "tools")


def is_ready() -> bool:
    return all((AI_BERKSHIRE / m).exists() for m in _MARKERS)


def ensure_framework(repo: str = UPSTREAM, timeout: int = 1800) -> dict:
    """确保 ai-berkshire 就位。缺失则克隆，已存在则跳过。

    返回 {"ok": bool, "action": "skip"|"cloned"|"failed", "message": str}
    """
    if is_ready():
        n_skills = len(list((AI_BERKSHIRE / "skills").glob("*.md")))
        return {"ok": True, "action": "skip",
                "message": f"ai-berkshire 已就位（{n_skills} 个技能）"}

    if os.environ.get("VC_AUTO_DOWNLOAD_FRAMEWORK", "").lower() not in ("1", "true", "yes"):
        return {"ok": True, "action": "disabled",
                "message": "自动下载已关闭；首次使用可配置外部技能目录，或由管理员主动同步研究框架"}

    print(f"[AI Berkshire Web] ai-berkshire 缺失，正在从 GitHub 拉取…\n"
          f"  源：{repo}")
    try:
        AI_BERKSHIRE.parent.mkdir(parents=True, exist_ok=True)
        # 克隆到临时名再改名，避免半成品目录
        tmp = AI_BERKSHIRE.parent / "ai-berkshire.partial"
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", repo, str(tmp)],
            capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            return {"ok": False, "action": "failed",
                    "message": f"克隆失败：{(proc.stderr or proc.stdout)[-200:]}"}

        # 剔除 .git（避免嵌套仓库打断父仓库版本管理）
        gitdir = tmp / ".git"
        if gitdir.exists():
            import shutil
            shutil.rmtree(gitdir)
        tmp.rename(AI_BERKSHIRE)

        n_skills = len(list((AI_BERKSHIRE / "skills").glob("*.md")))
        print(f"[AI Berkshire Web] ai-berkshire 拉取完成（{n_skills} 个技能）")
        return {"ok": True, "action": "cloned",
                "message": f"已拉取 ai-berkshire（{n_skills} 个技能）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "failed", "message": str(e)}


if __name__ == "__main__":
    r = ensure_framework()
    print(r)
