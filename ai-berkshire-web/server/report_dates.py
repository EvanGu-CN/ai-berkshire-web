"""Shared report-date windows. No import/mtime fallback and no data migration."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
import re

CHINA_TZ = timezone(timedelta(hours=8))
PERIODS = {"1w": "近1周", "1m": "近1个月", "3m": "近3个月", "6m": "近6个月", "12m": "近12个月"}
_NAME_DATE = re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
_DASH_DATE = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")


def today() -> date:
    return datetime.now(CHINA_TZ).date()


def parse_day(value) -> date | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(CHINA_TZ).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def report_day(item: dict) -> date | None:
    if item.get("date_source") in ("mtime", "文件修改时间", "导入时间"):
        return None
    if item.get("source") == "official" and item.get("date_source") == "文件名":
        path = PurePosixPath(item.get("path", ""))
        if not (_NAME_DATE.search(path.name) or _NAME_DATE.search(path.parent.name) or _DASH_DATE.search(path.name)):
            return None  # Old scan caches mislabelled an mtime fallback.
    return parse_day(item.get("created_at") if item.get("source") == "generated" else item.get("date"))


def date_text(value, path=None, known_dates=None) -> str:
    day = known_dates[path] if known_dates is not None and path in known_dates else parse_day(value)
    return day.isoformat() if day else ''


@dataclass(frozen=True)
class DateFilter:
    period: str = "all"
    since: date | None = None
    until: date | None = None

    @classmethod
    def build(cls, period="all", date_from="", date_to="", *, asof=None):
        current = asof or today()
        if period in ("all", "undated"):
            if date_from or date_to:
                raise ValueError("自定义起止日期请先选择「自定义日期」")
            return cls(period)
        if period == "custom":
            if not date_from and not date_to:
                raise ValueError("请至少选择一个报告日期")
            try:
                since = date.fromisoformat(date_from) if date_from else None
                until = date.fromisoformat(date_to) if date_to else None
            except ValueError as exc:
                raise ValueError("报告日期格式应为 YYYY-MM-DD") from exc
            if since and until and since > until:
                raise ValueError("开始日期不能晚于结束日期")
            return cls(period, since, until)
        if period == "1w":
            return cls(period, current - timedelta(days=6), current)
        if period not in PERIODS:
            raise ValueError("不支持的报告日期范围")
        year, month = divmod(current.year * 12 + current.month - 1 - int(period[:-1]), 12)
        month += 1
        return cls(period, date(year, month, min(current.day, calendar.monthrange(year, month)[1])), current)

    def matches(self, item: dict) -> bool:
        if self.period == "all":
            return True
        value = report_day(item)
        if self.period == "undated":
            return value is None
        return value is not None and (self.since is None or value >= self.since) and (self.until is None or value <= self.until)

    def sql(self, column: str, path_column: str | None = None) -> tuple[str, list]:
        """Column is a trusted code constant; values are always bound parameters."""
        if self.period == "all":
            return "", []
        expression = f"vc_report_day({column}{',' + path_column if path_column else ''})"
        if self.period == "undated":
            return f" AND {expression} IS NULL", []
        clause, args = f" AND {expression} IS NOT NULL", []
        for operator, value in ((">=", self.since), ("<=", self.until)):
            if value is not None:
                clause += f" AND {expression} {operator} ?"
                args.append(value.isoformat())
        return clause, args


def install_sql(conn, report_days=None):
    def normalize(value, path=None):
        return date_text(value, path, report_days) or None
    conn.create_function("vc_report_day", 1, normalize, deterministic=True)
    conn.create_function("vc_report_day", 2, normalize, deterministic=True)
