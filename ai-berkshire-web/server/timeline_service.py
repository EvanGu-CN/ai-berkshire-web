"""Bounded timeline pages with a metadata index and a disposable rule-result cache."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from . import auth, memory_store as memory, reports_lib, shared_reports
from . import report_dates as rd

_catalog_lock = threading.Lock()
_catalog_token = None
_catalog_groups: dict[str, tuple[dict, ...]] = {}
_catalog_companies: list[dict] = []
_RULE_VERSION = hashlib.sha256(Path(memory.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest()[:16]
_MAX_RULE_TEXT = 120_000


def _stamp(path: Path) -> tuple:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    except OSError:
        return ()


def _database_token() -> tuple:
    path = auth.DB_PATH
    return str(path.resolve()), _stamp(path), _stamp(path.with_name(path.name + '-wal'))


def _catalog() -> tuple[dict, list[dict], tuple]:
    global _catalog_token, _catalog_groups, _catalog_companies
    token = (reports_lib.index_token(), _database_token())
    with _catalog_lock:
        if token != _catalog_token:
            groups = {}
            for item in shared_reports.all_items():
                grp = (item.get('group') or '未分类').strip() or '未分类'
                groups.setdefault(grp, []).append(item)
            companies = []
            for grp, items in groups.items():
                items.sort(key=lambda item: (bool(item.get('date')), item.get('date') or '',
                                            item.get('title') or '', item['path']), reverse=True)
                dates = [(item.get('date') or '')[:10] for item in items if item.get('date')]
                companies.append({'grp': grp, 'n': len(items), 'score_cnt': None,
                                  'first_date': min(dates) if dates else '',
                                  'last_date': max(dates) if dates else ''})
            _catalog_groups = {grp: tuple(items) for grp, items in groups.items()}
            _catalog_companies = sorted(companies, key=lambda item: (-item['n'], item['grp'].casefold()))
            _catalog_token = (reports_lib.index_token(), _database_token())
        return _catalog_groups, _catalog_companies, _catalog_token


def list_companies(date_filter=None) -> list[dict]:
    # No memory.db lock, schema setup, full-text scan or rule extraction on this path.
    groups, companies, _ = _catalog()
    if not date_filter or date_filter.period == 'all':
        return [dict(item) for item in companies]
    result = []
    for grp, catalog in groups.items():
        items = [item for item in catalog if date_filter.matches(item)]
        if items:
            days = [rd.report_day(item).isoformat() for item in items if rd.report_day(item)]
            result.append({'grp': grp, 'n': len(items), 'score_cnt': None,
                           'first_date': min(days) if days else '', 'last_date': max(days) if days else ''})
    return sorted(result, key=lambda item: (-item['n'], item['grp'].casefold()))


def _saved_conclusions(paths: list[str]) -> tuple[dict, bool]:
    if not paths or not memory.DB_PATH.is_file():
        return {}, False
    try:
        with closing(sqlite3.connect(memory.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as conn:
            conn.row_factory = sqlite3.Row
            marks = ','.join('?' for _ in paths)
            rows = conn.execute(f"SELECT * FROM conclusions WHERE source='llm' AND path IN ({marks})", paths)
            return {row['path']: dict(row) for row in rows}, False
    except sqlite3.Error:
        return {}, True


def _cache_file() -> Path:
    return memory.DATA_DIR / 'timeline_cache.db'


def _cached_rules(paths: list[str]) -> dict:
    path = _cache_file()
    if not paths or not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)) as conn:
            marks = ','.join('?' for _ in paths)
            return {row[0]: (row[1], json.loads(row[2])) for row in conn.execute(
                f'SELECT path,revision,payload FROM rule_cache WHERE path IN ({marks})', paths)}
    except (sqlite3.Error, ValueError):
        return {}


def _store_rules(rows: list[tuple]) -> None:
    if not rows:
        return
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=.1)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE IF NOT EXISTS rule_cache(path TEXT PRIMARY KEY,revision TEXT,payload TEXT)')
            conn.executemany('INSERT OR REPLACE INTO rule_cache VALUES (?,?,?)', rows)
            conn.commit()
    except (sqlite3.Error, OSError):
        pass  # Disposable cache availability must not block report reading.


def _revision(item: dict, catalog_token: tuple) -> str:
    if item.get('source') == 'official':
        try:
            version = _stamp(reports_lib._norm_path(item['path']))
        except ValueError:
            version = ()
    elif item.get('source') == 'generated':
        version = catalog_token[1]
    else:
        version = item['path']  # Imported report IDs are immutable content versions.
    return hashlib.sha256(json.dumps((_RULE_VERSION, version, item.get('title'),
                                     item.get('date'), item.get('group')), ensure_ascii=False).encode()).hexdigest()


def get_timeline(grp: str, limit: int = 20, offset: int = 0, date_filter=None) -> dict:
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError('无效的时间线分页参数')
    groups, _, token = _catalog()
    catalog = groups.get(grp, ())
    if date_filter and date_filter.period != 'all':
        catalog = [dict(item, date=rd.report_day(item).isoformat() if rd.report_day(item) else '')
                   for item in catalog if date_filter.matches(item)]
    selected = catalog[offset:offset + limit]
    paths = [item['path'] for item in selected]
    saved, memory_busy = _saved_conclusions(paths)
    cached = _cached_rules(paths)
    items, updates = [], []
    for item in selected:
        path = item['path']
        conclusion = saved.get(path)
        title = item.get('title') or path
        revision = _revision(item, token)
        if conclusion is None:
            if path in cached and cached[path][0] == revision:
                conclusion = cached[path][1]
                title = conclusion.get('title') or title
            else:
                doc = shared_reports.get_report(path)
                if doc:
                    title, text = doc['title'], doc['content']
                    shortened = len(text) > _MAX_RULE_TEXT
                    if shortened:
                        text = text[:80_000] + '\n\n' + text[-40_000:]
                    extracted = memory.extract_conclusion_rule(title, text)
                    conclusion = dict(extracted or {}, title=title, source='rule' if extracted else 'none',
                                      limited_text=shortened)
                    updates.append((path, revision, json.dumps(conclusion, ensure_ascii=False)))
                else:
                    conclusion = {'source': 'unavailable', 'summary': '报告暂不可读取，可刷新来源后重试。'}
        items.append({'path': path, 'title': title, 'date': (item.get('date') or '')[:10],
                      'verdict': conclusion.get('verdict') or '', 'score': conclusion.get('score'),
                      'score_max': conclusion.get('score_max'), 'price_range': conclusion.get('price_range') or '',
                      'summary': conclusion.get('summary') or '', 'source': conclusion.get('source', 'rule'),
                      'report_source': item.get('source', 'official'),
                      'limited_text': conclusion.get('limited_text', False)})
    _store_rules(updates)
    items.sort(key=lambda item: (bool(item['date']), item['date'], item['title'], item['path']))
    dated = [item for item in items if item['date']]
    scored = [item for item in dated if item['score'] is not None]
    scores = [(item['date'], item['score']) for item in scored]
    verdicts = [item for item in dated if item['verdict']]
    drifts = [{'date': current['date'], 'from': previous['verdict'], 'to': current['verdict'], 'path': current['path']}
              for previous, current in zip(verdicts, verdicts[1:]) if previous['verdict'] != current['verdict']]
    stats = {'scope': 'page', 'report_count': len(items), 'score_count': sum(item['score'] is not None for item in items),
             'undated_count': len(items) - len(dated), 'first_date': dated[0]['date'] if dated else '',
             'last_date': dated[-1]['date'] if dated else '', 'score_first': scores[0][1] if scores else None,
             'score_latest': scores[-1][1] if scores else None, 'score_first_max': scored[0]['score_max'] if scored else None,
             'score_latest_max': scored[-1]['score_max'] if scored else None,
             'score_scales': sorted({item['score_max'] for item in scored if item['score_max'] is not None}),
             'score_min': min(value for _, value in scores) if scores else None,
             'score_max': max(value for _, value in scores) if scores else None,
             'verdict_latest': verdicts[-1]['verdict'] if verdicts else '', 'drift_count': len(drifts)}
    return {'grp': grp, 'items': items, 'scores': scores, 'drifts': drifts, 'stats': stats,
            'total': len(catalog), 'limit': limit, 'offset': offset, 'has_next': offset + limit < len(catalog),
            'conclusion_index_busy': memory_busy}
