"""Consistent SQLite backups for moving accounts, model settings and reports."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile

from . import auth
from .paths import DATA_DIR

APP_DB = DATA_DIR / 'app.db'
DB_NAME = 'app.db'
MANIFEST = 'manifest.json'


def _snapshot(source: Path, dest: Path):
    src = sqlite3.connect(str(source))
    target = sqlite3.connect(str(dest))
    try:
        src.backup(target)
    finally:
        target.close()
        src.close()


def export_data(dest: Path | str) -> dict:
    """SQLite backup API includes committed WAL data without copying live files."""
    dest = Path(dest)
    if not APP_DB.is_file():
        return {'ok':False, 'message':'没有可导出的数据'}
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=APP_DB.parent) as temp_dir:
        snapshot = Path(temp_dir) / DB_NAME
        with auth._lock:
            _snapshot(APP_DB, snapshot)
        counts = _db_counts(snapshot)
        manifest = {'app':'AI Berkshire Web','version':'2.1','exported_at':datetime.now().astimezone().isoformat(),
                    'counts':counts,'note':'含账号、密码哈希和模型密钥，请妥善保管'}
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot,DB_NAME)
            zf.writestr(MANIFEST,json.dumps(manifest,ensure_ascii=False))
    return {'ok':True,'path':str(dest),'size':dest.stat().st_size,'counts':counts}


def import_data(src: Path | str) -> dict:
    """Validate off-line, reject running tasks, back up, replace, then migrate."""
    try:
        with zipfile.ZipFile(src) as zf:
            info=zf.getinfo(DB_NAME)
            if info.file_size > 1024 * 1024 * 1024:
                return {'ok':False,'message':'备份数据库超过 1 GB，请使用离线迁移'}
            payload=zf.read(DB_NAME)
    except (OSError,zipfile.BadZipFile,KeyError):
        return {'ok':False,'message':'请选择包含 app.db 的有效备份文件'}
    if not payload.startswith(b'SQLite format 3'):
        return {'ok':False,'message':'备份中的数据库无效'}
    APP_DB.parent.mkdir(parents=True,exist_ok=True)
    backup_path=''
    with tempfile.TemporaryDirectory(dir=APP_DB.parent) as temp_dir:
        incoming=Path(temp_dir)/DB_NAME
        incoming.write_bytes(payload)
        try:
            check=sqlite3.connect(str(incoming))
            try:
                if check.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                    return {'ok':False,'message':'备份数据库校验未通过'}
                tables={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'users','history','user_settings','sessions'}.issubset(tables):
                    return {'ok':False,'message':'备份缺少必需的数据表'}
                check.execute('DELETE FROM sessions')
                check.commit()
            finally:
                check.close()
        except sqlite3.DatabaseError:
            return {'ok':False,'message':'备份数据库校验未通过'}
        with auth._lock:
            if APP_DB.is_file():
                conn=sqlite3.connect(str(APP_DB))
                try:
                    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'research_usage' in tables and conn.execute("SELECT 1 FROM research_usage WHERE status='running' LIMIT 1").fetchone():
                        return {'ok':False,'message':'有研究任务正在进行，请结束任务后再导入'}
                    conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                finally:
                    conn.close()
                backup=APP_DB.with_name('app.db.bak-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
                _snapshot(APP_DB,backup)
                backup_path=str(backup)
            for suffix in ('-wal','-shm'):
                APP_DB.with_name(APP_DB.name+suffix).unlink(missing_ok=True)
            os.replace(incoming,APP_DB)
        auth.init_db()
        from .accounts import recover_interrupted
        recover_interrupted()
    return {'ok':True,'counts':_db_counts(),'backup_path':backup_path,'requires_login':True}


def _db_counts(path=None) -> dict:
    source=Path(path or APP_DB)
    if not source.is_file():
        return {}
    conn=sqlite3.connect(str(source))
    try:
        result={}
        for table in ('users','history','user_settings','sessions','imported_reports'):
            try:
                result[table]=conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except sqlite3.DatabaseError:
                result[table]=-1
        return result
    finally:
        conn.close()
