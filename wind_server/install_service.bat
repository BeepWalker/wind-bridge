@echo off
REM ============================================================
REM  Wind Bridge Server - Windows 服务注册脚本
REM  以管理员身份运行！
REM ============================================================
REM
REM  前提：
REM  1. 已安装 Python (>=3.9)，并在 PATH 中
REM  2. pip install -r requirements.txt
REM  3. Wind 终端已安装并已登录
REM  4. windpy 可正常 import
REM
REM  用法：
REM     install_service.bat           - 安装服务（首次）
REM     install_service.bat start     - 启动服务
REM     install_service.bat stop      - 停止服务
REM     install_service.bat restart   - 重启服务
REM     install_service.bat remove    - 删除服务
REM     install_service.bat status    - 查看状态
REM ============================================================

setlocal enabledelayedexpansion

set SERVICE_NAME=WindBridgeAPI
set DISPLAY_NAME="Wind Bridge API Server"
set DESCRIPTION="生产级Wind数据HTTP网关 - 将WindPy封装为REST API"
set SCRIPT_DIR=%~dp0
set PYTHON_PATH=%SCRIPT_DIR%..\wind_server\wind_api_server.py
set CONFIG_PATH=%SCRIPT_DIR%..\wind_server\config.yaml
set LOG_DIR=%SCRIPT_DIR%..\logs

REM 创建日志目录
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 检测 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 未安装或未加入 PATH
    echo 请先安装 Python 3.9+: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(sys.executable)"') do set PY_EXE=%%i
set PY_DIR=%~dp%PY_EXE%..
echo Python: %PY_EXE%

REM 检测 nssm
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] nssm 未安装，正在下载...
    powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%TEMP%\nssm.zip'"
    powershell -Command "Expand-Archive -Path '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force"
    copy /Y "%TEMP%\nssm\nssm-2.24\win64\nssm.exe" "%WINDIR%\System32\nssm.exe" >nul
    if %errorlevel% neq 0 (
        echo [WARN] 无法自动安装 nssm，请手动下载: https://nssm.cc/download
        echo [INFO] 将使用 Python 直接启动（需要保持窗口打开）
        goto :RUN_DIRECT
    )
    echo [OK] nssm 安装成功
)

REM ------------------------------------------------------------------
REM  命令分发
REM ------------------------------------------------------------------
set CMD=%1
if "%CMD%"=="" set CMD=install

if /I "%CMD%"=="install" goto :INSTALL
if /I "%CMD%"=="start"   goto :START
if /I "%CMD%"=="stop"    goto :STOP
if /I "%CMD%"=="restart" goto :RESTART
if /I "%CMD%"=="remove"  goto :REMOVE
if /I "%CMD%"=="status"  goto :STATUS
echo 未知命令: %CMD%
echo 可用: install, start, stop, restart, remove, status
goto :END

:INSTALL
    echo 正在安装服务 %SERVICE_NAME%...
    nssm install %SERVICE_NAME% "%PY_EXE%"
    nssm set %SERVICE_NAME% AppParameters "\"%PYTHON_PATH%\""
    nssm set %SERVICE_NAME% AppDirectory "%SCRIPT_DIR%..\wind_server"
    nssm set %SERVICE_NAME% AppStdout "%LOG_DIR%\stdout.log"
    nssm set %SERVICE_NAME% AppStderr "%LOG_DIR%\stderr.log"
    nssm set %SERVICE_NAME% AppRotateFiles 1
    nssm set %SERVICE_NAME% AppRotateSeconds 86400
    nssm set %SERVICE_NAME% AppRotateBytes 10485760
    nssm set %SERVICE_NAME% DisplayName %DISPLAY_NAME%
    nssm set %SERVICE_NAME% Description %DESCRIPTION%
    nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
    nssm set %SERVICE_NAME% AppPriority NORMAL_PRIORITY_CLASS
    echo [OK] 服务已注册
    echo.
    echo 启动服务:  %~nx0 start
    echo 删除服务:  %~nx0 remove
    goto :END

:START
    echo 正在启动服务...
    nssm start %SERVICE_NAME%
    goto :END

:STOP
    echo 正在停止服务...
    nssm stop %SERVICE_NAME%
    goto :END

:RESTART
    echo 正在重启服务...
    nssm restart %SERVICE_NAME%
    goto :END

:REMOVE
    echo 正在删除服务...
    nssm stop %SERVICE_NAME% 2>nul
    nssm remove %SERVICE_NAME% confirm
    echo [OK] 服务已删除
    goto :END

:STATUS
    nssm status %SERVICE_NAME%
    goto :END

:RUN_DIRECT
    echo.
    echo ============================================================
    echo  直接启动模式 (Ctrl+C 停止)
    echo ============================================================
    echo  服务地址: http://localhost:8899
    echo  API 文档: http://localhost:8899/docs
    echo  健康检查: http://localhost:8899/api/health
    echo ============================================================
    echo.
    cd /d "%SCRIPT_DIR%..\wind_server"
    python wind_api_server.py
    goto :END

:END
    pause
