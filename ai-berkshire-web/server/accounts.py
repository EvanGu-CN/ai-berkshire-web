"""Account policy and immutable daily usage; no dependency on report deletion."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import secrets
import uuid

from . import auth
from .platform_policy import configuration

CHINA_TZ = timezone(timedelta(hours=8))
DEFAULTS = {"free_quota": 20, "vip_quota": 200, "svip_quota": 0,
            "invite_required": False}


def now() -> str:
    return datetime.now(configuration()["tzinfo"]).isoformat(timespec="seconds")


@contextmanager
def transaction():
    with auth._lock:
        conn = auth.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_usage (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL,
            day TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user_day ON research_usage(user_id, day, status);
        CREATE TABLE IF NOT EXISTS invite_codes (
            code TEXT PRIMARY KEY, creator_id TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL, used_by TEXT, used_at TEXT
        );
    """)


def migrate_usage():
    with transaction() as conn:
        # Idempotent migration; existing history is preserved. New usage is independent.
        migrated = conn.execute("SELECT 1 FROM account_settings WHERE key='usage_migrated'").fetchone()
        if not migrated:
            conn.execute("""INSERT OR IGNORE INTO research_usage
                SELECT 'legacy_' || id, user_id, 'research', substr(created_at,1,10),
                       'completed', created_at, created_at FROM history""")
            conn.execute("INSERT INTO account_settings VALUES ('usage_migrated', 'true')")


def settings() -> dict:
    with transaction() as conn:
        rows = conn.execute("SELECT key,value FROM account_settings").fetchall()
    defaults = {**DEFAULTS, **{key + "_quota": value["quota"]
                             for key, value in configuration()["membership_tiers"].items()}}
    return {**defaults, **{r["key"]: json.loads(r["value"]) for r in rows if r["key"] in defaults}}


def save_settings(values: dict) -> dict:
    allowed = set(DEFAULTS) | {key + "_quota" for key in configuration()["membership_tiers"]}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError("不支持的设置")
        if key == "invite_required":
            if type(value) is not bool:
                raise ValueError("邀请注册开关无效")
        elif type(value) is not int or not 0 <= value <= 10000:
            raise ValueError("每日额度需为 0–10000 的整数，0 表示不限")
    with transaction() as conn:
        conn.executemany("INSERT OR REPLACE INTO account_settings VALUES (?,?)",
                         [(k, json.dumps(v)) for k, v in values.items()])
    return settings()


def root_admin_id() -> str:
    with transaction() as conn:
        row = conn.execute("SELECT value FROM account_settings WHERE key='root_admin_id'").fetchone()
    return json.loads(row[0]) if row else ""


def establish_root(conn, user_id):
    conn.execute("INSERT OR IGNORE INTO account_settings VALUES ('root_admin_id',?)",
                 (json.dumps(user_id),))


def public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"],
            "is_admin": bool(row["is_admin"]), "tier": row["tier"] or "free",
            "is_root_admin": row["id"] == root_admin_id()}


def _quota(conn, user, policy):
    platform = configuration()
    tier = user["tier"] or platform["default_tier"]
    fallback = platform["membership_tiers"][platform["default_tier"]]
    quota = 0 if user["is_admin"] else policy.get(tier + "_quota", fallback["quota"])
    day = now()[:10]
    used = conn.execute("SELECT count(*) FROM research_usage WHERE user_id=? AND day=? "
                        "AND status IN ('running','completed')", (user["id"], day)).fetchone()[0]
    return {"ok": quota == 0 or used < quota, "used": used, "quota": quota,
            "remaining": None if quota == 0 else max(0, quota - used),
            "tier": tier, "label": platform["membership_tiers"].get(tier, fallback)["label"],
            "unit": "request", "timezone": platform["timezone"],
            "resets_at": datetime.combine(datetime.fromisoformat(now()).date() + timedelta(days=1),
                                           datetime.min.time(), platform["tzinfo"]).isoformat()}


def quota_info(user_id):
    policy = settings()
    with transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["is_disabled"]:
            raise ValueError("账号不可用")
        return _quota(conn, user, policy)


def reserve(user_id, action):
    policy = settings()
    with transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["is_disabled"]:
            raise ValueError("账号不可用")
        info = _quota(conn, user, policy)
        if not info["ok"]:
            raise ValueError(f"今日操作额度已用完（{info['quota']} 次），{info['timezone']} 次日零点恢复，可联系管理员调整等级")
        rid = uuid.uuid4().hex
        conn.execute("INSERT INTO research_usage VALUES (?,?,?,?,?,?,NULL)",
                     (rid, user_id, action, now()[:10], "running", now()))
        return rid


def finish(usage_id, success):
    with transaction() as conn:
        conn.execute("UPDATE research_usage SET status=?,finished_at=? WHERE id=? AND status='running'",
                     ("completed" if success else "failed", now(), usage_id))


def recover_interrupted():
    # One local server process owns this SQLite app; a restart releases unfinished work.
    with transaction() as conn:
        conn.execute("UPDATE research_usage SET status='failed',finished_at=? WHERE status='running'", (now(),))


def usage_list(user_id, limit=50):
    with transaction() as conn:
        return [dict(r) for r in conn.execute("SELECT action,day,status,created_at,finished_at "
            "FROM research_usage WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))]


def generate_invite(user_id):
    code = secrets.token_hex(5).upper()
    platform = configuration()
    created = datetime.now(platform["tzinfo"])
    expires = (created + timedelta(hours=platform["invite_expiry_hours"])).isoformat(timespec="seconds")
    with transaction() as conn:
        count = conn.execute("SELECT count(*) FROM invite_codes WHERE creator_id=? AND created_at>=?",
                             (user_id, now()[:10])).fetchone()[0]
        if count >= platform["invite_daily_limit"]:
            raise ValueError("今日生成邀请已达上限，请明天再试")
        conn.execute("INSERT INTO invite_codes VALUES (?,?,?,?,NULL,NULL)",
                     (code, user_id, now(), expires))
    return {"code": code, "expires_at": expires}


def list_invites(user_id):
    with transaction() as conn:
        rows = conn.execute("SELECT code,expires_at,used_at FROM invite_codes WHERE creator_id=? "
                            "ORDER BY created_at DESC LIMIT 50", (user_id,)).fetchall()
    return [{**dict(r), "used": bool(r["used_at"]), "expired": r["expires_at"] <= now()} for r in rows]


def consume_invite(conn, code, user_id):
    cur = conn.execute("UPDATE invite_codes SET used_by=?,used_at=? WHERE code=? AND used_at IS NULL "
                       "AND expires_at>?", (user_id, now(), (code or "").strip().upper(), now()))
    return cur.rowcount == 1
