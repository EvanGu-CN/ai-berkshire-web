# -*- coding: utf-8 -*-
"""用户认证：SQLite（WAL）存储用户/会话，PBKDF2 密码哈希（纯标准库）。

- app.db: users / sessions / user_settings / history
- 预置管理员：首次启动自动创建（用户名/密码可配）
- 旧数据迁移：单机版的 settings.json 与 data/history/*.json 归入管理员
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import DATA_DIR, HISTORY_DIR, SETTINGS_FILE
from .platform_policy import configuration

DB_PATH = DATA_DIR / "app.db"
_lock = threading.Lock()

# Bootstrap only; existing administrator identities are never replaced.
ADMIN_USERNAME = os.environ.get("VC_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("VC_ADMIN_PASS", "")

SESSION_DAYS = configuration()["session_days"]


# ---------------------------------------------------------------- 密码
def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256，10 万轮。返回 (salt_hex, hash_hex)。"""
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex(), h.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        _, h = _hash_password(password, bytes.fromhex(salt_hex))
        return secrets.compare_digest(h, hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 连接
def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _lock:
        conn = connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    tier TEXT DEFAULT 'free',
                    is_disabled INTEGER DEFAULT 0,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT,
                    expires_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    doc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    skill_id TEXT, skill_title TEXT, arguments TEXT,
                    content TEXT, provider TEXT, model TEXT,
                    created_at TEXT, chars INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_history_uid ON history(user_id, created_at DESC);
            """)
            # 旧库迁移：补 tier / is_disabled 列
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(users)").fetchall()]
            if "tier" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
            if "is_disabled" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN "
                    "is_disabled INTEGER DEFAULT 0")
            from . import accounts
            accounts.init_schema(conn)
            from . import report_imports
            report_imports.init_schema(conn)
            conn.commit()
        finally:
            conn.close()
    _seed_admin()
    _migrate_legacy()
    accounts.migrate_usage()


# ---------------------------------------------------------------- 管理员
def _seed_admin() -> None:
    from . import accounts
    with _lock:
        conn = connect()
        try:
            # Existing passwords, disabled states and roles must survive every restart.
            row = conn.execute(
                "SELECT id FROM users WHERE username=? AND is_admin=1", (ADMIN_USERNAME,)).fetchone()
            if not row:
                row = conn.execute("SELECT id FROM users WHERE is_admin=1 ORDER BY created_at LIMIT 1").fetchone()
            if row:
                accounts.establish_root(conn, row["id"])
                conn.commit()
                return
            # Never promote an unrelated existing username or create a public default password.
            if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
                return
            if len(ADMIN_PASSWORD) < 8:
                raise RuntimeError("首次启动请设置 VC_ADMIN_PASS（至少 8 位），已有账号不会被此设置覆盖")
            uid = "u_" + uuid.uuid4().hex[:10]
            salt_hex, hash_hex = _hash_password(ADMIN_PASSWORD)
            conn.execute(
                "INSERT INTO users (id,username,salt,password_hash,is_admin,tier,is_disabled,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uid, ADMIN_USERNAME, salt_hex, hash_hex, 1, configuration()["default_tier"], 0, accounts.now()),
            )
            accounts.establish_root(conn, uid)
            conn.commit()
        finally:
            conn.close()


def _get_admin_id() -> str:
    from .accounts import root_admin_id
    return root_admin_id()


# ---------------------------------------------------------------- 旧数据迁移
def _migrate_legacy() -> None:
    """单机版数据归入管理员：settings.json + data/history/*.json。只跑一次。"""
    flag = DATA_DIR / ".migrated"
    if flag.exists():
        return
    admin_id = _get_admin_id()
    if not admin_id:
        return
    with _lock:
        conn = connect()
        try:
            # 旧设置（多配置 JSON）→ 管理员的 user_settings
            if SETTINGS_FILE.is_file():
                try:
                    doc = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                    if isinstance(doc, dict) and "configs" in doc:
                        conn.execute(
                            "INSERT OR REPLACE INTO user_settings VALUES (?,?)",
                            (admin_id, json.dumps(doc, ensure_ascii=False)),
                        )
                except (json.JSONDecodeError, OSError):
                    pass
            # 旧历史 JSON 文件 → 管理员的 history
            if HISTORY_DIR.is_dir():
                for fp in sorted(HISTORY_DIR.glob("*.json")):
                    try:
                        rec = json.loads(fp.read_text(encoding="utf-8"))
                        conn.execute(
                            "INSERT OR IGNORE INTO history VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (rec.get("id") or fp.stem, admin_id,
                             rec.get("skill_id", ""), rec.get("skill_title", ""),
                             rec.get("arguments", ""), rec.get("content", ""),
                             rec.get("provider", ""), rec.get("model", ""),
                             rec.get("created_at", ""), rec.get("chars", 0)),
                        )
                    except (json.JSONDecodeError, OSError):
                        continue
            conn.commit()
        finally:
            conn.close()
    flag.write_text(datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                    encoding="utf-8")


# ---------------------------------------------------------------- 用户操作
def register(username: str, password: str, invite_code: str = "") -> dict | None:
    username = (username or "").strip()
    if not (3 <= len(username) <= 32):
        return {"error": "用户名长度需 3-32 个字符"}
    if len(password or "") < 8:
        return {"error": "密码至少 8 位"}
    if not all(c.isalnum() or c in "_-." for c in username):
        return {"error": "用户名只能包含字母、数字和 _-."}
    uid = "u_" + uuid.uuid4().hex[:10]
    salt_hex, hash_hex = _hash_password(password)
    from . import accounts
    policy = accounts.settings()
    with _lock:
        conn = connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
                if policy["invite_required"] or invite_code:
                    if not accounts.consume_invite(conn, invite_code, uid):
                        conn.rollback()
                        return {"error": "推荐码无效、已使用或已过期，请获取新的邀请链接"}
                conn.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                    (uid, username, salt_hex, hash_hex, 0,
                     configuration()["default_tier"], 0,
                     datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return {"error": "用户名已存在"}
        finally:
            conn.close()
    return {"ok": True, "user": {"id": uid, "username": username,
                                "is_admin": False}}


def login(username: str, password: str) -> dict | None:
    with _lock:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?",
                (username or "",)).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    if row["is_disabled"]:
        return {"error": "账号已被禁用，请联系管理员"}
    if not _verify_password(password or "",
                            row["salt"], row["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_DAYS * 86400
    with _lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)",
                (token, row["id"],
                 datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"), expires),
            )
            conn.commit()
        finally:
            conn.close()
    from .accounts import public_user
    return {"token": token, "user": public_user(row)}


def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _lock:
        conn = connect()
        try:
            sess = conn.execute(
                "SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
            if not sess or sess["expires_at"] < time.time():
                return None
            u = conn.execute(
                "SELECT id, username, is_admin, tier, is_disabled "
                "FROM users WHERE id=?",
                (sess["user_id"],)).fetchone()
            if not u or u["is_disabled"]:
                return None
            result = dict(u)
        finally:
            conn.close()
    from .accounts import public_user
    return public_user(result)



def logout(token: str) -> None:
    with _lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- 等级与配额
TIERS = configuration()["membership_tiers"]
VALID_TIERS = set(TIERS)


def membership_tiers() -> dict:
    from .accounts import settings
    policy = settings()
    return {key: {**tier, "quota": policy.get(key + "_quota", tier["quota"])}
            for key, tier in configuration()["membership_tiers"].items()}


def tier_info(tier: str) -> dict:
    tiers = membership_tiers()
    return tiers.get(tier, tiers[configuration()["default_tier"]])


def check_quota(user_id: str) -> dict:
    from .accounts import quota_info
    return quota_info(user_id)


# ---------------------------------------------------------------- 用户管理（管理员）
def list_users() -> list[dict]:
    tiers = membership_tiers()
    with _lock:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id, username, is_admin, tier, is_disabled, created_at "
                "FROM users ORDER BY is_admin DESC, created_at ASC").fetchall()
            return [{
                "id": r["id"], "username": r["username"],
                "is_admin": bool(r["is_admin"]),
                "tier": r["tier"] or "free",
                "is_disabled": bool(r["is_disabled"]),
                "created_at": r["created_at"],
                "quota": tiers.get(r["tier"] or "free", tiers[configuration()["default_tier"]]),
            } for r in rows]
        finally:
            conn.close()


def update_user(user_id: str, *, tier: str | None = None,
                is_admin: bool | None = None,
                is_disabled: bool | None = None) -> bool:
    sets, args = [], []
    if tier is not None:
        if tier not in configuration()["membership_tiers"]:
            return False
        sets.append("tier=?")
        args.append(tier)
    if is_admin is not None:
        sets.append("is_admin=?")
        args.append(1 if is_admin else 0)
    if is_disabled is not None:
        sets.append("is_disabled=?")
        args.append(1 if is_disabled else 0)
    if not sets:
        return False
    args.append(user_id)
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id=?", args)
            if is_disabled:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def reset_password(user_id: str, new_password: str) -> bool:
    if len(new_password or "") < 6:
        return False
    salt_hex, hash_hex = _hash_password(new_password)
    with _lock:
        conn = connect()
        try:
            cur = conn.execute(
                "UPDATE users SET salt=?, password_hash=? WHERE id=?",
                (salt_hex, hash_hex, user_id))
            # 重置密码后强制下线
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def delete_user(user_id: str) -> bool:
    with _lock:
        conn = connect()
        try:
            cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM history WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM imported_reports WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM research_usage WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM invite_codes WHERE creator_id=?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()



def change_password(user_id: str, old_password: str, new_password: str) -> bool:
    if len(new_password) < 8:
        return False
    with _lock:
        conn = connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or not _verify_password(old_password, row["salt"], row["password_hash"]):
                return False
            salt, hashed = _hash_password(new_password)
            conn.execute("UPDATE users SET salt=?, password_hash=? WHERE id=?", (salt, hashed, user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
            return True
        finally:
            conn.close()
