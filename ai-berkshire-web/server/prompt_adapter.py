# -*- coding: utf-8 -*-
"""网页运行时提示词适配：让工具环境设计的 skill 在纯 LLM 环境可用。

核心思路：
- 工具步骤（Bash 验算 / Task 后台 Agent / WebSearch / 文件写入）→ 改为
  「推理完成 + 置信度标注 + 报告声明」，禁止模型因缺工具而打回；
- 团队型 skill（*-team）→ 由 multi_agent.py 真·并行编排 4 个角色 Agent。
"""
from __future__ import annotations

# 注入到所有 skill 提示词前的运行时规则
WEB_RUNTIME_RULES = """\
【网页版运行时适配规则 · 优先级高于技能原文中的工具约束】

你现在运行在网页版研究环境中：**没有 Bash、没有 Task/后台 Agent、没有 WebSearch、没有文件写入工具**。
技能原文是为 Claude Code / Codex 工具环境设计的，其中的工具步骤按下述规则适配执行：

1. **禁止打回**：绝对不允许以"缺少工具/无法验证/环境不完整"为由拒绝执行、输出"打回"、或只输出过程说明。你的任务是产出**完整的研究报告本身**。
2. **数据收集步骤**（Task 后台 Agent / WebSearch）→ 基于你的知识与常识完成分析；报告开头声明「数据基于模型知识，截止日期前的信息，最新数据需人工补充」。
3. **金融验算步骤**（financial_rigor.py / Bash）→ 在报告中**列出计算式与结果**（如：市值 = 股价 × 总股本 = …），并标注「待验算」；关键数字标注置信度（高/中/低）。
4. **文件写入步骤**（写入 ~/xx.md）→ 忽略，直接以 Markdown 输出全文。
5. **抽检/准出步骤**（report_audit.py 等）→ 以"四视角互查"替代：输出前自查一遍数据一致性，冲突处显式标注。
6. 保留技能的**全部方法论结构**：四大师视角、模块顺序、评分表、镜子测试、结论纪律照常执行。
7. 报告末尾固定附加一行：
   > *⚠️ 本报告由网页版纯推理生成，关键数据未经程序化验算（标注"待验算"），仅供研究参考，不构成投资建议。*
"""

# ---------------------------------------------------------------- 四大师角色
ROLES = [
    {
        "id": "business",
        "label": "段永平视角 · 商业模式",
        "persona": "段永平",
        "focus": (
            "商业模式本质与护城河：这门生意靠什么赚钱？用户为什么付钱、有没有替代？"
            "竞争对手拿100亿能复制吗？先看生意对不对，再看其他一切。"
        ),
    },
    {
        "id": "finance",
        "label": "巴菲特视角 · 财务估值",
        "persona": "巴菲特",
        "focus": (
            "财务质量与估值：收入/利润/自由现金流的真实性与可持续性，ROIC、利润率趋势，"
            "当前估值贵不贵、安全边际多少。所有计算列出算式（标注「待验算」）。"
        ),
    },
    {
        "id": "industry",
        "label": "芒格视角 · 行业竞争",
        "persona": "芒格",
        "focus": (
            "逆向思考与行业格局：反过来想——什么情况下这家公司会死？"
            "竞争格局是否在恶化？聪明人为什么在做空？列出失败情景。"
        ),
    },
    {
        "id": "risk",
        "label": "李录视角 · 风险管理层",
        "persona": "李录",
        "focus": (
            "长期确定性与风险：10年后这家公司还在吗？管理层是否值得托付？"
            "治理结构、关键人风险、地缘与监管风险。不确定就说不确定。"
        ),
    },
]


# ---------------------------------------------------------------- 市场范围
MARKET_OPTIONS = {
    "all": "全部市场（A股/港股/美股/国际市场）",
    "a": "仅 A 股（沪深北交所）",
    "hk": "仅港股（HKEX）",
    "us": "仅美股（NASDAQ/NYSE）",
    "a_hk": "A 股 + 港股",
}

# 筛选类技能：识别（排名约束由 skill_params 按用户可调 N 生成）
SCREENING_SKILLS = {
    "industry-funnel", "industry-research", "quality-screen",
    "bottleneck-hunter", "era-alpha", "investment-checklist",
}


