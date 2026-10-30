#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Berkshire Web 数据迁移工具 — 跨环境搬迁网站生成的报告与用户数据

迁移单元 = data/app.db（用户 + API Key + 全站共享报告池，一个文件全带走）

用法：
    python migrate-data.py export [备份文件.zip]     # 导出（默认 data/backup-日期.zip）
    python migrate-data.py import <备份文件.zip>     # 导入（自动备份旧库为 app.db.bak-时间戳）
    python migrate-data.py status                   # 查看当前数据概况

本工具用于显式数据迁移，不是常规代码发布步骤。常规生产发布见 ../ops/README.md。
CLI 读取进程中的 VC_DATA_DIR，不会自动加载根 .env.local 或 systemd 环境文件。

典型流程（在应用目录执行，A 环境 → B 环境）：
    A: python migrate-data.py export ../.local/backups/backup.zip
    —— 单独安全传输备份到 B 环境，不提交 Git ——
    B: python migrate-data.py import ../.local/backups/backup.zip
    B: python run.py    # 首次启动自动拉取 ai-berkshire，报告/用户即刻可用
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import data_migrate as dm  # noqa: E402
from server.paths import DATA_DIR  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1].lower()

    if cmd == "export":
        dest = (Path(sys.argv[2]) if len(sys.argv) > 2
                else DATA_DIR / f"backup-{__import__('time').strftime('%Y%m%d-%H%M%S')}.zip")
        r = dm.export_data(dest)
        print("✔ 导出成功" if r.get("ok") else "✘ 导出失败",
              r.get("message", ""))
        if r.get("ok"):
            print(f"  文件：{r['path']}")
            print(f"  大小：{r['size'] / 1024:.0f} KB")
            print(f"  内容：{r['counts']}")
            print("  ⚠️ 含用户密码哈希与 API Key，请勿公开传播")
        return 0 if r.get("ok") else 1

    if cmd == "import":
        if len(sys.argv) < 3:
            print("用法：python migrate-data.py import <备份文件.zip>")
            return 1
        r = dm.import_data(sys.argv[2])
        if not r.get("ok"):
            print("✘ 导入失败：", r.get("message"))
            return 1
        print("✔ 导入成功")
        print(f"  当前数据：{r['counts']}")
        if r.get("backup_path"):
            print(f"  旧库已备份到：{r['backup_path']}")
        if r.get("manifest", {}).get("exported_at"):
            print(f"  备份导出于：{r['manifest']['exported_at']}")
        print("  下一步：python run.py 启动服务（缺 ai-berkshire 会自动拉取）")
        return 0

    if cmd == "status":
        counts = dm._db_counts()
        print(f"数据目录：{DATA_DIR}")
        print(f"app.db  ：{'存在' if dm.APP_DB.is_file() else '不存在'}")
        print(f"数据概况：{counts or '（无）'}")
        mem = DATA_DIR / "memory.db"
        if mem.is_file():
            print(f"memory.db：{mem.stat().st_size / 1024 / 1024:.0f} MB（衍生索引，可重建，无需迁移）")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
