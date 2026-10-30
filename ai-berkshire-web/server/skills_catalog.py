# -*- coding: utf-8 -*-
"""技能目录：解析 ai-berkshire/skills 下的 Skill 定义，归类并提供调用元数据。"""
from __future__ import annotations

import re
from pathlib import Path

from .paths import SKILLS_DIR

# 与 README「Skills 一览」对齐的分类元数据（name -> category / 用途 / 场景 / 示例）
SKILL_META: dict[str, dict] = {
    "investment-research": {
        "category": "深度研究",
        "purpose": "四大师综合深度分析",
        "scenario": "对一家上市公司进行全方位投资研究",
        "example": "腾讯",
        "keywords": ["研究", "深度", "四大师"],
    },
    "investment-team": {
        "category": "深度研究",
        "purpose": "四大师多视角研究",
        "scenario": "从四个投资视角独立分析，再整合研究结论",
        "example": "美团",
        "keywords": ["团队", "并行", "综合"],
    },
    "management-deep-dive": {
        "category": "深度研究",
        "purpose": "管理层纵深研究",
        "scenario": "当管理层是核心变量时深挖",
        "example": "王兴 美团",
        "keywords": ["管理层", "创始人", "CEO"],
    },
    "private-company-research": {
        "category": "深度研究",
        "purpose": "未上市公司深度研究",
        "scenario": "研究蚂蚁、SpaceX等信息稀缺的未上市公司",
        "example": "SpaceX",
        "keywords": ["未上市", "一级市场", "独角兽"],
    },
    "deep-company-series": {
        "category": "深度研究",
        "purpose": "8篇长文系列拆一家公司",
        "scenario": "公众号级深度系列，从认知重置到决策闭环",
        "example": "拼多多",
        "keywords": ["系列", "长文", "公众号"],
    },
    "earnings-review": {
        "category": "财报分析",
        "purpose": "财报精读（一手资料）",
        "scenario": "只读原始财报，像巴菲特一样读年报",
        "example": "腾讯 2025Q4",
        "keywords": ["财报", "年报", "季报"],
    },
    "earnings-team": {
        "category": "财报分析",
        "purpose": "财报多视角精读",
        "scenario": "四大师并行解读财报 → 可发布文章",
        "example": "PDD 2025年报",
        "keywords": ["财报", "团队", "发布"],
    },
    "industry-research": {
        "category": "行业筛选",
        "purpose": "产业链全景扫描",
        "scenario": "研究一个行业的全部投资机会（按产业链环节切片）",
        "example": "核电",
        "keywords": ["行业", "产业链", "全景"],
    },
    "industry-funnel": {
        "category": "行业筛选",
        "purpose": "行业漏斗筛选",
        "scenario": "全市场 → 粗筛 ≤10 家 → 终选 3 家深度分析",
        "example": "AI算力",
        "keywords": ["筛选", "漏斗", "行业"],
    },
    "quality-screen": {
        "category": "行业筛选",
        "purpose": "去劣筛选（7条硬指标）",
        "scenario": "快速排除非一流公司，支持批量筛",
        "example": "恒生指数成分股",
        "keywords": ["筛选", "排除", "质量"],
    },
    "bottleneck-hunter": {
        "category": "行业筛选",
        "purpose": "供应链瓶颈猎手",
        "scenario": "从超级趋势出发，寻找产业链物理瓶颈和套利机会",
        "example": "AI基础设施",
        "keywords": ["瓶颈", "供应链", "套利"],
    },
    "era-alpha": {
        "category": "行业筛选",
        "purpose": "时代α捕手",
        "scenario": "识别时代级高增长主线中的核心α",
        "example": "AI应用",
        "keywords": ["时代", "alpha", "主线"],
    },
    "investment-checklist": {
        "category": "行业筛选",
        "purpose": "巴菲特买入前 Checklist",
        "scenario": "六关快速筛选，10分钟决定是否值得深入",
        "example": "茅台, 英伟达, 苹果",
        "keywords": ["checklist", "清单", "快速"],
    },
    "income-investment": {
        "category": "持仓管理",
        "purpose": "收益型股票分析",
        "scenario": "区分可持续收益、机会型高息与收益率陷阱",
        "example": "Verizon mode=existing role=core-income quantity=100",
        "keywords": ["红利", "收益", "股息"],
    },
    "portfolio-review": {
        "category": "持仓管理",
        "purpose": "组合管理与优化",
        "scenario": "仓位、集中度、再平衡",
        "example": "腾讯30%, 美团20%, 茅台20%, 现金30%",
        "keywords": ["组合", "仓位", "再平衡"],
    },
    "thesis-tracker": {
        "category": "持仓管理",
        "purpose": "投资论文追踪",
        "scenario": "买入后的纪律系统：持续跟踪论文是否被证伪",
        "example": "拼多多",
        "keywords": ["论文", "跟踪", "纪律"],
    },
    "thesis-drift": {
        "category": "持仓管理",
        "purpose": "投资论文漂移检测",
        "scenario": "对比两份论文/报告，区分事实变化与措辞变化",
        "example": "拼多多 报告A 报告B",
        "keywords": ["漂移", "对比", "证伪"],
    },
    "news-pulse": {
        "category": "持仓管理",
        "purpose": "股价异动快速归因",
        "scenario": "股价大涨/大跌时10分钟搞清发生了什么",
        "example": "腾讯",
        "keywords": ["异动", "归因", "新闻"],
    },
    "dyp-ask": {
        "category": "思维工具",
        "purpose": "段永平问答",
        "scenario": "以段永平的方式思考任何问题",
        "example": "拼多多的护城河到底在哪里？",
        "keywords": ["段永平", "问答", "思考"],
    },
    "financial-data": {
        "category": "思维工具",
        "purpose": "财务数据获取与交叉验证规范",
        "scenario": "确保关键数据来自2个独立来源，误差>1%告警",
        "example": "腾讯市值交叉验证",
        "keywords": ["数据", "验证", "规范"],
    },
    "wechat-article": {
        "category": "思维工具",
        "purpose": "微信公众号文章",
        "scenario": "从作者、编辑和读者视角推敲文章",
        "example": "美团",
        "keywords": ["公众号", "文章", "写作"],
    },
}

