@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先使用带依赖的解释器，否则退回当前 python（run.py 会自动装依赖）
set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" (
    where py >nul 2>nul && set PY=py -3
)
if "%PY%"=="" (
    echo [AI Berkshire Web] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo Starting AI Berkshire Web at http://127.0.0.1:8765 ...
%PY% run.py
pause
