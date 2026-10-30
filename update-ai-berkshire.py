#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步上游 ai-berkshire 到本项目（一键升级脚本）

仅更新执行机器上的本地研究框架，不提交代码、不发布生产服务。
常规生产更新使用 python -m ops.remote deploy；不要在生产 current 的共享框架链接上运行本整目录替换脚本。

用法：
    python update-ai-berkshire.py            # 拉取最新并替换
    python update-ai-berkshire.py --dry-run  # 只预览将要做什么，不改动

上游仓库：https://github.com/xbtlin/ai-berkshire.git

替换规则（保证 ai-berkshire-web 无缝使用）：
  1. 克隆上游最新代码到临时目录
  2. 整目录替换 ai-berkshire/ 的内容
  3. 剔除上游自带的 .git/（避免嵌套仓库打断父仓库 AI Berkshire Web 的版本管理）
  4. 保留本地的 .git.bak/（原作者仓库的历史备份，不动）
  5. 提示后续动作（报告库自动重建 / 研究记忆需手动重建索引）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "ai-berkshire"
UPSTREAM = "https://github.com/xbtlin/ai-berkshire.git"

# 本地保留、不被上游覆盖的目录/文件
PRESERVE = {".git.bak"}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser(description="同步本机 ai-berkshire 研究框架（不执行生产部署）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只显示将执行的操作，不做任何改动")
    ap.add_argument("--repo", default=UPSTREAM, help="上游 git 地址")
    args = ap.parse_args()

    print(f"[sync] 上游仓库：{args.repo}")
    print(f"[sync] 目标目录：{TARGET}")

    if not args.dry_run and not TARGET.is_dir():
        print(f"[sync] 目标目录不存在，将全新创建：{TARGET}")

    # ---- 1. 克隆上游最新 ----
    tmp = Path(tempfile.mkdtemp(prefix="ai-berkshire-sync-"))
    clone_dir = tmp / "ai-berkshire"
    try:
        print("[sync] 正在克隆上游最新代码 …")
        if args.dry_run:
            print(f"[sync] (dry-run) 将执行: git clone --depth 1 {args.repo} {clone_dir}")
        else:
            run(["git", "clone", "--depth", "1", args.repo, str(clone_dir)])
            head = run(["git", "-C", str(clone_dir), "log", "-1", "--oneline"]).stdout.strip()
            print(f"[sync] 上游最新提交：{head}")

        # 统计将引入的内容
        if clone_dir.is_dir():
            n_skills = len(list((clone_dir / "skills").glob("*.md"))) \
                if (clone_dir / "skills").is_dir() else 0
            n_reports = sum(1 for _ in (clone_dir / "reports").rglob("*.md")) \
                if (clone_dir / "reports").is_dir() else 0
            print(f"[sync] 上游内容：skills {n_skills} 个 / reports {n_reports} 份")

        # ---- 2. 保留本地 .git.bak ----
        kept_gitbak = None
        if TARGET.is_dir() and (TARGET / ".git.bak").exists():
            kept_gitbak = tmp / "kept.git.bak"
            if args.dry_run:
                print("[sync] (dry-run) 将保留本地 ai-berkshire/.git.bak")
            else:
                shutil.move(str(TARGET / ".git.bak"), str(kept_gitbak))
                print("[sync] 已暂存本地 .git.bak（原作者历史，替换后放回）")

        # ---- 3. 整目录替换 ----
        if args.dry_run:
            print("[sync] (dry-run) 将清空并替换 ai-berkshire/ 内容（不含 .git.bak）")
            print("[sync] (dry-run) 将剔除上游 .git/，避免嵌套仓库")
        else:
            if TARGET.exists():
                shutil.rmtree(TARGET)
            # 复制上游内容，但排除 .git
            def _ignore(directory: str, names: list[str]) -> set[str]:
                return {n for n in names if n == ".git"}
            shutil.copytree(clone_dir, TARGET, ignore=_ignore)
            print("[sync] 已替换 ai-berkshire/ 内容（已剔除 .git/）")

            # ---- 4. 放回 .git.bak ----
            if kept_gitbak and kept_gitbak.exists():
                shutil.move(str(kept_gitbak), str(TARGET / ".git.bak"))
                print("[sync] 已恢复 .git.bak")

        # ---- 5. 校验 web 服务依赖面 ----
        checks = {
            "skills/": TARGET / "skills",
            "reports/": TARGET / "reports",
            "reports/index.json": TARGET / "reports" / "index.json",
            "tools/financial_rigor.py": TARGET / "tools" / "financial_rigor.py",
        }
        print("[sync] web 服务依赖检查：")
        ok = True
        for label, p in checks.items():
            exists = p.exists()
            ok = ok and exists
            print(f"   {'✔' if exists else '✘'} {label}")
        if not ok:
            print("[sync] ⚠️ 有依赖缺失——ai-berkshire-web 的对应功能会降级，"
                  "请检查上游目录结构是否变更（paths.py 可能需要同步调整）")

        if not args.dry_run:
            print()
            print("[sync] 完成。后续建议：")
            print("   1. 报告库：打开网页 →「报告库」→ 点「⟳ 更新索引」"
                  "（或等自动过期检测）")
            print("   2. 研究记忆：如有报告增删 →「研究记忆」→ 点「重建索引」")
            print("   3. 提交版本：git add ai-berkshire && git commit")
            print("   4. 重启 ai-berkshire-web 服务（技能/参数如有变更）")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
