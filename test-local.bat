@echo off
setlocal

REM Full local stack launcher (Windows): moto, mcp-fs, config-a2a, web-a2a.
REM Usage:  test-local.bat [test_config.yaml]      (run from the mcp-fs repo root)
REM Reads the test config, prepares keys / config / launch scripts, then starts
REM each service in its own window in the right order. Close the windows to stop.

set "CONFIG=%~1"
if "%CONFIG%"=="" set "CONFIG=test_config.yaml"
if not exist "%CONFIG%" (
  echo Test config not found: %CONFIG%
  echo Copy test_config.example.yaml to test_config.yaml and fill it in.
  exit /b 1
)

echo [1/6] Preparing keys, config, and launch scripts...
uv run python scripts\test_local_prepare.py "%CONFIG%"
if errorlevel 1 exit /b 1
call state\test-local.vars.bat

if "%START_MOTO%"=="1" (
  echo [2/6] Starting moto server...
  start "moto" cmd /k call state\run-moto.bat
  timeout /t 4 /nobreak >nul
) else (
  echo [2/6] Using external S3 endpoint; not starting moto.
)

echo [3/6] Starting mcp-fs...
start "mcp-fs" cmd /k call state\run-mcp-fs.bat
timeout /t 10 /nobreak >nul

echo [4/6] Provisioning project %MCP_FS_MOUNT% (owner %ADMIN_EMAIL%)...
set "MCP_FS_ADMIN=%ADMIN_EMAIL%"
uv run python scripts\provision.py %MCP_FS_MOUNT% %ADMIN_EMAIL%

echo [5/6] Starting config-a2a agent...
start "config-a2a" cmd /k call state\run-config-a2a.bat
timeout /t 6 /nobreak >nul

echo [6/6] Starting web-a2a UI...
start "web-a2a" cmd /k call state\run-web-a2a.bat

echo.
echo Services launching in separate windows.
echo When web-a2a is up, open http://localhost:8000
echo   login as %ADMIN_EMAIL%
echo   add a remote agent by URL: http://127.0.0.1:9100/agents/files  (auth: none)
echo   then chat, for example: list my files
echo.
echo Close the four windows to stop the services.
endlocal
