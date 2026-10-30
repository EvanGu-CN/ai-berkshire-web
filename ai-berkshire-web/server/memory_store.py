# -*- coding: utf-8 -*-
"""研究记忆存储：SQLite 保存报告全文、倒排索引、结构化结论。

- docs       报告元数据 + 正文
- terms      倒排索引（中文按二字词，ASCII 按单词）
- conclusions 结构化结论（规则抽取 / LLM 深度抽取）
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR, REPORTS_DIR
from . import reports_lib
from . import shared_reports
from . import report_dates as rd

DB_PATH = DATA_DIR / "memory.db"
_lock = threading.Lock()

# ---------------------------------------------------------------- 分词
_ASCII_WORD = re.compile(r"[A-Za-z0-9_.\-]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """轻量分词：CJK 二字词 + ASCII 单词（小写）。"""
    tokens: list[str] = []
    for m in _ASCII_WORD.finditer(text):
        tokens.append(m.group().lower())
    cjk_chars = _CJK.findall(text)
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])
    return tokens


# ---------------------------------------------------------------- 连接
def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS docs (
            path TEXT PRIMARY KEY,
            title TEXT, grp TEXT, date TEXT, dtype TEXT,
            content TEXT, size INTEGER, indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS terms (
            term TEXT, path TEXT, tf INTEGER,
            PRIMARY KEY (term, path)
        );
        CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);
        CREATE TABLE IF NOT EXISTS conclusions (
            path TEXT PRIMARY KEY,
            title TEXT, grp TEXT, date TEXT,
            verdict TEXT, score REAL, score_max REAL,
            price_range TEXT, summary TEXT, extra TEXT,
            source TEXT,   -- rule | llm
            updated_at TEXT
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------- 索引
def index_status() -> dict:
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            n_docs = conn.execute("SELECT COUNT(*) c FROM docs").fetchone()["c"]
            n_terms = conn.execute("SELECT COUNT(*) c FROM terms").fetchone()["c"]
            n_concl = conn.execute("SELECT COUNT(*) c FROM conclusions").fetchone()["c"]
            updated = conn.execute(
                "SELECT MAX(indexed_at) m FROM docs").fetchone()["m"]
            return {
                "docs": n_docs, "terms": n_terms, "conclusions": n_concl,
                "last_indexed": updated or "",
            }
        finally:
            conn.close()


def reindex_all(progress_cb=None) -> dict:
    """全量重建索引（扫描合并索引中的全部报告）。"""
    all_items = reports_lib.all_items()
    total = len(all_items)
    done = 0
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            conn.execute("DELETE FROM terms")
            conn.execute("DELETE FROM docs")
            for it in all_items:
                path = it.get("path") or ""
                doc = reports_lib.get_report(path)
                if not doc:
                    continue
                content = doc["content"]
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?)",
                    (path, doc["title"], it.get("group", ""), it.get("date", ""),
                     it.get("type", ""), content, len(content), now),
                )
                # 倒排
                tf: dict[str, int] = {}
                for t in tokenize(f"{doc['title']} {it.get('group','')} {content}"):
                    tf[t] = tf.get(t, 0) + 1
                conn.executemany(
                    "INSERT OR REPLACE INTO terms VALUES (?,?,?)",
                    [(t, path, c) for t, c in tf.items()],
                )
                # 规则抽取结论
                concl = extract_conclusion_rule(doc["title"], content)
                if concl:
                    conn.execute(
                        "INSERT OR REPLACE INTO conclusions VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (path, doc["title"], it.get("group", ""), it.get("date", ""),
                         concl.get("verdict", ""), concl.get("score"),
                         concl.get("score_max"), concl.get("price_range", ""),
                         concl.get("summary", ""),
                         json.dumps(concl.get("extra", {}), ensure_ascii=False),
                         "rule", now),
                    )
                done += 1
                if progress_cb and done % 50 == 0:
                    progress_cb(done, total)
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "indexed": done, "total": total}


# ---------------------------------------------------------------- 检索
def search(query: str, top_n: int = 6, date_filter=None) -> list[dict]:
    """倒排检索：BM25 风格长度归一 + 标题加权，抗大文件刷榜。"""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    report_days = {item['path']: rd.report_day(item) for item in shared_reports.all_items()} if date_filter and date_filter.period != 'all' else {}
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            rd.install_sql(conn, report_days)
            date_clause, date_args = (date_filter or rd.DateFilter()).sql('d.date', 'd.path')
            n_docs = conn.execute("SELECT COUNT(*) c FROM docs").fetchone()["c"] or 1
            avg_len = conn.execute(
                "SELECT AVG(size) a FROM docs").fetchone()["a"] or 1000
            scores: dict[str, float] = {}
            marks: dict[str, list[str]] = {}
            for t in set(q_tokens):
                rows = conn.execute(
                    "SELECT d.path, tf, d.size, d.title FROM terms t2 "
                    "JOIN docs d ON d.path=t2.path WHERE t2.term=?" + date_clause,
                    [t] + date_args,
                ).fetchall()
                if not rows:
                    continue
                df = len(rows)
                idf = 1.0 + (n_docs / (1 + df)) ** 0.5
                for r in rows:
                    # BM25 式饱和 + 长度归一：防止大文件靠词频刷分
                    tf, size = r["tf"], r["size"] or 1
                    tf_norm = (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * size / avg_len))
                    title_bonus = 2.0 if t in (r["title"] or "").lower() or t in r["title"] else 0.0
                    scores[r["path"]] = scores.get(r["path"], 0.0) + (tf_norm + title_bonus) * idf
                    marks.setdefault(r["path"], []).append(t)
            if not scores:
                return []
            ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_n]
            results = []
            for path, sc in ranked:
                row = conn.execute(
                    "SELECT title, grp, date, content FROM docs WHERE path=?",
                    (path,),
                ).fetchone()
                if not row:
                    continue
                results.append({
                    "path": path,
                    "title": row["title"],
                    "grp": row["grp"],
                    "date": rd.date_text(row['date'], path, report_days),
                    "score": round(sc, 2),
                    "snippet": _snippet(row["content"], marks.get(path, []), query),
                })
            return results
        finally:
            conn.close()


def _snippet(content: str, tokens: list[str], query: str, width: int = 260) -> str:
    """在正文中定位最相关一段作为摘要。"""
    text = re.sub(r"\s+", " ", content)
    # 优先按查询里的中文词定位
    words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_.\-]{2,}", query)
    pos = -1
    for w in words:
        idx = text.find(w)
        if idx >= 0:
            pos = idx
            break
    if pos < 0 and tokens:
        idx = text.lower().find(tokens[0])
        pos = max(idx, 0)
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 3)
    return text[start:start + width] + ("…" if start + width < len(text) else "")


# ---------------------------------------------------------------- 结论规则抽取
_SCORE_PATTERNS = [
    re.compile(r"综合(?:评分|得分|分数)?[^\d-]{0,6}(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)"),
    re.compile(r"评分[：:]\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)"),
    re.compile(r"[（(](\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*分?[)）]"),
]
_VERDICT_WORDS = ["通过镜子测试", "✅ 通过", "✅通过", "有条件通过", "灰色地带",
                  "❌ 不通过", "❌不通过", "建议观望", "建议回避", "建议买入", "不通过", "通过"]
_PRICE_PATTERNS = [
    re.compile(r"[¥$＄]\s?[\d,.]{1,24}\s?[-–~—]\s?[¥$＄]?\s?[\d,.]{1,24}(?![\d,.])"),
    re.compile(r"(?<![\d,.])[\d,.]{1,24}\s?[-–~—]\s?[\d,.]{1,24}\s?(?:港元|美元|元|人民币|港币|HKD|USD|CNY)"),
    re.compile(r"(?:建仓|买入|目标)区间[^\d]{0,6}[\d,.]{1,24}\s?[-–~—]\s?[\d,.]{1,24}(?![\d,.])"),
]
_SUMMARY_MARKERS = ["一句话结论", "综合决策备忘录", "结论与建议", "最终结论", "投资结论", "结论"]


def extract_conclusion_rule(title: str, content: str) -> dict | None:
    """从报告正文规则抽取结论（不依赖 LLM，覆盖约六七成报告）。"""
    result: dict = {"verdict": "", "score": None, "score_max": None,
                    "price_range": "", "summary": "", "extra": {}}
    text = content or ""
    if not text:
        return None

    # 评分
    for pat in _SCORE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                score, maximum = float(m.group(1)), float(m.group(2))
                if not (math.isfinite(score) and math.isfinite(maximum) and 0 <= score <= maximum and maximum > 0):
                    continue
                result["score"], result["score_max"] = score, maximum
                break
            except (ValueError, IndexError):
                pass

    # 结论词（取文档后 1/3 优先，更接近最终结论）
    tail = text[len(text) * 2 // 3:]
    head = text
    for w in _VERDICT_WORDS:
        if w in tail or w in head:
            result["verdict"] = w.replace("✅", "").replace("❌", "").strip()
            break

    # 价格区间
    for pat in _PRICE_PATTERNS:
        m = pat.search(text)
        if m:
            result["price_range"] = m.group().strip()
            break

    # 摘要：找结论标记后的首段
    for marker in _SUMMARY_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            after = text[idx + len(marker): idx + len(marker) + 500]
            after = re.sub(r"^[#*\s:：>—-]+", "", after).strip()
            para = re.split(r"\n\s*\n|\n#{1,3}\s", after)[0]
            para = re.sub(r"\s+", " ", para).strip()
            if len(para) > 10:
                result["summary"] = para[:220]
                break
    if not result["summary"]:
        # 退化：取标题下第一段引用/正文
        m = re.search(r"^>?\s*(.{20,220})", text[:1200], re.M)
        if m:
            result["summary"] = re.sub(r"[*#>]", "", m.group(1)).strip()

    if result["score"] is not None or any(result[k] for k in ("verdict", "summary", "price_range")):
        return result
    return None


# ---------------------------------------------------------------- 结论查询
def list_conclusions(q: str = "", grp: str = "", verdict: str = "",
                     limit: int = 50, offset: int = 0, date_filter=None) -> dict:
    report_days = {item['path']: rd.report_day(item) for item in shared_reports.all_items()} if date_filter and date_filter.period != 'all' else {}
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            rd.install_sql(conn, report_days)
            sql = "SELECT * FROM conclusions WHERE 1=1"
            args: list = []
            if grp:
                sql += " AND grp=?"
                args.append(grp)
            if verdict:
                sql += " AND verdict LIKE ?"
                args.append(f"%{verdict}%")
            if q:
                sql += " AND (title LIKE ? OR summary LIKE ? OR grp LIKE ?)"
                args += [f"%{q}%"] * 3
            date_clause, date_args = (date_filter or rd.DateFilter()).sql('date', 'path')
            sql += date_clause
            args += date_args
            total = conn.execute(
                f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
            rows = conn.execute(
                sql + " ORDER BY date DESC, title LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
            groups = [r[0] for r in conn.execute(
                "SELECT DISTINCT grp FROM conclusions WHERE grp!='' "
                "ORDER BY grp LIMIT 100"
            ).fetchall()]
            return {
                "total": total,
                "items": [dict(r, date=rd.date_text(r['date'], r['path'], report_days)) for r in rows],
                "groups": groups,
            }
        finally:
            conn.close()


def get_conclusion(path: str) -> dict | None:
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            row = conn.execute(
                "SELECT * FROM conclusions WHERE path=?", (path,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def upsert_conclusion_llm(path: str, data: dict) -> bool:
    """LLM 深度抽取结果覆盖写入（source=llm）。"""
    with _lock:
        conn = _connect()
        try:
            init_db(conn)
            row = conn.execute(
                "SELECT title, grp, date FROM docs WHERE path=?", (path,)
            ).fetchone()
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT OR REPLACE INTO conclusions VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                (path,
                 data.get("title") or (row["title"] if row else path),
                 data.get("grp") or (row["grp"] if row else ""),
                 data.get("date") or (row["date"] if row else ""),
                 data.get("verdict", ""),
                 data.get("score"),
                 data.get("score_max"),
                 data.get("price_range", ""),
                 data.get("summary", ""),
                 json.dumps(data.get("extra", {}), ensure_ascii=False),
                 "llm", now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


# ---------------------------------------------------------------- 时间线
def list_companies(date_filter=None) -> list[dict]:
    """Read the lightweight catalog without waiting for full memory indexing."""
    from .timeline_service import list_companies as list_catalog_companies
    return list_catalog_companies(date_filter=date_filter)


def get_timeline(grp: str, limit: int = 20, offset: int = 0, date_filter=None) -> dict:
    """Read only one bounded page, reusing derived conclusions when unchanged."""
    from .timeline_service import get_timeline as get_page
    return get_page(grp, limit=limit, offset=offset, date_filter=date_filter)