def build_scope_rules(skill_id: str, market: str) -> str:
    """生成市场范围硬约束（优先级高于技能原文）。"""
    parts: list[str] = []
    if market and market != "all" and market in MARKET_OPTIONS:
        mkt = MARKET_OPTIONS[market]
        parts.append(
            f"【市场范围硬约束 · 优先级最高，覆盖技能原文】\n"
            f"本次研究**只覆盖 {mkt}**：\n"
            f"- 扫描、粗筛、精析、终选的每一个环节都**只允许出现该市场内的上市公司**；\n"
            f"- 技能原文中要求扫描其他市场（美股/港股/国际市场/未上市公司）的步骤**全部跳过**；\n"
            f"- 报告中出现任何范围外市场的公司即为违规（包括举例、对标、可比公司）；\n"
            f"- 报告开头注明：「市场范围：{mkt}」。"
        )
    return "\n\n".join(parts)


def adapt_skill_prompt(skill_content: str, arguments: str,
                       skill_id: str = "", market: str = "all",
                       params: dict | None = None) -> str:
    """给技能正文套上网页运行时规则（含市场范围与动态参数约束）。"""
    from .skill_params import build_param_rules
    args = (arguments or "").strip() or "（未指定标的）"
    body = skill_content.replace(
        "$ARGUMENTS", args if arguments else "（未指定标的，请给出使用示例）"
    )
    constraints = "\n\n".join(
        s for s in (build_scope_rules(skill_id, market),
                    build_param_rules(skill_id, params)) if s)
    scope_block = f"\n\n{constraints}\n" if constraints else ""
    return (
        f"{WEB_RUNTIME_RULES}{scope_block}\n\n"
        f"===== 研究任务 =====\n研究对象/参数：{args}\n\n"
        f"===== 技能方法论（工具步骤按上述规则适配）=====\n\n{body}\n\n"
        f"===== 输出要求 =====\n"
        f"直接输出完整研究报告（Markdown）。结构严格遵循技能方法论；"
        f"关键数据标注置信度；结尾给出明确结论（通过/不通过/灰色地带 + 理由）。"
    )


def build_role_prompt(role: dict, skill_content: str, arguments: str,
                      skill_id: str = "", market: str = "all",
                      params: dict | None = None) -> str:
    """单个角色 Agent 的提示词（多 Agent 第一阶段）。"""
    from .skill_params import build_param_rules
    args = (arguments or "").strip() or "（未指定标的）"
    # 技能正文可能很长，取前 3500 字做方法论参考，避免撑爆上下文
    method_ref = skill_content[:3500]
    constraints = "\n\n".join(
        s for s in (build_scope_rules(skill_id, market),
                    build_param_rules(skill_id, params)) if s)
    scope_block = f"\n\n{constraints}\n" if constraints else ""
    return (
        f"{WEB_RUNTIME_RULES}{scope_block}\n\n"
        f"你是投研团队中的「{role['label']}」（以{role['persona']}的思维方式思考）。\n"
        f"研究对象：{args}\n\n"
        f"你的分析焦点：{role['focus']}\n\n"
        f"要求：\n"
        f"1. 只做你这个视角的分析，输出 Markdown 小节（含小标题、要点、可选小表格）；\n"
        f"2. 结尾给出本视角评分（x/5）与一句话判断；\n"
        f"3. 800字以内，观点鲜明，不要两头讨好。\n\n"
        f"===== 完整技能方法论（供参考，工具步骤按运行时规则适配）=====\n{method_ref}"
    )


def build_synthesis_prompt(arguments: str, sections: list[dict]) -> str:
    """Team Lead 综合提示词（多 Agent 第二阶段）。"""
    args = (arguments or "").strip() or "（未指定标的）"
    parts = []
    for s in sections:
        parts.append(f"### {s['label']}\n{s['text']}\n")
    joined = "\n".join(parts)
    return (
        f"{WEB_RUNTIME_RULES}\n\n"
        f"你是投研团队 Team Lead（巴菲特-芒格-段永平-李录四大师综合框架）。\n"
        f"研究对象：{args}\n\n"
        f"下面是4个并行研究 Agent 各自独立产出的视角分析：\n\n{joined}\n\n"
        f"请综合成一份完整投资研究报告，要求：\n"
        f"1. 标题：# {args} 投资研究报告（多Agent团队）\n"
        f"2. 开头：一句话结论 + 四维评分总表（表格：维度/框架/评分/核心判断）\n"
        f"3. 正文：按「商业模式→财务估值→行业竞争→风险管理层」组织，保留各视角的分歧与张力"
        f"（巴菲特说便宜、李录说不确定就不买——这种冲突要如实呈现，不要抹平）；\n"
        f"4. 三情景估值表（乐观/中性/悲观，列出算式，标注「待验算」）；\n"
        f"5. 投资建议表（激进/稳健/保守三档，含价格区间）；\n"
        f"6. 镜子测试段落（5句话说清楚为什么买/不买）；\n"
        f"7. 末尾附加运行时免责声明。\n"
        f"结论必须明确：通过 / 不通过 / 灰色地带。"
    )
