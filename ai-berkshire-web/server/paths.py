# -*- coding: utf-8 -*-
"""路径常量：定位 ai-berkshire 框架与本应用的数据目录。"""
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = APP_ROOT.parent
AI_BERKSHIRE = Path(os.environ.get("VC_FRAMEWORK_DIR", str(PROJECT_ROOT / "ai-berkshire"))).expanduser().resolve()

SKILLS_DIR = AI_BERKSHIRE / "skills"
REPORTS_DIR = AI_BERKSHIRE / "reports"
REPORTS_INDEX = REPORTS_DIR / "index.json"
FINANCIAL_RIGOR = AI_BERKSHIRE / "tools" / "financial_rigor.py"

DATA_DIR = Path(os.environ.get("VC_DATA_DIR", str(APP_ROOT / "data"))).expanduser().resolve()
HISTORY_DIR = DATA_DIR / "history"
SETTINGS_FILE = DATA_DIR / "settings.json"
