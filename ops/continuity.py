"""Compare existing rows across a release without exporting values or fingerprints."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

TABLES = ("users", "sessions", "user_settings", "history", "account_settings", "research_usage", "invite_codes",
          "imported_reports")


def row_hash(row):
    return hashlib.sha256(json.dumps(list(row), ensure_ascii=False, default=str).encode()).hexdigest()


def snapshot(database: Path):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        for table in TABLES:
            if table not in tables:
                continue
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            result[table] = {"columns": columns, "rows": sorted(row_hash(r) for r in conn.execute(f'SELECT * FROM "{table}"'))}
        return result


def verify(database: Path, baseline: dict):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        for table, data in baseline.items():
            if table not in TABLES:
                raise ValueError("连续性基线包含未知表")
            columns = ','.join('"' + c.replace('"', '""') + '"' for c in data['columns'])
            current = {row_hash(r) for r in conn.execute(f'SELECT {columns} FROM "{table}"')}
            if not set(data['rows']).issubset(current):
                raise RuntimeError("既有数据连续性检查失败")


def main():
    action, database, baseline = sys.argv[1:]
    database, baseline = Path(database), Path(baseline)
    if action == "capture":
        with baseline.open("x", encoding="utf-8") as out:
            baseline.chmod(0o600)
            json.dump(snapshot(database), out)
    elif action == "verify":
        verify(database, json.loads(baseline.read_text(encoding="utf-8")))
        print("data_continuity=passed")
    else:
        raise ValueError("未知检查操作")


if __name__ == "__main__":
    main()
