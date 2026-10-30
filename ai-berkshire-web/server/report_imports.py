"""用户选择的本地 Markdown 报告：预览、去重、独立持久化与共享阅读。"""
from __future__ import annotations

from datetime import date
import hashlib
from pathlib import PurePosixPath
import re
import uuid

from . import auth, accounts

PREFIX = "import:"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024

_TABLE_COLUMNS = """(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    report_type TEXT NOT NULL,
    report_date TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL
)"""


def init_schema(conn) -> None:
    # SQLite cannot drop the old inline UNIQUE(user_id, content_hash).
    # Copy every column unchanged in one savepoint before replacing that constraint.
    conn.execute("SAVEPOINT report_import_file_identity")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS imported_reports " + _TABLE_COLUMNS)
        legacy_unique = any(
            index[2] and [col[2] for col in conn.execute(
                "SELECT * FROM pragma_index_info(?)", (index[1],))] == ["user_id", "content_hash"]
            for index in conn.execute("PRAGMA index_list(imported_reports)").fetchall())
        if legacy_unique:
            conn.execute("CREATE TABLE imported_reports_file_identity " + _TABLE_COLUMNS)
            conn.execute("INSERT INTO imported_reports_file_identity SELECT * FROM imported_reports")
            conn.execute("DROP TABLE imported_reports")
            conn.execute("ALTER TABLE imported_reports_file_identity RENAME TO imported_reports")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_reports_owner ON imported_reports(user_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_reports_file_version "
                     "ON imported_reports(user_id,source_path,content_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_reports_content "
                     "ON imported_reports(user_id,content_hash)")
        # Existing IDs/rows stay intact, including any copies saved by an older
        # version. Guard new inserts without requiring a destructive cleanup.
        conn.execute("""CREATE TRIGGER IF NOT EXISTS imported_reports_deduplicate_content
            BEFORE INSERT ON imported_reports
            WHEN EXISTS (SELECT 1 FROM imported_reports
                         WHERE user_id=NEW.user_id AND content_hash=NEW.content_hash)
            BEGIN SELECT RAISE(IGNORE); END""")
        conn.execute("RELEASE SAVEPOINT report_import_file_identity")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT report_import_file_identity")
        conn.execute("RELEASE SAVEPOINT report_import_file_identity")
        raise


def _report_date(value: str) -> str:
    match = re.search(r"(20\d{2})[-/]?(0[1-9]|1[0-2])[-/]?([0-2]\d|3[01])", value)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    return ""


def _prepare(item: dict) -> dict:
    raw_path = item["path"].replace("\\", "/")
    result = {"path": raw_path, "status": "error", "reason": ""}
    path = PurePosixPath(raw_path)
    if (raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path)
            or any(part in ("", ".", "..") for part in raw_path.split("/"))
            or any(ord(c) < 32 for c in raw_path)):
        return dict(result, reason="请选择文件夹或文件上传，路径必须是目录内的相对路径")
    if any(part.startswith(".") for part in path.parts) or path.name.lower() in ("readme.md", "readme.markdown"):
        return dict(result, status="skipped", reason="目录说明或隐藏文件")
    if path.suffix.lower() not in (".md", ".markdown"):
        return dict(result, status="skipped", reason="仅导入 Markdown 报告")
    content = item["content"].lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return dict(result, reason="报告内容为空")
    if "\x00" in content:
        return dict(result, reason="文件不是有效的文本报告")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        return dict(result, reason="单份报告不能超过 4 MiB")

    # 读取简单 front matter；作者和 ID 始终取自当前账号及服务器。
    front = {}
    body = content
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", content, re.S)
    if match:
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                front[key.strip()] = value.strip().strip("\"'")
        body = content[match.end():]
    meta = {**front, **{k: v for k, v in (item.get("metadata") or {}).items() if v}}
    heading = re.search(r"^#\s+(.+?)\s*#*\s*$", body, re.M)
    title = meta.get("title") or (heading.group(1) if heading else path.stem)
    parents = list(path.parts[:-1])
    if parents and parents[0].lower() == "reports":
        parents.pop(0)
    if parents and parents[0] in ("公司", "专题"):
        parents.pop(0)
    company = meta.get("group") or meta.get("company") or (parents[0] if parents else "未分类")
    report_type = meta.get("type") or meta.get("skill_id")
    if not report_type:
        from .skills_catalog import SKILL_META
        report_type = next((name for name in sorted(SKILL_META, key=len, reverse=True)
                            if name in path.stem), "研究")
        if report_type == "研究" and "checklist" in path.stem.lower():
            report_type = "investment-checklist"
    report_date = _report_date(meta.get("date") or meta.get("created_at") or "") or _report_date(str(path))
    return dict(result, status="ready", title=title[:400], group=company[:120],
                type=report_type[:100], date=report_date, content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest())


