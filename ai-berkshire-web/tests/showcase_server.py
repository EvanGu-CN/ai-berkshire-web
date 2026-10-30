"""Public screenshot fixture. All names, documents and accounts are fictional."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

# This module is never loaded by the normal application or by pytest.
root = Path(os.environ['VC_SHOWCASE_ROOT']).resolve()
assert root.is_dir() and root.name.startswith('showcase-')
assert os.environ.get('VC_SHOWCASE_TOKEN')
os.environ.update({'VC_DATA_DIR': str(root / 'data'), 'VC_FRAMEWORK_DIR': str(root / 'framework'),
                   'VC_PLATFORM_CONFIG': '',
                   'VC_ADMIN_USER': '演示研究员', 'VC_ADMIN_PASS': 'showcase-only-password',
                   'VC_AUTO_DOWNLOAD_FRAMEWORK': '0'})

from server import skills_catalog

framework = root / 'framework'
for directory in ('skills', 'reports', 'tools'):
    (framework / directory).mkdir(parents=True, exist_ok=True)
for name in skills_catalog.SKILL_META:
    (framework / 'skills' / f'{name}.md').write_text(
        f'# 界面演示方法 {name}\n\n仅用于截图，不调用模型。研究对象：$ARGUMENTS。', encoding='utf-8')

from server.app import app
from server import auth, report_imports, settings_store

user_id = next(user['id'] for user in auth.list_users() if user['username'] == '演示研究员')
config = settings_store.list_configs(user_id)['configs'][0]
settings_store.update_config(user_id, config['id'], {'name': '演示模式 · 无需 API Key'})
today = datetime.now(timezone(timedelta(hours=8))).date()


def document(company, topic, age, score, verdict):
    day = today - timedelta(days=age)
    return {'path': f'reports/{company}/{topic}-{day:%Y%m%d}.md', 'metadata': {
        'company': company, 'type': topic, 'date': day.isoformat()}, 'content': f'''# {company} · {topic}

> 界面演示 · 虚构公司与合成资料，用于展示产品功能，不构成投资建议。

## 研究结论

**当前判断：{verdict}。** 先理解商业模式，再核对现金流与估值。研究日期：{day}。

综合评分：{score}/10

## 四个研究视角

| 视角 | 关键问题 | 后续跟踪 |
|---|---|---|
| 段永平 · 商业模式 | 客户为什么持续选择这家公司？ | 续约率与产品价值 |
| 巴菲特 · 财务估值 | 盈利能否转化为自由现金流？ | 回款与资本开支 |
| 芒格 · 行业竞争 | 竞争优势是否能够长期维持？ | 客户转换成本 |
| 李录 · 风险管理 | 什么变化会推翻当前判断？ | 负面信号与管理层承诺 |

## 商业模式与竞争优势

示例企业通过订阅服务解决客户的日常工作需求。分析重点是复购、获客成本与现金回收，而非单看收入增长。

- **收入质量**：区分经常性收入与一次性项目。
- **客户价值**：核对客户留存，避免只看新增订单。
- **资本配置**：优先观察投入回报与现金储备。

## 情景与行动条件

| 情景 | 需要观察的变化 | 对应行动 |
|---|---|---|
| 改善 | 留存稳定、回款持续改善 | 更新估值假设 |
| 基准 | 业务保持稳定，数据仍待补充 | 保持跟踪 |
| 承压 | 核心客户流失、现金回收放缓 | 重新审视研究结论 |

## 风险与待验证事项

> 若现金流与利润长期背离，应重新评估，而不是沿用旧结论。

下一次复盘关注客户集中度、回款周期与投入效率。此处全部内容为合成展示，不引用真实财务数据。
'''}


fixtures = [
    document('示例科技', '财务估值分析', 1, 8, '保持跟踪'),
    document('示例科技', '商业模式分析', 18, 8, '保持跟踪'),
    document('示例科技', '风险与管理层评估', 45, 7, '谨慎观察'),
    document('示例科技', '行业竞争分析', 82, 6, '等待更多信息'),
    document('示例消费', '商业模式分析', 3, 7, '保持跟踪'),
    document('示例消费', '财务估值分析', 27, 7, '谨慎观察'),
    document('示例制造', '行业竞争分析', 8, 6, '等待更多信息'),
    document('示例制造', '风险与管理层评估', 60, 6, '谨慎观察'),
]
result = report_imports.process(user_id, fixtures, save=True)
assert result['counts']['imported'] == len(fixtures)


@app.get('/__showcase__', include_in_schema=False)
def showcase_identity():
    return {'token': os.environ['VC_SHOWCASE_TOKEN'], 'synthetic': True, 'date': today.isoformat()}
