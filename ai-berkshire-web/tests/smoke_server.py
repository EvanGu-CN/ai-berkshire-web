"""Isolated visual-test server; no real credentials, data, or model requests."""
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta, timezone

os.environ['VC_DATA_DIR'] = tempfile.mkdtemp(prefix='vc-visual-data-')
os.environ['VC_ADMIN_PASS'] = os.environ.get('VC_SMOKE_PASSWORD', 'visual-test-password')
os.environ['VC_ADMIN_USER'] = 'admin'
from server import auth, settings_store, skills_catalog, reports_lib
from server.app import app

# Keep report catalog and timeline tests synthetic as well as accounts/models.
framework = Path(tempfile.mkdtemp(prefix='vc-visual-framework-'))
reports = framework / 'reports' / '时间线验收公司'
reports.mkdir(parents=True)
for day, score, verdict in [('20261001', 8, '通过'), ('20261015', 6, '建议观望')]:
    (reports / (day + '.md')).write_text(
        f'# 时间线合成研究 {day}\n\n评分：{score}/10\n\n最终结论：{verdict}。合成资料用于界面验收。', encoding='utf-8')
bulk = framework / 'reports' / '分页验收专题'
bulk.mkdir()
for index in range(45):
    (bulk / f'20261001-{index:03}.md').write_text(f'# 分页报告 {index:03}\n\n评分：8/10\n\n合成资料，用于分页验收。', encoding='utf-8')
for index in range(35):
    directory = framework / 'reports' / f'公司检索{index:03}'
    directory.mkdir()
    (directory / '20261001.md').write_text(f'# 公司检索 {index:03}\n合成资料。', encoding='utf-8')
reports_lib.AI_BERKSHIRE = framework
reports_lib.REPORTS_DIR = framework / 'reports'
reports_lib.REPORTS_INDEX = framework / 'reports/index.json'

# Recent-report discovery uses actual report dates, including an old and an
# undated file whose filesystem timestamps are both fresh.
recent = framework / 'reports' / '报告复用验收公司'
recent.mkdir()
today = datetime.now(timezone(timedelta(hours=8))).date()
for index, age in enumerate([1] * 7 + [90, 250, -2]):
    day = today - timedelta(days=age)
    (recent / f'{day:%Y%m%d}-{index}.md').write_text(
        f'# 报告复用验收公司 · 合成研究 {index}\n\n报告日期：{day}。仅用于已有报告筛选验收。', encoding='utf-8')
(recent / 'unknown.md').write_text('# 报告复用验收公司 · 日期未知\n\n无报告日期，不计入近期。', encoding='utf-8')

skills = Path(tempfile.mkdtemp(prefix='vc-visual-skills-'))
for name in skills_catalog.SKILL_META:
    (skills / f'{name}.md').write_text(f'# 测试研究方法 {name}\n测试资料，仅用于界面回归。研究 $ARGUMENTS。', encoding='utf-8')
skills_catalog.SKILLS_DIR = skills
for username in ('演示研究员','测试VIP','测试管理员'):
    auth.register(username,'visual-test-password')
users = {u['username']:u for u in auth.list_users()}
auth.update_user(users['测试VIP']['id'],tier='vip')
auth.update_user(users['测试管理员']['id'],is_admin=True)
root_id=users[auth.ADMIN_USERNAME]['id']
for uid in (root_id, users['演示研究员']['id']):
    cfg=settings_store.list_configs(uid)['configs'][0]
    settings_store.update_config(uid,cfg['id'],{'provider':'demo','name':'界面测试演示模式'})
