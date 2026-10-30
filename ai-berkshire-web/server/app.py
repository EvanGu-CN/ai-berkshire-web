# -*- coding: utf-8 -*-
"""AI Berkshire Web — 多用户在线版：认证 + 数据隔离 + 研究工作台。"""
from __future__ import annotations

import json
import logging
import threading
from typing import Literal
import shutil
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictInt

from . import auth, accounts
from .platform_policy import public_policy
from . import financial_tools as ft
from . import history_store as hs
from . import memory_qa as mq
from . import memory_store as ms
from . import multi_agent as ma
from . import pipeline as pl
from . import reports_lib as rl
from . import report_imports as ri
from . import report_dates as rd
from . import shared_reports as sr
from . import skills_catalog as sc
from . import upstream_sync as usync
from .llm_client import stream_research, test_connection
from .paths import APP_ROOT
from .web_assets import render_index
from .streaming import KeepaliveStreamingResponse
from .settings_store import (
    activate_config, create_config, delete_config, get_active_config,
    list_configs, update_config,
)

app = FastAPI(title="AI Berkshire Web", version="2.1.0")

STATIC_DIR = APP_ROOT / "static"
INDEX_HTML, INDEX_ETAG = render_index(STATIC_DIR)

auth.init_db()   # 启动即建库 + 种管理员 + 迁移旧数据
accounts.recover_interrupted()
logger = logging.getLogger(__name__)

# Fresh installations start with zero reports. Local files are imported explicitly.


# ----------------------------------------------------------------- 鉴权
def get_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.query_params.get("token", "")


def get_current_user(request: Request) -> dict:
    user = auth.get_user_by_token(get_token(request))
    if not user:
        raise HTTPException(401, "未登录或会话已过期")
    return user


class AuthPayload(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    invite_code: str = Field(default="", max_length=64)


@app.post("/api/auth/login")
def api_auth_login(p: AuthPayload):
    result = auth.login(p.username.strip(), p.password)
    if not result:
        raise HTTPException(401, "用户名或密码错误")
    if "error" in result:
        raise HTTPException(403, result["error"])
    return result


@app.post("/api/auth/register")
def api_auth_register(p: AuthPayload):
    result = auth.register(p.username, p.password, p.invite_code)
    if "error" in result:
        raise HTTPException(400, result["error"])
    # 注册后直接登录
    token = auth.login(p.username.strip(), p.password)
    return token


@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    auth.logout(get_token(request))
    return {"ok": True}


@app.get("/api/auth/me")
def api_auth_me(user: dict = Depends(get_current_user)):
    quota = auth.check_quota(user["id"])
    return {**user, "quota": quota, "registration": accounts.settings()["invite_required"]}


# ----------------------------------------------------------------- 用户管理（仅管理员）
def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "需要管理员权限")
    return user


class UserUpdatePayload(BaseModel):
    tier: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    is_admin: StrictBool | None = None
    is_disabled: StrictBool | None = None


