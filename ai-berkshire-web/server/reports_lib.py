# -*- coding: utf-8 -*-
"""报告库：以 reports/index.json 为底座 + 文件系统扫描合并，支持自动/手动更新。

- 索引缓存于内存与 data/reports_cache.json
- 过期判定：任一 .md 文件 mtime > 索引构建时间 → stale
- GET /api/reports 不做全盘扫描（快）；status/refresh 才触发扫描
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .paths import AI_BERKSHIRE, DATA_DIR, REPORTS_INDEX, REPORTS_DIR

CACHE_FILE = DATA_DIR / "reports_cache.json"

_INDEX: list[dict] | None = None
_BUILT_AT: float = 0.0

# 中间产物过滤（与 ai-berkshire 口径一致：底稿/审计/复算不进公开索引）
_DRAFT_RE = re.compile(r"底稿|核验|评审|verify|audit|对账|核查边界|证据", re.I)

DATE_IN_NAME = re.compile(r"(20[2-3]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
DATE_DASH = re.compile(r"(20[2-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)

TYPE_RULES = [
    ("底稿", ["底稿", "核验", "对账"]),
    ("财报", ["earnings", "财报", "中报", "年报", "季报", "业绩", "电话会"]),
    ("公众号", ["公众号", "wechat", "推文"]),
    ("深度系列", ["看懂", "深度研究", "deep-dive"]),
    ("横评对比", ["横评", "对比", "vs", "对决", "换仓", "轮动"]),
    ("组织管理", ["管理层", "management"]),
    ("估值仓位", ["估值", "赔率", "凯利", "仓位", "valuation", "终值"]),
    ("筛选", ["funnel", "筛选", "召回", "screen", "候选池"]),
]


def _load_index_json() -> list[dict]:
    if not REPORTS_INDEX.is_file():
        return []
    try:
        data = json.loads(REPORTS_INDEX.read_text(encoding="utf-8"))
        return data.get("reports") or []
    except (json.JSONDecodeError, OSError):
        return []


def _file_date(path: Path) -> str:
    name = path.name
    m = DATE_IN_NAME.search(name) or DATE_IN_NAME.search(str(path.parent.name))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = DATE_DASH.search(name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""


def _guess_type(rel: str) -> str:
    low = rel.lower()
    for tname, kws in TYPE_RULES:
        if any(k.lower() in low for k in kws):
            return tname
    return "研究"


def _scan_filesystem() -> list[dict]:
    """扫描 reports/ 目录，返回全部合格 .md 条目（不含 index.json 特有字段）。"""
    items: list[dict] = []
    if not REPORTS_DIR.is_dir():
        return items
    for dirpath, dirnames, filenames in os.walk(REPORTS_DIR):
        dirnames[:] = [d for d in dirnames if not d.startswith("_") and d != ".git"]
        for fn in filenames:
            if not fn.endswith(".md") or fn == "README.md":
                continue
            fp = Path(dirpath) / fn
            rel = fp.relative_to(AI_BERKSHIRE).as_posix()
            if _DRAFT_RE.search(rel):
                continue
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = H1.search(content[:2000])
            title = m.group(1).strip() if m else fp.stem
            grp = fp.parent.name if fp.parent != REPORTS_DIR else "专题"
            items.append({
                "title": title,
                "path": rel,
                "bucket": "公司" if fp.parent != REPORTS_DIR else "专题",
                "group": grp,
                "series": "",
                "type": _guess_type(rel),
                "date": _file_date(fp),
                "date_source": "文件名",
                "ticker": "",
            })
    return items


def build_index(force: bool = False) -> dict:
    """构建合并索引：index.json 元数据优先，文件系统补新增。"""
    global _INDEX, _BUILT_AT
    if _INDEX is not None and not force:
        return status()

    known: dict[str, dict] = {}
    for it in _load_index_json():
        p = it.get("path")
        if p:
            known[p.replace("\\", "/")] = it

    merged: list[dict] = list(known.values())
    seen = set(known.keys())
    for it in _scan_filesystem():
        if it["path"] not in seen:
            merged.append(it)
            seen.add(it["path"])

    # 按日期倒序（新的在前），同日期按标题
    merged.sort(key=lambda x: (x.get("date") or "", x.get("title") or ""), reverse=True)
    _INDEX = merged
    _BUILT_AT = time.time()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(
            {"built_at": _BUILT_AT, "items": merged}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass
    return status()


def _load_cache() -> None:
    global _INDEX, _BUILT_AT
    if _INDEX is not None:
        return
    if CACHE_FILE.is_file():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            _INDEX = data.get("items") or []
            _BUILT_AT = float(data.get("built_at") or 0)
            return
        except (json.JSONDecodeError, OSError):
            pass
    build_index(force=True)


def status() -> dict:
    """索引状态 + 过期判定（比较最新 md mtime 与构建时间）。"""
    _load_cache()
    newest = 0.0
    if REPORTS_DIR.is_dir():
        for dirpath, dirnames, filenames in os.walk(REPORTS_DIR):
            dirnames[:] = [d for d in dirnames if not d.startswith("_")]
            for fn in filenames:
                if fn.endswith(".md"):
                    try:
                        mt = os.path.getmtime(os.path.join(dirpath, fn))
                        newest = max(newest, mt)
                    except OSError:
                        pass
    stale = newest > (_BUILT_AT or 0)
    return {
        "count": len(_INDEX or []),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(_BUILT_AT)) if _BUILT_AT else "",
        "stale": stale,
    }


def _norm_path(rel: str) -> Path:
    """将 index 里的相对路径解析到 reports 目录内，防目录穿越。"""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    candidate = (AI_BERKSHIRE / rel).resolve()
    root = REPORTS_DIR.resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError("非法路径")
    return candidate


def all_items() -> list[dict]:
    """返回全部索引条目（供 memory 索引等复用）。"""
    _load_cache()
    return list(_INDEX or [])


def index_token() -> tuple:
    """Cheap metadata-index revision; no filesystem scan or report-body read."""
    _load_cache()
    return _BUILT_AT, id(_INDEX), len(_INDEX or [])


def list_reports(q: str = "", group: str = "", bucket: str = "",
                 rtype: str = "", limit: int = 60, offset: int = 0) -> dict:
    _load_cache()
    st = status()
    if st["stale"]:
        build_index(force=True)      # 自动更新：发现新报告直接重建
    items = _INDEX or []
    q_lower = (q or "").strip().lower()

    def match(it: dict) -> bool:
        if group and it.get("group") != group:
            return False
        if bucket and it.get("bucket") != bucket:
            return False
        if rtype and it.get("type") != rtype:
            return False
        if q_lower:
            hay = f"{it.get('title','')} {it.get('group','')} {it.get('path','')}".lower()
            if q_lower not in hay:
                return False
        return True

    filtered = [it for it in items if match(it)]
    page = filtered[offset:offset + limit]
    groups = sorted({it.get("group") for it in items if it.get("group")})
    types = sorted({it.get("type") for it in items if it.get("type")})
    buckets = sorted({it.get("bucket") for it in items if it.get("bucket")})
    return {
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "items": page,
        "facets": {"groups": groups[:80], "types": types, "buckets": buckets},
        "index_count": len(items),
        "built_at": st["built_at"],
    }


def get_report(path: str) -> dict | None:
    try:
        fp = _norm_path(path)
    except ValueError:
        return None
    if not fp.is_file():
        return None
    try:
        content = fp.read_text(encoding="utf-8")
    except OSError:
        return None
    title = fp.stem
    m = re.search(r"^#\s+(.+)$", content, re.M)
    if m:
        title = m.group(1).strip()
    return {
        "path": path,
        "title": title,
        "content": content,
        "size": len(content),
        "group": fp.parent.name,
    }
