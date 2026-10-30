# -*- coding: utf-8 -*-
"""研究流水线：行业/候选池 → 粗筛 → Checklist → 深度研究（漏斗自动化）。

阶段产物统一为 JSON 结构，逐层淘汰并保留淘汰理由。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterator

from . import history_store as hs
from . import skills_catalog as sc
from .llm_client import stream_research
from .settings_store import get_active_config

STAGES = [
    {"id": "screen", "label": "去劣筛选（7条硬指标）", "skill": "quality-screen"},
    {"id": "checklist", "label": "买入前 Checklist（六关）", "skill": "investment-checklist"},
    {"id": "deep", "label": "四大师深度研究", "skill": "investment-research"},
]


class StageResultError(ValueError):
    """A safe, user-facing failure after a stage's bounded format retry."""


def _is_demo(user_id: str = "") -> bool:
    s = get_active_config(user_id)
    return (s.get("provider") or "demo").lower() == "demo" or not s.get("api_key")


def _mock_stage_result(stage_id: str, companies: list[str]) -> list[dict]:
    """演示模式：按公司名哈希稳定产出过/不过 + 理由。"""
    out = []
    for c in companies:
        h = int(hashlib.md5(f"{stage_id}:{c}".encode()).hexdigest(), 16)
        passed = (h % 10) < 6  # 约 60% 通过
        reason = (
            "演示模式判定：指标大体达标，建议进入下一轮"
            if passed else
            "演示模式判定：硬指标不满足（盈利能力/规模/确定性等），建议淘汰"
        )
        out.append({"company": c, "pass": passed, "reason": reason})
    return out


def _parse_stage_json(text: str, companies: list[str]) -> list[dict]:
    """Validate every input identity and strict boolean verdict; never auto-pass malformed output."""
    # Reasoning and prose can contain brackets; a greedy first-to-last slice
    # combines these with the final answer and breaks otherwise valid JSON.
    text = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    results = []
    offset = 0
    while (start := text.find("[", offset)) >= 0:
        offset = start + 1
        try:
            data, end = decoder.raw_decode(text, start)
            offset = end
            if not isinstance(data, list) or not data:
                raise ValueError("筛选结果为空")
            by_name = {}
            for it in data:
                if not isinstance(it, dict) or type(it.get("pass")) is not bool:
                    raise ValueError("筛选结论格式无效")
                name = str(it.get("company") or it.get("name") or "").strip()
                if name not in companies or name in by_name:
                    raise ValueError("筛选标的与输入不符")
                by_name[name] = {"company":name, "pass":it["pass"], "reason":str(it.get("reason") or "")[:200]}
            if set(by_name) != set(companies):
                raise ValueError("筛选结果不完整")
            results.append([by_name[name] for name in companies])
        except (ValueError, TypeError):
            continue
    if len(results) == 1:
        return results[0]
    raise ValueError("筛选结果未能验证；需要唯一、完整且结论为布尔值的公司结果数组")


def _run_stage(stage: dict, companies: list[str], user_id: str = "") -> Iterator[dict]:
    """执行单个筛选阶段，yield 结果事件。"""
    if _is_demo(user_id):
        for it in _mock_stage_result(stage["id"], companies):
            yield {"type": "stage_item", "stage": stage["id"], **it}
        return

    prompt = (
        f"请对以下公司执行「{stage['label']}」筛选流程，只输出一个 JSON 数组，"
        f"不要任何其他文字。每项格式：{{\"company\": 公司名, \"pass\": true/false, "
        f"\"reason\": \"30字以内理由\"}}。\n\n公司列表：\n- "
        + "\n- ".join(companies)
    )
    doc = sc.get_skill_content(stage["skill"])
    if doc:
        prompt = doc["content"][:4000] + "\n\n---\n\n" + prompt
    prompt += (
        "\n\n本次用于程序解析：以上技能中的报告、表格等输出要求由本段覆盖。"
        "只返回 JSON 数组，不要 Markdown、分析过程或多个版本。"
        "company 必须原样使用公司列表中的名称，每家公司恰好一项；"
        "pass 必须是 JSON 布尔值 true 或 false，不可使用字符串。"
    )

    chars = 0
    for attempt in range(2):
        chunks = []
        for chunk in stream_research(prompt, user_id):
            chunks.append(chunk)
            chars += len(chunk)
            yield {"type": "stage_progress", "stage": stage["id"], "chars": chars}
        try:
            items = _parse_stage_json("".join(chunks), companies)
        except ValueError as exc:
            if attempt:
                raise StageResultError(
                    f"{stage['label']}返回的结果格式仍未通过校验（已自动重试一次），"
                    "已完成阶段保留在研究历史中；请重试"
                ) from exc
            yield {"type": "stage_retry", "stage": stage["id"]}
            prompt += (
                "\n\n上次返回未通过格式校验，请重新执行本阶段并严格遵循上述 JSON 契约。"
                "不要省略公司，不要输出解释，也不要将无法核实的公司自动判为通过。"
            )
            continue
        for it in items:
            yield {"type": "stage_item", "stage": stage["id"], **it}
        return


