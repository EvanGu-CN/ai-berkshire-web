# -*- coding: utf-8 -*-
"""研究记忆问答：检索相关报告 → LLM 带引用作答；LLM 深度抽取结论。"""
from __future__ import annotations

import json
import re
from typing import Iterator

from . import memory_store as ms
from .llm_client import stream_research

# ---------------------------------------------------------------- 问答
QA_SYSTEM = (
    "你是 AI Berkshire Web 研究记忆助手。用户会给你若干段来自历史投研报告的资料片段，"
    "每段带 [编号] 与报告标题、日期。\n"
    "回答要求：\n"
    "1. 只依据给定资料回答，资料不足时明确说「记忆库中没有找到」，不要编造；\n"
    "2. 引用资料时在句末标注来源编号，如 [1][3]；\n"
    "3. 同一结论多份报告一致/冲突时都要指出（标注各自来源）；\n"
    "4. 用中文，简洁直接，先给结论再给依据。"
)


def build_qa_prompt(question: str, hits: list[dict]) -> str:
    parts = ["## 问题", question, "", "## 资料片段（来自研究记忆库）"]
    for i, h in enumerate(hits, 1):
        parts.append(f"\n[{i}] 《{h['title']}》 · {h['grp']} · {h['date']}")
        parts.append(f"路径: {h['path']}")
        parts.append(f"片段: {h['snippet']}")
    parts.append(
        "\n\n请基于以上资料回答问题。每个关键论断后标注 [编号]。"
    )
    return "\n".join(parts)


def ask(question: str, top_n: int = 6,
         user_id: str = "", date_filter=None) -> tuple[list[dict], Iterator[str]]:
    """返回 (引用来源列表, 回答文本流)。"""
    hits = ms.search(question, top_n=top_n, date_filter=date_filter)
    if not hits:
        def gen_empty():
            yield "当前条件下未找到相关报告。可扩大报告日期范围、调整问题，或检查研究记忆的索引状态。"
        return [], gen_empty()

    prompt = build_qa_prompt(question, hits)

    def gen():
        buf: list[str] = []
        for chunk in stream_research(prompt, user_id):
            buf.append(chunk)
            yield chunk
        # 自动附来源列表（保证可追溯）
        yield "\n\n---\n\n**📎 资料来源**\n\n"
        for i, h in enumerate(hits, 1):
            yield f"- [{i}] 《{h['title']}》 · {h['grp']} · {h['date']} · `{h['path']}`\n"

    return hits, gen()


# ---------------------------------------------------------------- 深度抽取
EXTRACT_PROMPT_TMPL = """从以下投研报告中抽取结构化结论，只输出一个 JSON 对象（不要 Markdown 代码块、不要解释），字段：
{{
  "verdict": "结论关键词（如：通过 / 不通过 / 灰色地带 / 有条件通过 / 建议观望，没有则留空）",
  "score": 数字或null（综合评分分子）,
  "score_max": 数字或null（评分分母，一般为5）,
  "price_range": "价格区间字符串（如 95-105 美元，没有则留空）",
  "summary": "80字以内的核心结论摘要",
  "extra": {{
    "moat": "护城河一句话评价",
    "risk": "最大风险一句话",
    "strategy": "操作策略一句话（若有）"
  }}
}}

报告标题：《{title}》

报告正文（可能截断）：
---
{body}
---"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def extract_conclusion_llm(title: str, content: str,
                             max_body: int = 6000,
                             user_id: str = "") -> dict | None:
    """LLM 深度抽取单篇结论。返回结构化 dict 或 None。"""
    body = content[:max_body]
    prompt = EXTRACT_PROMPT_TMPL.format(title=title, body=body)
    text = "".join(stream_research(prompt, user_id))
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        # 尝试清理尾部逗号等常见瑕疵
        cleaned = re.sub(r",\s*([}\]])", r"\1", m.group())
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    out = {
        "title": title,
        "verdict": str(data.get("verdict") or "").strip(),
        "score": data.get("score") if isinstance(data.get("score"), (int, float)) else None,
        "score_max": data.get("score_max") if isinstance(data.get("score_max"), (int, float)) else None,
        "price_range": str(data.get("price_range") or "").strip(),
        "summary": str(data.get("summary") or "").strip()[:300],
        "extra": data.get("extra") if isinstance(data.get("extra"), dict) else {},
    }
    return out
