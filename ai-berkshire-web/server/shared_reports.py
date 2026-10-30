# -*- coding: utf-8 -*-
"""共享报告池：网站用户生成的研究报告（全站共享）。

- 数据源：app.db 的 history 表（所有用户），作者信息 join users
- 与官方报告（ai-berkshire/reports）合并成统一报告库
- 重复检测：同标的近期已有报告 → 提示复用，省 token
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import auth
from . import reports_lib as rl
from . import report_imports as ri
from . import report_dates as rd

GEN_PREFIX = "gen:"

# 简单公司名抽取：取第一个分隔符前的片段，去掉数字/百分号/空格
_COMPANY_SPLIT = re.compile(r"[,，、;；/\s]+")


def extract_keyword(arguments: str) -> str:
    """从研究参数里取主标的名，用于重复检测。"""
    args = (arguments or "").strip()
    if not args:
        return ""
    first = _COMPANY_SPLIT.split(args)[0]
    first = re.sub(r"[\d.]+%?$", "", first).strip()
    return first[:24]


def _row_to_item(r, username: str | None = None) -> dict:
    content = r["content"] or ""
    return {
        "id": r["id"],
        "path": GEN_PREFIX + r["id"],
        "source": "generated",
        "title": r["skill_title"] or r["skill_id"],
        "group": extract_keyword(r["arguments"] or "") or "未分类",
        "bucket": "共享研报",
        "type": r["skill_id"] or "",
        "date": rd.date_text(r["created_at"]),
        "created_at": r["created_at"],
        "author": username or "",
        "author_id": r["user_id"],
        "arguments": r["arguments"] or "",
        "model": r["model"] or "",
        "chars": r["chars"] or len(content),
        "preview": content[:160].replace("\n", " "),
        "ticker": "",
        "series": "",
    }


def list_generated(q: str = "", scope: str = "all", user_id: str = "",
                   group: str = "", limit: int | None = 2000) -> list[dict]:
    """列出用户生成的共享报告。scope: all | mine | others"""
    sql = ("SELECT h.id,h.user_id,h.skill_id,h.skill_title,h.arguments,h.model,h.created_at,h.chars,"
           "substr(h.content,1,160) AS content,u.username AS username FROM history h "
           "LEFT JOIN users u ON u.id = h.user_id WHERE 1=1")
    args: list = []
    if scope == "mine" and user_id:
        sql += " AND h.user_id=?"
        args.append(user_id)
    elif scope == "others" and user_id:
        sql += " AND h.user_id!=?"
        args.append(user_id)
    q_lower = (q or "").strip().lower()
    if q_lower:
        sql += (" AND (LOWER(h.skill_title) LIKE ? OR LOWER(h.arguments) LIKE ? "
                "OR LOWER(h.content) LIKE ?)")
        args += [f"%{q_lower}%"] * 3
    sql += " ORDER BY h.created_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)

    with auth._lock:
        conn = auth.connect()
        try:
            rows = conn.execute(sql, args).fetchall()
            items = [_row_to_item(r, r["username"]) for r in rows]
        finally:
            conn.close()

    if group:
        items = [it for it in items if it["group"] == group]
    return items


def get_generated(record_id: str) -> dict | None:
    with auth._lock:
        conn = auth.connect()
        try:
            r = conn.execute(
                "SELECT h.*, u.username AS username FROM history h "
                "LEFT JOIN users u ON u.id = h.user_id WHERE h.id=?",
                (record_id,)).fetchone()
            if not r:
                return None
            return {
                "path": GEN_PREFIX + r["id"],
                "source": "generated",
                "title": r["skill_title"] or r["skill_id"],
                "content": r["content"] or "",
                "size": r["chars"] or 0,
                "group": extract_keyword(r["arguments"] or "") or "共享研报",
                "author": r["username"] or "",
                "author_id": r["user_id"],
                "created_at": r["created_at"],
                "arguments": r["arguments"] or "",
                "skill_id": r["skill_id"],
                "model": r["model"] or "",
            }
        finally:
            conn.close()


def all_items() -> list[dict]:
    """全部在库报告的轻量目录，供时间线使用；不依赖全文或结论索引。"""
    with auth._lock:
        conn = auth.connect()
        try:
            rows = conn.execute(
                "SELECT h.id,h.user_id,h.skill_id,h.skill_title,h.arguments,h.model,h.created_at,h.chars,"
                "'' AS content,u.username FROM history h LEFT JOIN users u ON u.id=h.user_id").fetchall()
            generated = [_row_to_item(row, row["username"]) for row in rows]
        finally:
            conn.close()
    return ([dict(item, source="official") for item in rl.all_items()]
            + generated + ri.list_reports())


def get_report(path: str) -> dict | None:
    """按统一报告路径读取正文，保留来源标识。"""
    if path.startswith(GEN_PREFIX):
        return get_generated(path[len(GEN_PREFIX):])
    if path.startswith(ri.PREFIX):
        return ri.get_report(path[len(ri.PREFIX):])
    doc = rl.get_report(path)
    return dict(doc, source="official") if doc else None


RECENT_PERIODS = rd.PERIODS


def _today() -> date:
    return rd.today()


def _report_day(item: dict) -> date | None:
    return rd.report_day(item)


def recent_matches(keyword: str, days: int | None = None, limit: int = 8,
                   user_id: str = "", *, period: str | None = None, offset: int = 0) -> dict:
    """Metadata-only discovery, with one date window and pagination for all sources."""
    today = _today()
    period = period or ("custom" if days is not None else "6m")
    if period == "custom":
        days = max(1, min(days or 30, 365))
        since = today - timedelta(days=days - 1)
        label = f"近{days}天"
    elif period in RECENT_PERIODS:
        since = rd.DateFilter.build(period, asof=today).since
        label = RECENT_PERIODS[period]
    else:
        raise ValueError("不支持的报告时间范围")
    kw = (keyword or "").strip()
    offset, limit = max(0, offset), max(1, min(limit, 50))
    matches = []
    if kw:
        for item in all_items():
            hay = " ".join(str(item.get(key) or "") for key in ("title", "group", "arguments"))
            if kw.casefold() not in hay.casefold():
                continue
            report_day = _report_day(item)
            if report_day is None or not since <= report_day <= today:
                continue
            matches.append(dict(item, date=report_day.isoformat(),
                                is_mine=bool(user_id) and item.get("author_id") == user_id))
    matches.sort(key=lambda item: (item["date"], item["path"]), reverse=True)
    page = matches[offset:offset + limit]
    return {"keyword": kw, "period": period, "period_label": label,
            "days": (today - since).days + 1, "since": since.isoformat(), "until": today.isoformat(),
            "total": len(matches), "items": page, "limit": limit, "offset": offset,
            "has_next": offset + limit < len(matches),
            **{source: [item for item in page if item["source"] == source]
               for source in ("official", "generated", "imported")}}


def list_unified(q: str = "", source: str = "all", scope: str = "all",
                 user_id: str = "", group: str = "", rtype: str = "",
                 limit: int = 30, offset: int = 0, date_filter: rd.DateFilter | None = None) -> dict:
    """统一报告库：官方 + 用户生成，按日期倒序合并分页。

    source: all | official | generated | imported
    scope:  all | mine  （对 generated/imported 生效）
    """
    official: list[dict] = []
    generated: list[dict] = []
    imported: list[dict] = []

    if source in ("all", "official"):
        try:
            res = rl.list_reports(q=q, group=group, rtype=rtype,
                                  limit=max(1, len(rl.all_items())), offset=0)
            official = [dict(it, source="official", author="",
                             author_id="") for it in res["items"]]
        except Exception:  # noqa: BLE001
            official = []

    if source in ("all", "generated"):
        generated = list_generated(
            q=q, scope=scope if scope in ("mine", "others") else "all",
            user_id=user_id, group=group, limit=None)

    if source in ("all", "imported"):
        imported = ri.list_reports(q=q, scope=scope, user_id=user_id, group=group, rtype=rtype)

    # group/rtype 过滤（official 已在 list_reports 过滤 group/rtype）
    if rtype and generated:
        generated = [it for it in generated if it.get("type") == rtype]
    if group and generated:
        generated = [it for it in generated if it.get("group") == group]

    if date_filter:
        official = [it for it in official if date_filter.matches(it)]
        generated = [it for it in generated if date_filter.matches(it)]
        imported = [it for it in imported if date_filter.matches(it)]

    def sort_key(it: dict) -> str:
        return it.get("created_at") or it.get("date") or it.get("imported_at") or ""

    merged = official + generated + imported
    merged = [dict(item, date=rd.report_day(item).isoformat() if rd.report_day(item) else '') for item in merged]
    merged.sort(key=sort_key, reverse=True)
    page = merged[offset:offset + limit]

    # facets：合并两边的 group/type
    groups = sorted({it.get("group") for it in merged if it.get("group")})
    types = sorted({it.get("type") for it in merged if it.get("type")})

    return {
        "total": len(merged),
        "offset": offset,
        "limit": limit,
        "items": page,
        "facets": {"groups": groups, "types": types},
        "counts": {
            "official": len(official),
            "generated": len(generated),
            "imported": len(imported),
            "all": len(merged),
        },
        "index_count": len(rl.all_items() or []),
    }