CATEGORY_ORDER = ["深度研究", "财报分析", "行业筛选", "持仓管理", "思维工具"]


def _slug(path: Path) -> str:
    return path.stem


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines()[:8]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _extract_argument_hints(content: str) -> list[str]:
    """从 skill 正文里抓 $ARGUMENTS / 用法示例的粗粒度提示。"""
    hints = []
    m = re.search(r"\$ARGUMENTS", content)
    if m:
        hints.append("支持 $ARGUMENTS 占位参数（公司名/主题/组合等）")
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("对 $ARGUMENTS"):
            hints.append(s)
            break
    return hints


def load_skills() -> list[dict]:
    """扫描 skills 目录，返回技能卡片列表（含正文摘要，不含全文）。"""
    items: list[dict] = []
    if not SKILLS_DIR.is_dir():
        return items
    for path in sorted(SKILLS_DIR.glob("*.md")):
        slug = _slug(path)
        meta = SKILL_META.get(slug, {})
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = _extract_title(content, slug)
        # 摘要：去掉标题后的前 3 行正文
        body_lines = [ln for ln in content.splitlines() if ln.strip()]
        summary = ""
        if len(body_lines) > 1:
            summary = " ".join(body_lines[1:4])[:180]
        items.append({
            "id": slug,
            "title": title,
            "category": meta.get("category", "其他"),
            "purpose": meta.get("purpose", ""),
            "scenario": meta.get("scenario", ""),
            "example": meta.get("example", ""),
            "keywords": meta.get("keywords", []),
            "summary": summary,
            "lines": content.count("\n") + 1,
            "path": str(path),
        })
    return items


def get_skill_content(skill_id: str) -> dict | None:
    """返回完整 skill 文档（供 LLM 提示词使用）。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", skill_id):
        return None
    path = SKILLS_DIR / f"{skill_id}.md"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    meta = SKILL_META.get(skill_id, {})
    return {
        "id": skill_id,
        "title": _extract_title(content, skill_id),
        "category": meta.get("category", "其他"),
        "content": content,
        "path": str(path),
    }


def build_prompt(skill_id: str, arguments: str, market: str = "all",
                 params: dict | None = None) -> str | None:
    """将 skill 模板与用户参数组装成研究提示词（含网页运行时适配与动态参数约束）。"""
    doc = get_skill_content(skill_id)
    if doc is None:
        return None
    from .prompt_adapter import adapt_skill_prompt
    return adapt_skill_prompt(doc["content"], arguments,
                              skill_id=skill_id, market=market, params=params)
