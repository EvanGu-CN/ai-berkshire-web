# -*- coding: utf-8 -*-
"""研究历史（多用户版）：SQLite app.db，按用户隔离。"""
from __future__ import annotations

import uuid
from datetime import datetime

from . import auth
from . import report_dates as rd


def _now() -> str:
    from .accounts import now
    return now()


def save_record(user_id: str, *, skill_id: str, skill_title: str,
                arguments: str, content: str, provider: str, model: str,
                record_id: str | None = None) -> dict:
    rid = record_id or uuid.uuid4().hex[:12]
    with auth._lock:
        conn = auth.connect()
        try:
            # 同 id 覆盖（流水线增量落盘），否则插入
            conn.execute(
                "DELETE FROM history WHERE id=? AND user_id=?", (rid, user_id))
            conn.execute(
                "INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, user_id, skill_id, skill_title, arguments,
                 content, provider, model, _now(), len(content)),
            )
            conn.commit()
        finally:
            conn.close()
    return {"id": rid, "skill_title": skill_title}


def list_records(user_id: str, q: str = "", skill_id: str = "",
                 limit: int = 100, offset: int = 0, date_filter=None) -> list[dict]:
    return list_page(user_id, q, skill_id, limit, offset, date_filter)['items']


def list_page(user_id: str, q: str = "", skill_id: str = "",
              limit: int = 100, offset: int = 0, date_filter=None) -> dict:
    sql = "SELECT * FROM history WHERE user_id=?"
    args: list = [user_id]
    if skill_id:
        sql += " AND skill_id=?"
        args.append(skill_id)
    if q:
        sql += " AND (skill_title LIKE ? OR arguments LIKE ? OR content LIKE ?)"
        args += [f"%{q}%"] * 3
    clause, date_args = (date_filter or rd.DateFilter()).sql('created_at')
    sql += clause
    args += date_args
    with auth._lock:
        conn = auth.connect()
        try:
            rd.install_sql(conn)
            total = conn.execute('SELECT COUNT(*) FROM (' + sql + ')', args).fetchone()[0]
            rows = conn.execute(sql + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "skill_id": r["skill_id"],
                    "skill_title": r["skill_title"],
                    "arguments": r["arguments"],
                    "provider": r["provider"],
                    "model": r["model"],
                    "created_at": r["created_at"],
                    "date": rd.date_text(r["created_at"]),
                    "chars": r["chars"],
                    "preview": (r["content"] or "")[:160].replace("\n", " "),
                })
            return {'items': items, 'total': total, 'offset': offset, 'limit': limit}
        finally:
            conn.close()


def get_record(user_id: str, record_id: str) -> dict | None:
    with auth._lock:
        conn = auth.connect()
        try:
            r = conn.execute(
                "SELECT * FROM history WHERE id=? AND user_id=?",
                (record_id, user_id)).fetchone()
            if not r:
                return None
            return dict(r)
        finally:
            conn.close()


def delete_record(user_id: str, record_id: str) -> bool:
    with auth._lock:
        conn = auth.connect()
        try:
            cur = conn.execute(
                "DELETE FROM history WHERE id=? AND user_id=?",
                (record_id, user_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
