# -*- coding: utf-8 -*-
"""LLM 调用客户端：支持 OpenAI 兼容接口、Anthropic 接口、演示模式（无 Key 可体验）。

- 读取 settings_store 当前启用配置（多配置卡片）
- 支持自定义请求头（部分供应商强制要求）
- 最大输入/输出 Token 为「不限」时不传/不截断
"""
from __future__ import annotations

import json
import time
from typing import Iterator

import requests

from .settings_store import get_active_config, UNLIMITED


class LLMError(Exception):
    pass


# ---------------------------------------------------------------- 演示模式
DEMO_CHUNKS = [
    "# 研究报告（演示模式）\n\n",
    "> 你正在使用**演示模式**：未配置 API Key，输出为占位内容，用于体验界面与流程。\n",
    "> 在「设置」中填入 API Key 后即可生成真实研究报告。\n\n",
    "## 一、研究框架\n\n",
    "本报告按 AI Berkshire 四大师框架展开：\n\n",
    "1. **生意本质**（段永平视角）\n",
    "2. **护城河与估值**（巴菲特视角）\n",
    "3. **逆向思考**（芒格视角）\n",
    "4. **长期确定性**（李录视角）\n\n",
    "## 二、综合决策备忘录\n\n",
    "| 维度 | 结论 | 信心度 |\n",
    "|------|------|--------|\n",
    "| 生意质量 | （演示占位）平台型生意 | ★★★★☆ |\n",
    "| 护城河 | （演示占位）网络效应+转换成本 | ★★★★☆ |\n",
    "| 估值 | （演示占位）处于历史中位偏低 | ★★★☆☆ |\n\n",
    "## 三、结论\n\n",
    "**灰色地带**（演示结论）：请配置 API Key 后运行真实研究。\n\n",
    "---\n\n",
    "*本演示内容不构成投资建议。*\n",
]


def _demo_stream() -> Iterator[str]:
    for chunk in DEMO_CHUNKS:
        time.sleep(0.06)
        yield chunk


def _resolve_max_tokens(value) -> int | None:
    """「不限」/空 → None（不传 max_tokens，交给服务端默认）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == UNLIMITED:
        return None
    try:
        n = int(float(s.rstrip("kK")) * (1000 if s.lower().endswith("k") else 1))
        return n if n > 0 else None
    except ValueError:
        return None


# ---------------------------------------------------------------- OpenAI 兼容
def _openai_stream(cfg: dict, prompt: str) -> Iterator[str]:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    headers.update(cfg.get("headers") or {})
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 AI Berkshire 投研助手，严格按照用户给出的技能定义执行投资研究，"
                    "输出结构完整、数据可追溯、结论明确的中文研究报告。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": float(cfg.get("temperature", 0.7)),
        "stream": True,
    }
    max_tokens = _resolve_max_tokens(cfg.get("max_output_tokens"))
    if max_tokens:
        payload["max_tokens"] = max_tokens

    timeout = (15, 600)
    with requests.post(url, headers=headers, json=payload, stream=True,
                       timeout=timeout) as resp:
        if resp.status_code != 200:
            raise LLMError(f"API 调用失败 HTTP {resp.status_code}：{resp.text[:500]}")
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


# ---------------------------------------------------------------- Anthropic
def _anthropic_stream(cfg: dict, prompt: str) -> Iterator[str]:
    base = cfg.get("base_url") or "https://api.anthropic.com"
    url = base.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    headers.update(cfg.get("headers") or {})
    payload = {
        "model": cfg["model"],
        "temperature": float(cfg.get("temperature", 0.7)),
        "system": (
            "你是 AI Berkshire 投研助手，严格按照用户给出的技能定义执行投资研究，"
            "输出结构完整、数据可追溯、结论明确的中文研究报告。"
        ),
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    # Anthropic 必须传 max_tokens；「不限」时给一个宽松上限
    max_tokens = _resolve_max_tokens(cfg.get("max_output_tokens")) or 8192
    payload["max_tokens"] = max_tokens

    with requests.post(url, headers=headers, json=payload, stream=True,
                       timeout=(15, 600)) as resp:
        if resp.status_code != 200:
            raise LLMError(f"API 调用失败 HTTP {resp.status_code}：{resp.text[:500]}")
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if not data:
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "content_block_delta":
                delta = obj.get("delta") or {}
                if delta.get("text"):
                    yield delta["text"]
            elif obj.get("type") == "error":
                raise LLMError(f"Anthropic 错误：{obj.get('error', {}).get('message', '未知错误')}")


# ---------------------------------------------------------------- 统一入口
def stream_research(prompt: str, user_id: str = "") -> Iterator[str]:
    """按指定用户当前启用配置流式产出研究文本。"""
    cfg = get_active_config(user_id)
    provider = (cfg.get("provider") or "demo").lower()
    if provider == "demo" or not cfg.get("api_key"):
        if provider != "demo" and not cfg.get("api_key"):
            yield (
                "⚠️ **未配置 API Key**，已自动切换到演示模式。\n\n"
                "请到「设置」页面配置 API Key 后重新运行。\n\n"
            )
        yield from _demo_stream()
        return
    if provider == "anthropic":
        yield from _anthropic_stream(cfg, prompt)
    else:
        yield from _openai_stream(cfg, prompt)


def test_connection(user_id: str = "") -> dict:
    """轻量连通性测试：非 demo 时发一条短消息。"""
    cfg = get_active_config(user_id)
    provider = (cfg.get("provider") or "demo").lower()
    if provider == "demo" or not cfg.get("api_key"):
        return {"ok": True, "provider": "demo", "message": "演示模式可用（无需 API Key）"}
    try:
        text = ""
        for chunk in stream_research("请只回复“连接成功”四个字。", user_id):
            text += chunk
            if len(text) > 40:
                break
        return {"ok": True, "provider": provider, "message": f"连接成功：{text.strip()[:40]}"}
    except LLMError as e:
        return {"ok": False, "provider": provider, "message": str(e)}
    except requests.RequestException as e:
        return {"ok": False, "provider": provider, "message": f"网络错误：{e}"}