class ResetPasswordPayload(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


@app.get("/api/admin/users")
def api_admin_users(user: dict = Depends(require_admin)):
    return {"items": auth.list_users(), "tiers": auth.membership_tiers(),
            "can_manage_roles": user.get("is_root_admin", False),
            "root_admin_id": accounts.root_admin_id(), "policy": accounts.settings()}


@app.put("/api/admin/users/{user_id}")
def api_admin_update_user(user_id: str, p: UserUpdatePayload,
                          user: dict = Depends(require_admin)):
    protect_user(user_id, user, role_change=p.is_admin is not None)
    if user_id == accounts.root_admin_id() and (p.is_admin is False or p.is_disabled):
        raise HTTPException(400, "不能停用或取消初始管理员")
    if user_id == user["id"] and p.is_disabled:
        raise HTTPException(400, "不能禁用自己")
    if user_id == user["id"] and p.is_admin is False:
        raise HTTPException(400, "不能取消自己的管理员")
    if not auth.update_user(user_id, tier=p.tier,
                            is_admin=p.is_admin, is_disabled=p.is_disabled):
        raise HTTPException(400, "更新失败（等级值无效或用户不存在）")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reset-password")
def api_admin_reset_pwd(user_id: str, p: ResetPasswordPayload,
                        user: dict = Depends(require_admin)):
    protect_user(user_id, user)
    if not auth.reset_password(user_id, p.new_password):
        raise HTTPException(400, "重置失败（密码至少 6 位）")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def api_admin_delete_user(user_id: str,
                          user: dict = Depends(require_admin)):
    protect_user(user_id, user, role_change=True)
    if user_id == accounts.root_admin_id():
        raise HTTPException(400, "不能删除初始管理员")
    if user_id == user["id"]:
        raise HTTPException(400, "不能删除自己")
    if not auth.delete_user(user_id):
        raise HTTPException(404, "用户不存在")
    return {"ok": True}


def protect_user(user_id: str, actor: dict, *, role_change=False):
    if role_change and not actor.get("is_root_admin"):
        raise HTTPException(403, "仅初始管理员可以任命管理员或删除用户")
    target = next((u for u in auth.list_users() if u["id"] == user_id), None)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target["is_admin"] and not actor.get("is_root_admin"):
        raise HTTPException(403, "仅初始管理员可以管理其他管理员")


def require_root(user: dict = Depends(require_admin)):
    if not user.get("is_root_admin"):
        raise HTTPException(403, "仅初始管理员可以迁移全站数据")
    return user


class AccountPasswordPayload(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AccountPolicyPayload(BaseModel):
    free_quota: StrictInt | None = Field(default=None, ge=0, le=10000)
    vip_quota: StrictInt | None = Field(default=None, ge=0, le=10000)
    svip_quota: StrictInt | None = Field(default=None, ge=0, le=10000)
    invite_required: StrictBool | None = None
    quotas: dict[str, StrictInt] | None = None


@app.get("/api/auth/policy")
def api_auth_policy():
    return {"invite_required": accounts.settings()["invite_required"],
            "platform": public_policy()}


@app.put("/api/account/password")
def api_account_password(p: AccountPasswordPayload, user: dict = Depends(get_current_user)):
    if not auth.change_password(user["id"], p.old_password, p.new_password):
        raise HTTPException(400, "当前密码不正确，新密码需至少 8 位")
    return {"ok": True}


@app.get("/api/account/usage")
def api_account_usage(user: dict = Depends(get_current_user)):
    return {"quota": accounts.quota_info(user["id"]), "items": accounts.usage_list(user["id"])}


@app.post("/api/invite/generate")
def api_invite_generate(user: dict = Depends(get_current_user)):
    try:
        return {"invite": accounts.generate_invite(user["id"])}
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc


@app.get("/api/invite/list")
def api_invite_list(user: dict = Depends(get_current_user)):
    return {"codes": accounts.list_invites(user["id"])}


@app.get("/api/admin/account-policy")
def api_account_policy(user: dict = Depends(require_admin)):
    return accounts.settings()


@app.put("/api/admin/account-policy")
def api_account_policy_update(p: AccountPolicyPayload, user: dict = Depends(require_admin)):
    values = p.model_dump(exclude_none=True)
    quotas = values.pop("quotas", {})
    if set(quotas) - set(auth.membership_tiers()):
        raise HTTPException(422, "包含未配置的会员等级")
    values.update({key + "_quota": value for key, value in quotas.items()})
    try:
        return accounts.save_settings(values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def reserve_usage(user, action):
    try:
        return accounts.reserve(user["id"], action)
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc


def report_date_filter(period: str = "all", date_from: str = "", date_to: str = ""):
    try:
        return rd.DateFilter.build(period, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def event_stream(events, *, keepalive=False, finalize=None):
    response = KeepaliveStreamingResponse if keepalive else StreamingResponse
    options = {"finalize": finalize} if keepalive else {}
    return response(events, media_type="application/x-ndjson; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}, **options)


# ----------------------------------------------------------------- 上游同步（仅管理员）
@app.get("/api/admin/sync-status")
def api_sync_status(user: dict = Depends(require_admin)):
    return usync.get_status()


@app.post("/api/admin/sync-ai-berkshire")
def api_sync_start(user: dict = Depends(require_admin)):
    return usync.start_sync()


# ----------------------------------------------------------------- 数据迁移（仅管理员）
@app.get("/api/admin/data/export")
def api_data_export(user: dict = Depends(require_root)):
    """导出全站数据（用户+API Key+共享报告池）为 zip 供跨环境迁移。"""
    from . import data_migrate as dm
    import tempfile
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(tempfile.gettempdir()) / f"ai-berkshire-web-backup-{ts}.zip"
    r = dm.export_data(dest)
    if not r.get("ok"):
        raise HTTPException(400, r.get("message", "导出失败"))
    return FileResponse(
        dest, media_type="application/zip",
        filename=f"ai-berkshire-web-backup-{ts}.zip",
        background=None)


@app.post("/api/admin/data/import")
def api_data_import(file: UploadFile = File(...),
                    user: dict = Depends(require_root)):
    """导入备份 zip，恢复用户与共享报告池（自动备份旧库）。"""
    from . import data_migrate as dm
    import tempfile
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 备份文件")
    tmp = Path(tempfile.gettempdir()) / f"vc-import-{uuid.uuid4().hex[:8]}.zip"
    try:
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)
        r = dm.import_data(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if not r.get("ok"):
        raise HTTPException(400, r.get("message", "导入失败"))
    return r


@app.get("/api/admin/data/status")
def api_data_status(user: dict = Depends(require_root)):
    from . import data_migrate as dm
    return {"counts": dm._db_counts(), "db_exists": dm.APP_DB.is_file()}


# ------------------------------------------------------- 共享报告 git 同步
@app.post("/api/admin/sync-reports")
def api_sync_reports(action: str = "export",
                     user: dict = Depends(require_admin)):
    """Retired route retained so old clients cannot write private reports into Git."""
    raise HTTPException(410, "报告与源码已分离，请使用本地报告导入或管理员数据备份")


# ----------------------------------------------------------------- schemas
class ResearchRequest(BaseModel):
    skill_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    arguments: str = Field(default="", max_length=10000)
    mode: Literal["auto", "single", "team"] = "auto"   # auto | single | team
    market: Literal["all", "a", "hk", "us", "a_hk"] = "all"  # all | a | hk | us | a_hk
    params: dict = Field(default_factory=dict)    # 动态参数（top_n / final_count 等）


class SettingsPayload(BaseModel):
    name: str | None = None
    provider: str | None = None
    provider_preset: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_input_tokens: str | None = None
    max_output_tokens: str | None = None
    thinking: str | None = None
    web_search_mode: str | None = None
    enable_thinking: bool | None = None
    enable_web_search: bool | None = None
    headers: dict | None = None
    clear_key: bool | None = None


class MemoryAskPayload(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_n: int = Field(default=6, ge=1, le=10)
    period: str = "all"
    date_from: str = ""
    date_to: str = ""


class MemoryExtractPayload(BaseModel):
    path: str


class PipelinePayload(BaseModel):
    targets: str = Field(min_length=1, max_length=4000)
    stages: list[Literal["screen", "checklist", "deep"]] = Field(default_factory=lambda: ["screen", "checklist", "deep"], min_length=1, max_length=3)
    deep_count: int = Field(default=1, ge=1, le=5)


# ----------------------------------------------------------------- skills
@app.get("/api/skills")
def api_skills(user: dict = Depends(get_current_user)):
    items = sc.load_skills()
    by_cat: dict[str, list] = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    ordered = [
        {"category": cat, "items": by_cat[cat]}
        for cat in sc.CATEGORY_ORDER if cat in by_cat
    ]
    for cat, its in by_cat.items():
        if cat not in sc.CATEGORY_ORDER:
            ordered.append({"category": cat, "items": its})
    return {"total": len(items), "categories": ordered}


@app.get("/api/skills/{skill_id}")
def api_skill_detail(skill_id: str,
                     user: dict = Depends(get_current_user)):
    doc = sc.get_skill_content(skill_id)
    if not doc:
        raise HTTPException(404, "技能不存在")
    return doc


@app.get("/api/skills/{skill_id}/params")
def api_skill_params(skill_id: str,
                     user: dict = Depends(get_current_user)):
    from .skill_params import params_for_skill
    if not sc.get_skill_content(skill_id):
        raise HTTPException(404, "技能不存在")
    return {"skill_id": skill_id, "params": params_for_skill(skill_id)}


# ----------------------------------------------------------------- research
@app.post("/api/research/run")
def api_research_run(req: ResearchRequest,
                     user: dict = Depends(get_current_user)):
    uid = user["id"]
    prompt = sc.build_prompt(req.skill_id, req.arguments, market=req.market, params=req.params)
    if not prompt:
        raise HTTPException(404, "技能不存在")
    skill = sc.get_skill_content(req.skill_id)
    active = get_active_config(uid)
    usage_id = reserve_usage(user, "research")
    use_team = req.mode == "team" or (req.mode == "auto" and req.skill_id in ma.TEAM_SKILLS)

    def generate():
        chunks = []
        full = ""
        complete = False
        try:
            if use_team:
                with closing(ma.iter_team_research(skill["content"] if skill else "", req.arguments,
                        skill_id=req.skill_id, market=req.market, params=req.params, user_id=uid)) as team_events:
                    for ev in team_events:
                        if ev.get("type") == "error":
                            raise RuntimeError("team failed")
                        if ev.get("type") == "done":
                            full = ev.get("full_text", "")
                            complete = bool(full)
                        if ev.get("type") == "role_done":
                            chunks.append("## " + ev.get("label", "") + "\n\n" + ev.get("text", ""))
                        if ev.get("type") != "done":
                            yield json.dumps(ev, ensure_ascii=False) + "\n"
            else:
                for chunk in stream_research(prompt, uid):
                    chunks.append(chunk)
                    yield json.dumps({"type": "delta", "text": chunk}, ensure_ascii=False) + "\n"
                full = "".join(chunks).strip()
                complete = bool(full)
            if not complete:
                raise RuntimeError("empty output")
        except GeneratorExit:
            raise
        except Exception:
            logger.exception("Research failed: usage=%s", usage_id)
            yield json.dumps({"type": "error", "message": "研究未能完成，请检查模型配置后重试。已有内容会保留。"}, ensure_ascii=False) + "\n"
        finally:
            saved = full or ("\n\n".join(chunks) if use_team else "".join(chunks))
            try:
                if saved.strip():
                    hs.save_record(uid, skill_id=req.skill_id,
                        skill_title=(skill["title"] if skill else req.skill_id) + ("（多视角）" if use_team else "") + ("" if complete else "（未完成）"),
                        arguments=req.arguments, content=saved,
                        provider=active.get("provider", "demo"), model=active.get("model", ""))
            finally:
                accounts.finish(usage_id, bool(saved.strip()))
        if complete:
            yield json.dumps({"type": "done", "full_text": full}, ensure_ascii=False) + "\n"
    return event_stream(generate(), keepalive=True, finalize=lambda: accounts.finish(usage_id, False))


# ----------------------------------------------------------------- history
@app.get("/api/history")
def api_history(q: str = "", skill_id: str = "", limit: int = Query(100, ge=1, le=200),
                offset: int = Query(0, ge=0), dates: rd.DateFilter = Depends(report_date_filter),
                user: dict = Depends(get_current_user)):
    return hs.list_page(user["id"], q=q, skill_id=skill_id, limit=limit, offset=offset, date_filter=dates)


@app.get("/api/history/{record_id}")
def api_history_detail(record_id: str,
                       user: dict = Depends(get_current_user)):
    rec = hs.get_record(user["id"], record_id)
    if not rec:
        raise HTTPException(404, "记录不存在")
    return rec


@app.delete("/api/history/{record_id}")
def api_history_delete(record_id: str,
                       user: dict = Depends(get_current_user)):
    if not hs.delete_record(user["id"], record_id):
        raise HTTPException(404, "记录不存在")
    return {"ok": True}


# ----------------------------------------------------------------- reports（官方 + 共享生成）
class ImportReportMetadata(BaseModel):
    title: str = Field(default="", max_length=400)
    group: str = Field(default="", max_length=120)
    type: str = Field(default="", max_length=100)
    date: str = Field(default="", max_length=64)


class ImportReportInput(BaseModel):
    path: str = Field(min_length=1, max_length=600)
    content: str = Field(max_length=ri.MAX_FILE_BYTES)
    metadata: ImportReportMetadata = Field(default_factory=ImportReportMetadata)


class ImportReportPayload(BaseModel):
    items: list[ImportReportInput] = Field(min_length=1, max_length=64)


@app.post("/api/reports/import/preview")
def api_report_import_preview(payload: ImportReportPayload, user: dict = Depends(get_current_user)):
    try:
        return ri.process(user["id"], [item.model_dump() for item in payload.items])
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc


@app.post("/api/reports/import")
def api_report_import(payload: ImportReportPayload, user: dict = Depends(get_current_user)):
    try:
        return ri.process(user["id"], [item.model_dump() for item in payload.items], save=True)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc


@app.delete("/api/reports/imported/{record_id}")
def api_report_import_delete(record_id: str, user: dict = Depends(get_current_user)):
    if not ri.delete_report(user["id"], record_id):
        raise HTTPException(404, "报告不存在或不属于当前用户")
    return {"ok": True}


@app.get("/api/reports")
def api_reports(q: str = "", group: str = "", bucket: str = "",
                rtype: str = "", source: str = "all", scope: str = "all",
                limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
                dates: rd.DateFilter = Depends(report_date_filter),
                user: dict = Depends(get_current_user)):
    """统一报告库。

    source: all | official | generated | imported
    scope:  all | mine                   —— 全部人 / 只看我的（对 generated/imported 生效）
    """
    return sr.list_unified(q=q, source=source, scope=scope, user_id=user["id"],
                           group=group, rtype=rtype,
                           limit=limit, offset=offset, date_filter=dates)


@app.get("/api/reports/recent")
def api_reports_recent(keyword: str = Query("", max_length=200),
                       period: Literal["1w", "1m", "3m", "6m", "12m"] = "6m",
                       days: int | None = Query(None, ge=1, le=365),
                       limit: int = Query(3, ge=1, le=10), offset: int = Query(0, ge=0),
                       user: dict = Depends(get_current_user)):
    """已有研究参考；统一报告日期口径，旧 days 参数继续可用。"""
    return sr.recent_matches(keyword, days=days, period=period if days is None else None,
                             limit=limit, offset=offset, user_id=user["id"])


@app.get("/api/reports/content")
def api_report_content(path: str,
                       user: dict = Depends(get_current_user)):
    if path.startswith(ri.PREFIX):
        rec = ri.get_report(path[len(ri.PREFIX):])
        if not rec:
            raise HTTPException(404, "报告不存在")
        return rec
    # 用户生成的共享报告：gen:{id}
    if path.startswith(sr.GEN_PREFIX):
        rec = sr.get_generated(path[len(sr.GEN_PREFIX):])
        if not rec:
            raise HTTPException(404, "报告不存在")
        return rec
    doc = rl.get_report(path)
    if not doc:
        raise HTTPException(404, "报告不存在或路径非法")
    return dict(doc, source="official", author="", author_id="")


@app.get("/api/reports/status")
def api_reports_status(user: dict = Depends(get_current_user)):
    return rl.status()


@app.post("/api/reports/refresh")
def api_reports_refresh(user: dict = Depends(get_current_user)):
    st = rl.build_index(force=True)
    return {"ok": True, **st}


# ----------------------------------------------------------------- settings（按用户隔离）
@app.get("/api/settings")
def api_settings_get(user: dict = Depends(get_current_user)):
    return list_configs(user["id"])


@app.post("/api/settings")
def api_settings_create(payload: SettingsPayload,
                        user: dict = Depends(get_current_user)):
    cfg = create_config(user["id"], payload.model_dump())
    from .settings_store import masked_config
    return masked_config(cfg)


@app.put("/api/settings/{config_id}")
def api_settings_put(config_id: str, payload: SettingsPayload,
                     user: dict = Depends(get_current_user)):
    saved = update_config(user["id"], config_id, payload.model_dump())
    if not saved:
        raise HTTPException(404, "配置不存在")
    from .settings_store import masked_config
    return masked_config(saved)


@app.delete("/api/settings/{config_id}")
def api_settings_delete(config_id: str,
                        user: dict = Depends(get_current_user)):
    if not delete_config(user["id"], config_id):
        raise HTTPException(404, "配置不存在")
    return {"ok": True}


@app.post("/api/settings/{config_id}/activate")
def api_settings_activate(config_id: str,
                          user: dict = Depends(get_current_user)):
    if not activate_config(user["id"], config_id):
        raise HTTPException(404, "配置不存在")
    return {"ok": True, "active_id": config_id}


@app.post("/api/settings/test")
def api_settings_test(user: dict = Depends(get_current_user)):
    return test_connection(user["id"])


# ----------------------------------------------------------------- memory（全局共享索引）
@app.get("/api/memory/status")
def api_memory_status(user: dict = Depends(get_current_user)):
    return ms.index_status()


@app.post("/api/memory/reindex")
def api_memory_reindex(user: dict = Depends(get_current_user)):
    result = ms.reindex_all()
    return result


@app.post("/api/memory/ask")
def api_memory_ask(p: MemoryAskPayload,
                   user: dict = Depends(get_current_user)):
    if not p.question.strip():
        raise HTTPException(400, "问题不能为空")
    dates = report_date_filter(p.period, p.date_from, p.date_to)
    hits, gen = mq.ask(p.question, top_n=max(1, min(p.top_n, 10)), user_id=user["id"], date_filter=dates)
    usage_id = reserve_usage(user, "memory") if hits else None

    def wrapped():
        produced = False
        complete = False
        try:
            yield json.dumps({"type": "sources", "hits": hits}, ensure_ascii=False) + "\n"
            for chunk in gen:
                produced = produced or bool(chunk.strip())
                yield json.dumps({"type": "delta", "text": chunk}, ensure_ascii=False) + "\n"
            complete = True
        except GeneratorExit:
            raise
        except Exception:
            logger.exception("Memory question failed")
            yield json.dumps({"type": "error", "message": "回答未能完成，请稍后重试或检查模型配置"}, ensure_ascii=False) + "\n"
        finally:
            if usage_id:
                accounts.finish(usage_id, produced)
        if complete:
            yield json.dumps({"type": "done"}) + "\n"
    return event_stream(wrapped(), keepalive=True,
                        finalize=lambda: accounts.finish(usage_id, False) if usage_id else None)


@app.get("/api/memory/conclusions")
def api_memory_conclusions(q: str = "", grp: str = "", verdict: str = "",
                           limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                           dates: rd.DateFilter = Depends(report_date_filter),
                           user: dict = Depends(get_current_user)):
    return ms.list_conclusions(q=q, grp=grp, verdict=verdict,
                               limit=limit, offset=offset, date_filter=dates)


@app.post("/api/memory/extract")
def api_memory_extract(p: MemoryExtractPayload,
                       user: dict = Depends(get_current_user)):
    doc = rl.get_report(p.path)
    if not doc:
        raise HTTPException(404, "报告不存在")
    usage_id = reserve_usage(user, "extract")
    success = False
    try:
        data = mq.extract_conclusion_llm(doc["title"], doc["content"], user_id=user["id"])
        if not data:
            raise HTTPException(502, "抽取未完成，请检查模型配置后重试")
        data["grp"] = doc.get("group", "")
        ms.upsert_conclusion_llm(p.path, data)
        success = True
        return {"ok": True, "conclusion": data}
    finally:
        accounts.finish(usage_id, success)


# ----------------------------------------------------------------- timeline（全局共享）
@app.get("/api/timeline/companies")
def api_timeline_companies(q: str = Query("", max_length=120), limit: int = Query(30, ge=1, le=50),
                           offset: int = Query(0, ge=0), dates: rd.DateFilter = Depends(report_date_filter),
                           user: dict = Depends(get_current_user)):
    items = [item for item in ms.list_companies(date_filter=dates) if q.strip().casefold() in item['grp'].casefold()]
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset,
            "limit": limit, "has_next": offset + limit < len(items)}


@app.get("/api/timeline/company")
def api_timeline_company(grp: str, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                         dates: rd.DateFilter = Depends(report_date_filter),
                         user: dict = Depends(get_current_user)):
    if not grp:
        raise HTTPException(400, "缺少 grp 参数")
    data = ms.get_timeline(grp, limit=limit, offset=offset, date_filter=dates)
    if not data["total"]:
        if dates.period == 'all':
            raise HTTPException(404, "该公司暂无研究记录")
    return data


# ----------------------------------------------------------------- pipeline
@app.post("/api/pipeline/run")
def api_pipeline_run(p: PipelinePayload,
                     user: dict = Depends(get_current_user)):
    import queue
    import re
    targets = [t.strip() for t in re.split(r"[,，、\n]+", p.targets) if t.strip()]
    if not targets or len(targets) > 30:
        raise HTTPException(400, "请输入 1–30 个候选标的")
    usage_id = reserve_usage(user, "pipeline")
    cancelled = threading.Event()
    q = queue.Queue()

    def worker():
        produced = False
        try:
            for ev in pl.run_pipeline(p.targets, p.stages, p.deep_count,
                                       user_id=user["id"], cancelled=cancelled):
                produced = produced or ev.get("type") in ("stage_item", "deep_delta")
                if cancelled.is_set():
                    break
                q.put(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.exception("Pipeline failed: usage=%s", usage_id)
            message = (str(exc) if isinstance(exc, pl.StageResultError) else
                       "流水线未能完成，已完成阶段保留在研究历史中")
            q.put(json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n")
        finally:
            accounts.finish(usage_id, produced)
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def generate():
        try:
            while True:
                try:
                    item = q.get(timeout=1)
                except queue.Empty:
                    yield "\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            cancelled.set()
    return event_stream(generate())


# ----------------------------------------------------------------- static
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "brand" / "favicon.ico", media_type="image/vnd.microsoft.icon")


@app.get("/")
def index(request: Request):
    headers = {"Cache-Control": "no-cache, must-revalidate", "ETag": INDEX_ETAG}
    if request.headers.get('if-none-match') == INDEX_ETAG:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(INDEX_HTML, headers=headers)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
