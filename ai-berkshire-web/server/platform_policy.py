"""Generic account/membership policy, independent of research data and model keys."""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

DEFAULT_TIERS = {
    "free": {"label": "基础会员", "quota": 20},
    "vip": {"label": "标准会员", "quota": 200},
    "svip": {"label": "高级会员", "quota": 0},
}
DEFAULT_ACTIONS = {"research": "研究生成", "pipeline": "研究流水线",
                   "memory": "知识库问答", "extract": "结论提取"}


@lru_cache(maxsize=1)
def configuration() -> dict:
    """Read a small optional deployment policy; it must never contain credentials."""
    raw = {}
    source = os.environ.get("VC_PLATFORM_CONFIG", "").strip()
    if source:
        path = Path(source).expanduser()
        if path.stat().st_size > 32 * 1024:
            raise ValueError("平台策略文件不能超过 32 KiB")
        raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"membership_tiers", "default_tier", "timezone", "session_days",
               "invite_expiry_hours", "invite_daily_limit", "usage_actions"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("平台策略包含不支持的字段；不要在此文件保存密钥")
    tiers = raw.get("membership_tiers", DEFAULT_TIERS)
    if not isinstance(tiers, dict) or not 1 <= len(tiers) <= 12:
        raise ValueError("会员等级需为 1–12 个")
    for key, tier in tiers.items():
        if (not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key) or not isinstance(tier, dict)
                or set(tier) != {"label", "quota"} or not isinstance(tier["label"], str)
                or not 1 <= len(tier["label"].strip()) <= 32
                or type(tier["quota"]) is not int or not 0 <= tier["quota"] <= 10000):
            raise ValueError("会员等级的标识、名称或每日额度无效")
    default_tier = raw.get("default_tier", "free")
    if default_tier not in tiers:
        raise ValueError("默认会员等级必须存在")
    actions = raw.get("usage_actions", DEFAULT_ACTIONS)
    if (not isinstance(actions, dict) or len(actions) > 32
            or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key)
                   or not isinstance(value, str) or not 1 <= len(value) <= 48
                   for key, value in actions.items())):
        raise ValueError("用量操作名称无效")
    result = {"membership_tiers": tiers, "default_tier": default_tier,
              "timezone": raw.get("timezone", "Asia/Shanghai"),
              "usage_actions": actions, "metering_unit": "request"}
    try:
        result["tzinfo"] = ZoneInfo(result["timezone"])
    except (KeyError, TypeError):
        raise ValueError("额度重置时区无效；请安装 tzdata 并使用 IANA 时区名称") from None
    for key, default, maximum in (("session_days", 30, 365),
                                  ("invite_expiry_hours", 72, 720),
                                  ("invite_daily_limit", 20, 1000)):
        value = raw.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("平台策略中的有效期或数量上限无效")
        result[key] = value
    return result


def public_policy() -> dict:
    return {key: value for key, value in configuration().items() if key != "tzinfo"}