def run_pipeline(targets: str, stages: list[str], deep_count: int = 1,
                  user_id: str = "", cancelled=None) -> Iterator[dict]:
    """执行整条流水线，yield NDJSON 事件。"""
    companies = list(dict.fromkeys(t.strip() for t in re.split(r"[,，、\n]+", targets) if t.strip()))
    if not companies:
        yield {"type": "error", "message": "请至少输入一个标的"}
        return

    stage_ids = [s["id"] for s in STAGES if s["id"] in stages]
    if "deep" in stage_ids:
        stage_ids = [s for s in stage_ids if s != "deep"]
        do_deep = True
    else:
        do_deep = False

    yield {"type": "pipeline_start", "companies": companies, "stages": stage_ids,
           "deep": do_deep, "demo": _is_demo(user_id)}

    # 收集运行数据用于落盘（增量更新，关页面/中断也不丢）
    stage_results: dict[str, list[dict]] = {}
    stage_labels: dict[str, str] = {}
    deep_outputs: list[dict] = []
    survivors = companies
    run_record_id: str | None = None

    def _save_run():
        nonlocal run_record_id
        try:
            _active = get_active_config(user_id)
            content = _build_run_report(companies, stage_results, stage_labels,
                                        deep_outputs, survivors)
            if not content:
                return
            rec = hs.save_record(user_id,
                skill_id="industry-funnel",
                skill_title=f"流水线运行·{'/'.join(companies[:3])}"
                + ("…" if len(companies) > 3 else ""),
                arguments=", ".join(companies),
                content=content,
                provider=(_active.get("provider") or "demo"),
                model=(_active.get("model") or ""),
                record_id=run_record_id,
            )
            run_record_id = rec["id"]
        except OSError:
            pass

    # ---- 筛选阶段（缓冲执行，拿到 survivors 再进下一轮）----
    try:
        for sid in stage_ids:
            if cancelled is not None and cancelled.is_set():
                return
            stage = next(x for x in STAGES if x["id"] == sid)
            yield {"type": "stage_start", "stage": sid, "label": stage["label"],
                   "input": list(survivors)}
            items = []
            for it in _run_stage(stage, survivors, user_id):
                if cancelled is not None and cancelled.is_set():
                    return
                if it["type"] == "stage_item":
                    items.append(it)
                yield it
            stage_results[sid] = items
            stage_labels[sid] = stage["label"]
            survivors = [it["company"] for it in items if it.get("pass")]
            yield {"type": "stage_end", "stage": sid, "survivors": list(survivors)}
            _save_run()          # 阶段完成即落盘
            if not survivors:
                yield {"type": "pipeline_end", "survivors": [],
                       "message": "全部标的在筛选中被淘汰"}
                return

        if do_deep:
            top = survivors[:max(1, deep_count)]
            for i, company in enumerate(top, 1):
                if cancelled is not None and cancelled.is_set():
                    return
                yield {"type": "deep_start", "company": company,
                       "index": i, "total": len(top)}
                prompt = (sc.build_prompt("investment-research", company)
                          or f"对 {company} 进行投资研究")
                buf: list[str] = []
                for chunk in stream_research(prompt, user_id):
                    if cancelled is not None and cancelled.is_set():
                        break
                    buf.append(chunk)
                    yield {"type": "deep_delta", "company": company, "text": chunk}
                content = "".join(buf).strip()
                deep_outputs.append({"company": company, "content": content})
                yield {"type": "deep_end", "company": company, "chars": len(content)}
                _save_run()      # 每篇深度完成即落盘

        yield {"type": "pipeline_end", "survivors": survivors}
    finally:
        # 中途停止/异常也要落盘已完成部分
        _save_run()


def _build_run_report(companies: list[str], stage_results: dict[str, list[dict]],
                      stage_labels: dict[str, str], deep_outputs: list[dict],
                      survivors: list[str]) -> str:
    """把整条流水线打包为一份可回看的 Markdown 记录。"""
    from datetime import datetime
    lines = [
        f"# 流水线运行记录",
        "",
        f"> 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  "
        f"候选池：{', '.join(companies)}（{len(companies)} 家）",
        "",
        "## 一、运行概览",
        "",
        "| 阶段 | 输入 | 通过 | 淘汰 |",
        "|------|------|------|------|",
    ]
    for sid, items in stage_results.items():
        label = stage_labels.get(sid, sid)
        n_pass = sum(1 for it in items if it.get("pass"))
        lines.append(
            f"| {label} | {len(items)} | {n_pass} | {len(items) - n_pass} |")
    lines += ["", f"**最终入围：{', '.join(survivors) if survivors else '无'}**", ""]

    for sid, items in stage_results.items():
        lines += [f"## 二、{stage_labels.get(sid, sid)}结果", "",
                  "| 标的 | 判定 | 理由 |", "|------|------|------|"]
        for it in items:
            verdict = "✅ 通过" if it.get("pass") else "❌ 淘汰"
            reason = (it.get("reason") or "").replace("|", "\\|")
            lines.append(f"| {it.get('company','')} | {verdict} | {reason} |")
        lines.append("")

    if deep_outputs:
        lines.append("## 三、深度研究报告")
        for d in deep_outputs:
            lines += ["", f"### {d['company']}", "", d.get("content") or "（无内容）", ""]

    lines += ["---", "",
              "*本记录由研究流水线自动生成，不构成投资建议。*"]
    return "\n".join(lines)
