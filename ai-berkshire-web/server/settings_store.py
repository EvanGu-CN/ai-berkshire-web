# -*- coding: utf-8 -*-
"""模型配置存储（多用户版）：每用户一份多配置文档，存 SQLite app.db。

文档结构与单机版一致：
{ "active_id": "...", "configs": [ {...}, ... ] }
"""
from __future__ import annotations

import json
import secrets
import time
from copy import deepcopy

from . import auth

UNLIMITED = "不限"

DEFAULT_CONFIG: dict = {
    "id": "",
    "name": "演示配置",
    "provider": "demo",            # API 协议：openai | anthropic | demo
    "provider_preset": "",         # UI 服务商预设；空值兼容旧配置
    "base_url": "",
    "api_key": "",
    "model": "",
    "temperature": 0.7,
    "max_input_tokens": UNLIMITED,
    "max_output_tokens": UNLIMITED,
    "thinking": "高（默认）",
    "web_search_mode": "模型侧联网优先（失败自动回退自建搜索）",
    "enable_thinking": True,
    "enable_web_search": True,
    "headers": {},
    "updated_at": "",
}


def _new_id() -> str:
    return "cfg_" + str(int(time.time() * 1000))[-8:] + secrets.token_hex(2)


def _default_doc() -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["id"] = _new_id()
    cfg["provider_preset"] = "demo"
    return {"active_id": cfg["id"], "configs": [cfg]}


def _load_doc(user_id: str) -> dict:
    with auth._lock:
        conn = auth.connect()
        try:
            row = conn.execute(
                "SELECT doc FROM user_settings WHERE user_id=?",
                (user_id,)).fetchone()
            if not row:
                doc = _default_doc()
                conn.execute(
                    "INSERT OR REPLACE INTO user_settings VALUES (?,?)",
                    (user_id, json.dumps(doc, ensure_ascii=False)))
                conn.commit()
                return doc
            doc = json.loads(row["doc"])
            for c in doc.get("configs", []):
                merged = deepcopy(DEFAULT_CONFIG)
                merged.update({k: v for k, v in c.items() if k in merged})
                c.clear()
                c.update(merged)
            if not doc.get("configs"):
                doc = _default_doc()
            if doc.get("active_id") not in [c["id"] for c in doc["configs"]]:
                doc["active_id"] = doc["configs"][0]["id"]
            return doc
        finally:
            conn.close()


def _save_doc(user_id: str, doc: dict) -> None:
    with auth._lock:
        conn = auth.connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO user_settings VALUES (?,?)",
                (user_id, json.dumps(doc, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- 查询
def get_active_config(user_id: str) -> dict:
    doc = _load_doc(user_id)
    for c in doc["configs"]:
        if c["id"] == doc["active_id"]:
            return deepcopy(c)
    return deepcopy(doc["configs"][0])


def list_configs(user_id: str) -> dict:
    doc = _load_doc(user_id)
    return {"active_id": doc["active_id"],
            "configs": [masked_config(c) for c in doc["configs"]]}


# ---------------------------------------------------------------- 修改
def create_config(user_id: str, payload: dict | None = None) -> dict:
    doc = _load_doc(user_id)
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["id"] = _new_id()
    if payload:
        _apply(cfg, payload)
    cfg["updated_at"] = _now()
    doc["configs"].append(cfg)
    if len(doc["configs"]) == 1:
        doc["active_id"] = cfg["id"]
    _save_doc(user_id, doc)
    return cfg


def update_config(user_id: str, config_id: str, payload: dict) -> dict | None:
    doc = _load_doc(user_id)
    for c in doc["configs"]:
        if c["id"] == config_id:
            _apply(c, payload)
            c["updated_at"] = _now()
            _save_doc(user_id, doc)
            return deepcopy(c)
    return None


def delete_config(user_id: str, config_id: str) -> bool:
    doc = _load_doc(user_id)
    before = len(doc["configs"])
    doc["configs"] = [c for c in doc["configs"] if c["id"] != config_id]
    if len(doc["configs"]) == before:
        return False
    if not doc["configs"]:
        doc = _default_doc()
    elif doc.get("active_id") == config_id:
        doc["active_id"] = doc["configs"][0]["id"]
    _save_doc(user_id, doc)
    return True


def activate_config(user_id: str, config_id: str) -> bool:
    doc = _load_doc(user_id)
    if config_id not in [c["id"] for c in doc["configs"]]:
        return False
    doc["active_id"] = config_id
    _save_doc(user_id, doc)
    return True


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _apply(cfg: dict, payload: dict) -> None:
    for key in ("name", "provider", "provider_preset", "base_url", "model", "temperature",
                "max_input_tokens", "max_output_tokens", "thinking",
                "web_search_mode"):
        if key in payload and payload[key] is not None:
            cfg[key] = payload[key]
    for key in ("enable_thinking", "enable_web_search"):
        if key in payload and payload[key] is not None:
            cfg[key] = bool(payload[key])
    if "headers" in payload and isinstance(payload["headers"], dict):
        items = list(payload["headers"].items())[:8]
        cfg["headers"] = {str(k): str(v) for k, v in items if str(k).strip()}
    if payload.get("clear_key"):
        cfg["api_key"] = ""
    elif payload.get("api_key"):
        cfg["api_key"] = str(payload["api_key"])


# ---------------------------------------------------------------- 脱敏
def masked_config(cfg: dict) -> dict:
    c = deepcopy(cfg)
    key = c.get("api_key") or ""
    if len(key) > 12:
        c["api_key_masked"] = key[:4] + "****" + key[-4:]
    elif key:
        c["api_key_masked"] = "****"
    else:
        c["api_key_masked"] = ""
    c.pop("api_key", None)
    c["has_api_key"] = bool(key)
    return c
