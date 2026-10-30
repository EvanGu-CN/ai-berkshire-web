# -*- coding: utf-8 -*-
"""启动入口：python run.py → http://127.0.0.1:8765

启动流程：自动补齐缺失依赖 → 保障 ai-berkshire 框架（缺失则从 GitHub 拉取）
→ 端口占用检测 → 启动 FastAPI 服务。

这是本地应用入口，不读取仓库根的 SSH .env.local。生产服务由已有 systemd
配置启动，常规代码发布从仓库根使用 python -m ops.remote deploy。
"""
import subprocess
import sys

REQUIRED = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "requests": "requests",
    "multipart": "python-multipart",   # FastAPI 文件上传（数据迁移导入）
    "tzdata": "tzdata",
}

# pip 源回退链：默认源 → 官方 PyPI → 清华镜像（国内网络兜底）
PIP_INDEXES = [
    [],
    ["--index-url", "https://pypi.org/simple"],
    ["--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"],
]


def ensure_deps() -> None:
    """缺失的包用当前解释器的 pip 自动安装（只装缺的，多源回退）。"""
    missing = [pkg for mod, pkg in REQUIRED.items() if _missing(mod)]
    if not missing:
        return
    print(f"[AI Berkshire Web] 正在安装缺失依赖: {', '.join(missing)} …")
    for index_args in PIP_INDEXES:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", *index_args, *missing]
            )
            return
        except subprocess.CalledProcessError:
            continue
    print("[AI Berkshire Web] 依赖安装失败（已尝试默认源 / PyPI / 清华镜像）。")
    print("  可手动执行: "
          f"{sys.executable} -m pip install {' '.join(missing)}")
    sys.exit(1)


def _missing(mod: str) -> bool:
    try:
        __import__(mod)
        return False
    except ImportError:
        return True


if __name__ == "__main__":
    ensure_deps()

    # ai-berkshire 框架保障：缺失则自动从 GitHub 拉取（代码不入库）
    try:
        from server.ensure_framework import ensure_framework
        fw = ensure_framework()
        if not fw["ok"]:
            print(f"[AI Berkshire Web] ⚠️ {fw['message']}")
            print("  框架缺失时技能/报告库不可用；可稍后在网页「报告库 → 同步官方仓库」重试，")
            print(f"  或手动执行：git clone --depth 1 https://github.com/xbtlin/ai-berkshire.git ai-berkshire")
    except Exception as e:  # noqa: BLE001 —— 框架保障失败不阻塞启动
        print(f"[AI Berkshire Web] ⚠️ 框架检查跳过：{e}")

    import uvicorn

    from server.app import app

    host, port = "127.0.0.1", 8765

    # 端口被占用 → 友好提示（多半是服务已在运行）
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            print(f"[AI Berkshire Web] 端口 {port} 已被占用，服务可能已在运行。")
            print(f"  请直接访问 http://{host}:{port}")
            sys.exit(0)

    print(f"[AI Berkshire Web] 服务启动中… 浏览器打开 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