def process(user_id: str, items: list[dict], *, save: bool = False) -> dict:
    """预览与保存使用同一套检查；每批事务只追加新报告，不修改既有记录。"""
    if sum(len(item["content"].encode("utf-8")) for item in items) > MAX_BATCH_BYTES:
        raise ValueError("单批内容不能超过 8 MiB，请分批导入")
    rows = [_prepare(item) for item in items]
    with auth._lock:
        conn = auth.connect()
        try:
            seen = {r[0] for r in conn.execute(
                "SELECT content_hash FROM imported_reports WHERE user_id=?", (user_id,))}
            for row in rows:
                if row["status"] != "ready":
                    continue
                digest = row["content_hash"]
                if digest in seen:
                    row.update(status="duplicate", reason="本人已导入相同正文，或本批包含相同文件（不受路径影响）")
                    continue
                seen.add(digest)
                if save:
                    rid = "imp_" + uuid.uuid4().hex
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO imported_reports VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rid, user_id, row["path"], row["title"], row["group"], row["type"],
                         row["date"], row["content"], digest, accounts.now()))
                    if cursor.rowcount:
                        row.update(status="imported", id=rid, report_path=PREFIX + rid)
                    else:
                        row.update(status="duplicate", reason="本人已导入相同正文（不受路径影响）")
            if save:
                conn.commit()
        finally:
            conn.close()
    public = [{k: v for k, v in row.items() if k != "content"} for row in rows]
    return {"ok": True, "items": public,
            "counts": {status: sum(row["status"] == status for row in rows)
                       for status in ("ready", "imported", "duplicate", "skipped", "error")}}


def _as_item(row) -> dict:
    return {"id": row["id"], "path": PREFIX + row["id"], "source": "imported",
            "title": row["title"], "group": row["company"], "type": row["report_type"],
            "date": row["report_date"], "created_at": row["report_date"],
            "imported_at": row["imported_at"], "source_path": row["source_path"],
            "author": row["username"] or "", "author_id": row["user_id"], "bucket": "本地导入"}


def list_reports(q: str = "", scope: str = "all", user_id: str = "",
                 group: str = "", rtype: str = "") -> list[dict]:
    sql = ("SELECT i.id,i.user_id,i.source_path,i.title,i.company,i.report_type,i.report_date,"
           "i.imported_at,u.username FROM imported_reports i LEFT JOIN users u ON u.id=i.user_id WHERE 1=1")
    args = []
    if scope == "mine":
        sql += " AND i.user_id=?"
        args.append(user_id)
    if group:
        sql += " AND i.company=?"
        args.append(group)
    if rtype:
        sql += " AND i.report_type=?"
        args.append(rtype)
    if q.strip():
        sql += " AND (i.title LIKE ? OR i.company LIKE ? OR i.source_path LIKE ? OR i.content LIKE ?)"
        args.extend([f"%{q.strip()}%"] * 4)
    sql += " ORDER BY i.report_date DESC,i.imported_at DESC,i.id"
    with auth._lock:
        conn = auth.connect()
        try:
            return [_as_item(row) for row in conn.execute(sql, args)]
        finally:
            conn.close()


def get_report(record_id: str) -> dict | None:
    with auth._lock:
        conn = auth.connect()
        try:
            row = conn.execute("SELECT i.*,u.username FROM imported_reports i "
                               "LEFT JOIN users u ON u.id=i.user_id WHERE i.id=?", (record_id,)).fetchone()
            if row:
                return dict(_as_item(row), content=row["content"], size=len(row["content"]))
            return None
        finally:
            conn.close()


def delete_report(user_id: str, record_id: str) -> bool:
    with auth._lock:
        conn = auth.connect()
        try:
            cursor = conn.execute("DELETE FROM imported_reports WHERE id=? AND user_id=?", (record_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
