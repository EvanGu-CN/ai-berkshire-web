# -*- coding: utf-8 -*-
"""技能动态参数：定义各技能可灵活配置的维度，生成硬约束注入提示词。

覆盖维度（来自全量技能扫描）：
- 筛选排名榜前 N（筛选类）：可调榜单大小
- 终选家数 / 粗筛保留（行业漏斗）：原文写死 3 家 / ≤10
- 系列篇数（深度系列）：原文 3-8 篇自动
- 时间窗口（新闻脉搏/论文跟踪）：原文默认最近
- 估值年限（深度研究类）：3 / 5 / 10 年（默认 10）
"""
from __future__ import annotations

SCREENING = ["industry-funnel", "industry-research", "quality-screen",
             "bottleneck-hunter", "era-alpha", "investment-checklist"]
DEEP = ["investment-research", "investment-team", "industry-research",
        "industry-funnel", "era-alpha", "bottleneck-hunter"]

PARAM_SCHEMA: dict[str, dict] = {
    "top_n": {
        "label": "筛选排名榜前 N",
        "type": "number", "min": 5, "max": 30, "default": 10,
        "skills": SCREENING,
    },
    "final_count": {
        "label": "终选家数",
        "type": "number", "min": 1, "max": 10, "default": 3,
        "skills": ["industry-funnel"],
    },
    "shortlist_count": {
        "label": "粗筛保留家数",
        "type": "number", "min": 5, "max": 20, "default": 10,
        "skills": ["industry-funnel"],
    },
    "article_count": {
        "label": "系列篇数",
        "type": "select", "default": "auto",
        "skills": ["deep-company-series"],
        "options": [
            {"value": "auto", "label": "自动（按复杂度 3-8 篇）"},
            {"value": "3", "label": "3 篇（精简）"},
            {"value": "5", "label": "5 篇（标准）"},
            {"value": "8", "label": "8 篇（全面）"},
        ],
    },
    "time_window": {
        "label": "时间窗口",
        "type": "select", "default": "1w",
        "skills": ["news-pulse", "thesis-tracker", "thesis-drift"],
        "options": [
            {"value": "24h", "label": "最近 24 小时"},
            {"value": "3d", "label": "最近 3 天"},
            {"value": "1w", "label": "最近一周"},
            {"value": "1m", "label": "最近一月"},
        ],
    },
    "horizon": {
        "label": "估值年限",
        "type": "select", "default": "10y",
        "skills": DEEP,
        "options": [
            {"value": "3y", "label": "3 年"},
            {"value": "5y", "label": "5 年"},
            {"value": "10y", "label": "10 年（默认）"},
        ],
    },
}

_HORIZON_TEXT = {
    "3y": "3", "5y": "5", "10y": "10",
}
_TIME_TEXT = {
    "24h": "过去 24 小时", "3d": "过去 3 天", "1w": "过去一周", "1m": "过去一个月",
}


def params_for_skill(skill_id: str) -> list[dict]:
    """返回某技能可用的参数定义（含默认值）。"""
    out = []
    for pid, defn in PARAM_SCHEMA.items():
        skills = defn.get("skills")
        if skills == "*" or skill_id in skills:
            item = dict(defn)
            item["id"] = pid
            out.append(item)
    return out


def build_param_rules(skill_id: str, params: dict | None) -> str:
    """把用户参数转成硬约束文本（优先级高于技能原文默认值）。"""
    p = params or {}
    parts: list[str] = []

    def valid(pid: str, default):
        v = p.get(pid, default)
        return v if v not in (None, "") else default

    # 筛选排名榜
    if skill_id in SCREENING:
        try:
            top_n = int(valid("top_n", 10))
        except (TypeError, ValueError):
            top_n = 10
        top_n = max(5, min(30, top_n))
        parts.append(
            f"【筛选排名表：前 {top_n} 家】报告中**必须**输出【筛选排名表】："
            f"综合排名前 {top_n} 的公司（列：名次｜公司｜代码｜市场｜综合评分｜一句话理由）；"
            f"候选不足 {top_n} 家则全部列出；"
            f"终选/推荐标的必须注明其排名名次。"
        )

    # 终选家数 / 粗筛保留（行业漏斗）
    if skill_id == "industry-funnel":
        try:
            final_n = int(valid("final_count", 3))
        except (TypeError, ValueError):
            final_n = 3
        final_n = max(1, min(10, final_n))
        try:
            short_n = int(valid("shortlist_count", 10))
        except (TypeError, ValueError):
            short_n = 10
        short_n = max(5, min(20, short_n))
        parts.append(
            f"【漏斗名额覆盖 · 优先级高于原文】\n"
            f"- 第二层粗筛保留 **≤ {short_n} 家**（原文默认 ≤10 作废）；\n"
            f"- 第四层终选 **{final_n} 家**（原文默认 3 家作废），"
            f"每家做四大师深度分析；若优质标的不足 {final_n} 家，"
            f"宁可少选也不要凑数，但需说明缺口。"
        )

    # 系列篇数
    if skill_id == "deep-company-series":
        ac = str(valid("article_count", "auto"))
        if ac != "auto" and ac.isdigit():
            n = max(3, min(8, int(ac)))
            parts.append(
                f"【篇数覆盖 · 优先级高于原文】系列**固定 {n} 篇**"
                f"（原文的 3-8 篇自动判定作废），按核心主轴平均分配到 {n} 篇。"
            )

    # 时间窗口
    if skill_id in ("news-pulse", "thesis-tracker", "thesis-drift"):
        tw = str(valid("time_window", "1w"))
        tw_text = _TIME_TEXT.get(tw, "过去一周")
        parts.append(
            f"【时间窗口】只统计分析**{tw_text}**内发生的事件与变化，"
            f"更早的信息仅作背景，不计入归因。"
        )

    # 估值年限
    if skill_id in DEEP:
        hz = str(valid("horizon", "10y"))
        years = _HORIZON_TEXT.get(hz, "10")
        if years != "10":
            parts.append(
                f"【估值年限覆盖】DCF、终值、回报推演的年限统一改为 **{years} 年**"
                f"（原文默认 10 年作废），十年口径类检查项按 {years} 年口径执行。"
            )

    return "\n\n".join(parts)
